"""
AGPM Core - Self-contained account management with its own local database.

This module manages its own SQLite database at AGPM/data/agpm.db,
independent of the main AGPM application.
"""

import os
import sys
import json
import sqlite3
import time
import secrets

import webbrowser
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# --- Paths ---

AGPM_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(AGPM_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'agpm.db')
CONFIG_PATH = os.path.join(DATA_DIR, 'config.json')
KEY_PATH = os.path.join(DATA_DIR, '.mk')

# --- API Constants ---
# Hardcoded for direct use without .env dependency
CLIENT_ID = '1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com'
CLIENT_SECRET = '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'
OAUTH_PORT = 5005
USER_AGENT = 'antigravity/1.11.3 Linux/x86_64'
URL_TOKEN = 'https://oauth2.googleapis.com/token'
URL_QUOTA = 'https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels'
URL_LOAD_PROJECT = 'https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist'
URL_USERINFO = 'https://www.googleapis.com/oauth2/v3/userinfo'


# --- Encryption (local key, no OS keyring dependency) ---

def _get_or_create_master_key() -> bytes:
    """Get or create a local 32-byte AES key stored in data/.mk as hex."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, 'r') as f:
            return bytes.fromhex(f.read().strip())
    key = secrets.token_bytes(32)
    with open(KEY_PATH, 'w') as f:
        f.write(key.hex())
    os.chmod(KEY_PATH, 0o600)
    return key


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string with AES-256-GCM. Returns iv_hex:tag_hex:ciphertext_hex."""
    key = _get_or_create_master_key()
    iv = secrets.token_bytes(16)
    aesgcm = AESGCM(key)
    combined = aesgcm.encrypt(iv, plaintext.encode('utf-8'), None)
    # combined = ciphertext + tag (last 16 bytes)
    tag = combined[-16:]
    ciphertext = combined[:-16]
    return f"{iv.hex()}:{tag.hex()}:{ciphertext.hex()}"


def decrypt_value(encrypted: str) -> str | None:
    """Decrypt an iv:tag:ciphertext hex string. Returns None on failure."""
    if not encrypted or encrypted.startswith('{') or encrypted.startswith('['):
        return encrypted  # Already plaintext JSON
    parts = encrypted.split(':')
    if len(parts) != 3:
        return None
    try:
        key = _get_or_create_master_key()
        iv = bytes.fromhex(parts[0])
        tag = bytes.fromhex(parts[1])
        ciphertext = bytes.fromhex(parts[2])
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(iv, ciphertext + tag, None).decode('utf-8')
    except Exception:
        return None


# --- Database Setup ---

def _init_db():
    """Create the AGPM database and accounts table if they don't exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL DEFAULT 'google',
            email TEXT NOT NULL UNIQUE,
            name TEXT DEFAULT '',
            refresh_token TEXT NOT NULL,
            access_token TEXT DEFAULT '',
            token_expiry INTEGER DEFAULT 0,
            quota_json TEXT DEFAULT '{}',
            proxy_url TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            is_active INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            last_used INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _get_conn():
    """Get a connection to the AGPM database, initializing if needed."""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# --- Account CRUD ---

def get_accounts() -> list[dict]:
    """Get all accounts from the local AGPM database."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM accounts ORDER BY last_used DESC")
    rows = cursor.fetchall()
    conn.close()

    accounts = []
    for row in rows:
        acc = dict(row)
        # Decrypt quota_json
        qj = decrypt_value(acc.get('quota_json', ''))
        if qj:
            try:
                acc['quota'] = json.loads(qj)
            except Exception:
                acc['quota'] = {}
        else:
            acc['quota'] = {}
        # Decrypt refresh_token for internal use
        acc['_refresh_token'] = decrypt_value(acc.get('refresh_token', ''))
        accounts.append(acc)

    return accounts


def add_account(email: str, refresh_token: str, provider: str = 'google', name: str = '', proxy_url: str = '', project_id: str = '', status: str = 'active') -> bool:
    """Add a new account to the local database."""
    conn = _get_conn()
    cursor = conn.cursor()

    # Check for duplicate
    cursor.execute("SELECT email FROM accounts WHERE email = ?", (email,))
    if cursor.fetchone():
        conn.close()
        return False  # Already exists

    account_id = secrets.token_hex(16)
    now = int(time.time() * 1000)
    encrypted_token = encrypt_value(refresh_token)
    
    quota_dict = {'models': {}}
    if project_id:
        quota_dict['project_id'] = project_id
    encrypted_quota = encrypt_value(json.dumps(quota_dict))

    cursor.execute(
        """INSERT INTO accounts
           (id, provider, email, name, refresh_token, proxy_url, status, is_active, created_at, last_used, quota_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
        (account_id, provider, email, name, encrypted_token, proxy_url, status, now, now, encrypted_quota),
    )
    conn.commit()
    conn.close()
    return True


def remove_account(email: str) -> bool:
    """Remove an account by email."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM accounts WHERE email = ?", (email,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def set_active_account(email: str) -> bool:
    """Set an account as active (deactivates all others)."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE accounts SET is_active = 0")
    cursor.execute("UPDATE accounts SET is_active = 1 WHERE email = ?", (email,))
    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed


# --- Token Refresh & Quota Fetch ---

def refresh_access_token(refresh_token: str) -> dict | None:
    """Synchronously refresh an access token via Google OAuth."""
    try:
        kwargs = get_httpx_kwargs()
        kwargs['timeout'] = 30.0
        resp = httpx.post(
            URL_TOKEN,
            data={
                'client_id': CLIENT_ID,
                'client_secret': CLIENT_SECRET,
                'refresh_token': refresh_token,
                'grant_type': 'refresh_token',
            },
            **kwargs,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def fetch_live_quota(access_token: str) -> dict:
    """Fetch live quota data from Google API."""
    try:
        headers = {
            'Authorization': f'Bearer {access_token}',
            'User-Agent': USER_AGENT,
            'Content-Type': 'application/json',
        }

        # Get project ID first
        project_id = None
        kwargs = get_httpx_kwargs()
        kwargs['timeout'] = 30.0
        try:
            resp = httpx.post(
                URL_LOAD_PROJECT,
                json={'metadata': {'ideType': 'ANTIGRAVITY'}},
                headers=headers,
                **kwargs,
            )
            if resp.is_success:
                project_id = resp.json().get('cloudaicompanionProject')
        except Exception:
            pass

        # Fetch quota
        payload = {}
        if project_id:
            payload['project'] = project_id

        resp = httpx.post(URL_QUOTA, json=payload, headers=headers, **kwargs)
        resp.raise_for_status()

        raw = resp.json()
        result = {'models': {}, 'project_id': project_id or ''}
        for name, info in raw.get('models', {}).items():
            q_info = info.get('quotaInfo')
            if q_info:
                fraction = q_info.get('remainingFraction', 0)
                result['models'][name] = {
                    'percentage': int(fraction * 100),
                    'resetTime': q_info.get('resetTime', ''),
                }
        return result
    except Exception:
        return {'models': {}, 'project_id': ''}


def refresh_account_quota(email: str) -> str:
    """Refresh quota for a specific account. Returns status message."""
    accounts = get_accounts()
    target = next((a for a in accounts if a['email'] == email), None)
    if not target:
        return f"Account {email} not found"

    rt = target.get('_refresh_token')
    if not rt:
        return f"No refresh token for {email}"

    # 1. Refresh access token
    tokens = refresh_access_token(rt)
    if not tokens or 'access_token' not in tokens:
        return f"Failed to refresh token for {email}"

    access_token = tokens['access_token']
    expiry = int(time.time() * 1000) + (tokens.get('expires_in', 3600) * 1000)

    # 2. Fetch quota
    quota = fetch_live_quota(access_token)

    # 3. Save to DB
    encrypted_quota = encrypt_value(json.dumps(quota))
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE accounts SET access_token = ?, token_expiry = ?, quota_json = ?, last_used = ? WHERE email = ?",
        (access_token, expiry, encrypted_quota, int(time.time() * 1000), email),
    )
    conn.commit()
    conn.close()

    model_count = len(quota.get('models', {}))
    return f"OK: {model_count} models updated for {email}"


def fetch_user_info(access_token: str) -> dict | None:
    """Fetch Google user info (email, name) from an access token."""
    try:
        kwargs = get_httpx_kwargs()
        kwargs['timeout'] = 15.0
        resp = httpx.get(
            URL_USERINFO,
            headers={'Authorization': f'Bearer {access_token}'},
            **kwargs,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def fetch_project_id(access_token: str) -> str:
    """Fetch the project ID for the account."""
    try:
        kwargs = get_httpx_kwargs()
        kwargs['timeout'] = 30.0
        resp = httpx.post(
            URL_LOAD_PROJECT,
            json={'metadata': {'ideType': 'ANTIGRAVITY'}},
            headers={
                'Authorization': f'Bearer {access_token}',
                'User-Agent': USER_AGENT,
                'Content-Type': 'application/json',
            },
            **kwargs,
        )
        if resp.is_success:
            return resp.json().get('cloudaicompanionProject', '')
    except Exception:
        pass
    return ''


def validate_refresh_token(refresh_token: str) -> dict | None:
    """Validate a refresh token and return user info if valid."""
    tokens = refresh_access_token(refresh_token)
    if not tokens or 'access_token' not in tokens:
        return None
    user_info = fetch_user_info(tokens['access_token'])
    if user_info:
        user_info['access_token'] = tokens['access_token']
        user_info['expires_in'] = tokens.get('expires_in', 3600)
    return user_info


# --- Config ---

def load_config() -> dict:
    """Load config.json from AGPM/data/."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_config(config_data: dict) -> bool:
    """Save config.json to AGPM/data/."""
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2)
        return True
    except Exception:
        return False


def get_proxy_config() -> dict:
    config = load_config()
    proxy = config.get('proxy', {})
    upstream = proxy.get('upstream_proxy', {})
    return {
        'enabled': proxy.get('enabled', False),
        'port': proxy.get('port', 8050),
        'auto_start': proxy.get('auto_start', True),
        'upstream_enabled': upstream.get('enabled', False),
        'upstream_url': upstream.get('url', ''),
    }


def get_httpx_kwargs() -> dict:
    """Return common httpx kwargs like proxy settings."""
    config = get_proxy_config()
    kwargs = {}
    if config.get('upstream_enabled') and config.get('upstream_url'):
        kwargs['proxy'] = config['upstream_url']
    return kwargs


def save_proxy_config(enabled, port, auto_start, upstream_enabled, upstream_url) -> bool:
    config = load_config()
    if 'proxy' not in config:
        config['proxy'] = {}
    config['proxy']['enabled'] = enabled
    config['proxy']['port'] = port
    config['proxy']['auto_start'] = auto_start
    if 'upstream_proxy' not in config['proxy']:
        config['proxy']['upstream_proxy'] = {}
    config['proxy']['upstream_proxy']['enabled'] = upstream_enabled
    config['proxy']['upstream_proxy']['url'] = upstream_url
    return save_config(config)


def is_proxy_configured() -> bool:
    if not os.path.exists(CONFIG_PATH):
        return False
    config = load_config()
    proxy = config.get('proxy', {})
    return bool(proxy.get('enabled') or proxy.get('upstream_proxy', {}).get('url'))


# --- Admin Auth & Portal Config ---

def get_admin_creds() -> tuple[str, str]:
    """Get admin username and password from config, default to admin/admin."""
    config = load_config()
    auth = config.get('auth', {})
    username = auth.get('username', 'admin')
    password = auth.get('password', 'admin')
    return username, password


def save_admin_creds(username, password) -> bool:
    """Save admin username and password to config."""
    config = load_config()
    if 'auth' not in config:
        config['auth'] = {}
    config['auth']['username'] = username
    config['auth']['password'] = password
    return save_config(config)


def get_portal_config() -> dict:
    """Get portal configuration (port, etc.)."""
    config = load_config()
    portal = config.get('portal', {})
    
    # Generate a persistent secret key if it doesn't exist
    if 'secret_key' not in portal:
        portal['secret_key'] = secrets.token_hex(32)
        config['portal'] = portal
        save_config(config)
        
    return {
        'port': portal.get('port', 5000),
        'admin_slug': portal.get('admin_slug', 'admin'),
        'secret_key': portal['secret_key']
    }


def save_portal_config(port, admin_slug='admin') -> bool:
    """Save portal configuration."""
    config = load_config()
    if 'portal' not in config:
        config['portal'] = {}
    config['portal']['port'] = port
    config['portal']['admin_slug'] = admin_slug
    return save_config(config)


# --- Models ---

KNOWN_MODELS = [
    ("gemini-2.5-pro", "Google"),
    ("gemini-2.5-flash", "Google"),
    ("gemini-2.0-flash", "Google"),
    ("gemini-2.0-flash-lite", "Google"),
    ("gemini-1.5-pro", "Google"),
    ("gemini-1.5-flash", "Google"),
    ("claude-3.5-sonnet", "Anthropic"),
    ("claude-3.5-haiku", "Anthropic"),
    ("claude-3-opus", "Anthropic"),
    ("claude-sonnet-4", "Anthropic"),
    ("claude-opus-4", "Anthropic"),
]


def get_all_models() -> list[tuple[str, str]]:
    """Get models from account quotas, falling back to known list."""
    accounts = get_accounts()
    models = set()
    for acc in accounts:
        quota = acc.get('quota', {}).get('models', {})
        for model_name in quota.keys():
            models.add(model_name)

    if models:
        result = []
        for m in sorted(models):
            display = m.replace('cloudaicompanion.googleapis.com/', '')
            provider = "Google" if "gemini" in m.lower() else "Anthropic" if "claude" in m.lower() else "Other"
            result.append((display, provider))
        return result

    return KNOWN_MODELS


# --- OAuth Flow with Local Callback Server ---

URL_AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
OAUTH_SCOPES = [
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/cloud-platform',
]


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that catches the OAuth redirect and extracts the auth code."""

    auth_code: str | None = None
    error: str | None = None
    result: dict | None = None
    auto_save: bool = False

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if 'error' in params:
            _OAuthCallbackHandler.error = params['error'][0]
            self._send_status_page(False, f"OAuth Error: {params['error'][0]}")
            return

        if 'code' not in params:
            self._send_status_page(False, "No authorization code received")
            return
            
        code = params['code'][0]
        _OAuthCallbackHandler.auth_code = code
        redirect_uri = f'http://127.0.0.1:{self.server.server_port}'
        
        # Synchronous token exchange and testing
        tokens = _exchange_code_for_tokens(code, redirect_uri)
        if not tokens or 'refresh_token' not in tokens:
            self._send_status_page(False, "Failed to exchange code for tokens.")
            return
            
        refresh_token = tokens['refresh_token']
        access_token = tokens.get('access_token', '')
        
        # Fetch user info
        email = ''
        name = ''
        project_id = ''
        if access_token:
            user_info = fetch_user_info(access_token)
            if user_info:
                email = user_info.get('email', '')
                name = user_info.get('name', '')
            project_id = fetch_project_id(access_token)
                
        if not email:
            self._send_status_page(False, "Failed to retrieve user email.")
            return
            
        # Test Gemini Connection
        test_success, test_msg = _test_gemini_connection(access_token, project_id)
        
        status_val = 'active' if test_success else 'rejected'
        
        # Add account if auto_save is enabled
        added = False
        if _OAuthCallbackHandler.auto_save:
            added = add_account(email, refresh_token, name=name, project_id=project_id, status=status_val)
            
        _OAuthCallbackHandler.result = {
            'success': True,
            'email': email,
            'name': name,
            'refresh_token': refresh_token,
            'access_token': access_token,
            'added': added,
            'test_success': test_success,
            'test_msg': test_msg
        }
        
        if test_success:
            self._send_status_page(True, "Connection successful! You can close this tab and return to the portal.")
        else:
            self._send_status_page(False, f"Account Verification Failed, Try to add Mobile Number in Google Account and retry")

    def _send_status_page(self, success: bool, message: str):
        title = "Verification Successful" if success else "Verification Failed"
        lucide_icon = "check-circle" if success else "alert-circle"
        status_class = "success" if success else "error"
        
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AGPM - {title}</title>
    <link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;700&family=Ubuntu:wght@400;500;700&display=swap" rel="stylesheet">
    <script src="https://unpkg.com/lucide@latest"></script>
    <style>
        :root {{
            --color-primary: #072C2C;
            --color-secondary: #FF5F03;
            --color-surface: #EDEADE;
            --color-surface-raised: #FFFFFF;
            --color-text: #111827;
            --color-text-secondary: #4B5563;
            --color-success: #16A34A;
            --color-danger: #DC2626;
            --font-primary: 'Ubuntu', sans-serif;
            --font-display: 'Oswald', sans-serif;
        }}
        
        body {{
            margin: 0;
            padding: 0;
            font-family: var(--font-primary);
            background-color: var(--color-surface);
            color: var(--color-text);
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
        }}
        
        .card {{
            background: var(--color-surface-raised);
            padding: 3rem;
            border-radius: 1.25rem;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
            max-width: 480px;
            width: 90%;
            text-align: center;
            border: 1px solid rgba(0,0,0,0.05);
            animation: fadeIn 0.5s cubic-bezier(0.4, 0, 0.2, 1);
        }}
        
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(20px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
        .status-icon {{
            width: 80px;
            height: 80px;
            margin: 0 auto 1.5rem;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 50%;
        }}
        
        .status-icon.success {{
            background-color: rgba(22, 163, 74, 0.1);
            color: var(--color-success);
        }}
        
        .status-icon.error {{
            background-color: rgba(220, 38, 38, 0.1);
            color: var(--color-danger);
        }}
        
        h1 {{
            font-family: var(--font-display);
            font-size: 2.25rem;
            text-transform: uppercase;
            margin: 0 0 1rem;
            letter-spacing: 0.5px;
            color: var(--color-primary);
        }}
        
        p {{
            font-size: 1.125rem;
            line-height: 1.6;
            color: var(--color-text-secondary);
            margin-bottom: 2.5rem;
        }}
        
        .btn {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.75rem;
            background: var(--color-primary);
            color: white;
            padding: 1rem 2.5rem;
            border-radius: 0.5rem;
            text-decoration: none;
            font-weight: 600;
            transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
            border: none;
            cursor: pointer;
            font-family: var(--font-primary);
            width: 100%;
            font-size: 1rem;
        }}
        
        .btn:hover {{
            background: #0E4A4A;
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(7, 44, 44, 0.2);
        }}
        
        .btn:active {{
            transform: translateY(0);
        }}
        
        .brand {{
            margin-bottom: 2.5rem;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.75rem;
        }}
        
        .logo-mark {{
            width: 40px;
            height: 40px;
            background: var(--color-secondary);
            border-radius: 0.75rem;
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: var(--font-display);
            font-weight: bold;
            font-size: 1.5rem;
            box-shadow: 0 4px 12px rgba(255, 95, 3, 0.3);
        }}
        
        .brand-name {{
            font-family: var(--font-display);
            font-weight: bold;
            font-size: 1.5rem;
            letter-spacing: 1.5px;
            color: var(--color-primary);
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="brand">
            <div class="logo-mark">A</div>
            <div class="brand-name">AGPM</div>
        </div>
        
        <div class="status-icon {status_class}">
            <i data-lucide="{lucide_icon}" style="width: 48px; height: 48px;"></i>
        </div>
        
        <h1>{title}</h1>
        <p>{message}</p>
        
        <button class="btn" onclick="window.close()">
            <span>Return to Console</span>
            <i data-lucide="arrow-right" style="width: 20px; height: 20px;"></i>
        </button>
    </div>
    <script>
        lucide.createIcons();
        // Fallback for button if window.close() fails
        document.querySelector('.btn').addEventListener('click', function() {{
            setTimeout(function() {{
                if (!window.closed) {{
                    alert("Verification complete. You can now close this tab manually.");
                }}
            }}, 500);
        }});
    </script>
</body>
</html>"""
        self.send_response(200 if success else 400)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        pass  # Suppress HTTP server logs

def _test_gemini_connection(access_token: str, project_id: str) -> tuple[bool, str]:
    """Test the Gemini connection by sending a basic request using the internal API format."""
    import uuid as _uuid
    url = 'https://cloudcode-pa.googleapis.com/v1internal:generateContent'
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'User-Agent': 'antigravity/1.11.3 Linux/x86_64'
    }

    # Use the same internal request format that proxy.py uses
    internal_body = {
        'requestId': str(_uuid.uuid4()),
        'request': {
            'contents': [{'role': 'user', 'parts': [{'text': 'hello'}]}],
            'generationConfig': {},
        },
        'model': 'gemini-3-flash',
        'userAgent': 'antigravity/1.11.3 Linux/x86_64',
        'requestType': 'generate-content',
    }
    if project_id:
        internal_body['project'] = project_id

    try:
        kwargs = get_httpx_kwargs()
        kwargs['timeout'] = 15.0
        resp = httpx.post(url, json=internal_body, headers=headers, **kwargs)
        if resp.status_code == 200:
            return True, "Connection successful"
        else:
            try:
                err = resp.json().get('error', {}).get('message', resp.text)
            except Exception:
                err = resp.text
            return False, f"HTTP {resp.status_code}: {err}"
    except Exception as e:
        return False, str(e)

def _exchange_code_for_tokens(code: str, redirect_uri: str) -> dict | None:
    """Exchange an authorization code for access + refresh tokens."""
    try:
        kwargs = get_httpx_kwargs()
        kwargs['timeout'] = 30.0
        resp = httpx.post(
            URL_TOKEN,
            data={
                'code': code,
                'client_id': CLIENT_ID,
                'client_secret': CLIENT_SECRET,
                'redirect_uri': redirect_uri,
                'grant_type': 'authorization_code',
            },
            **kwargs,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return None


def start_oauth_flow() -> dict:
    """
    Run the full Google OAuth flow:
    1. Start local HTTP server on a random port
    2. Open browser with Google auth URL
    3. Wait for callback with auth code
    4. Exchange code for tokens
    5. Fetch user info

    Returns dict with keys: success, email, name, refresh_token, access_token, error
    """
    result = {
        'success': False,
        'email': '',
        'name': '',
        'refresh_token': '',
        'access_token': '',
        'error': '',
        'auth_url': '',
    }

    # Reset handler state
    _OAuthCallbackHandler.auth_code = None
    _OAuthCallbackHandler.error = None

    # Use fixed port for OAuth callback
    callback_port = OAUTH_PORT
    try:
        server = HTTPServer(('0.0.0.0', callback_port), _OAuthCallbackHandler)
    except Exception:
        # Fallback to random port if preferred port is busy
        server = HTTPServer(('0.0.0.0', 0), _OAuthCallbackHandler)
    
    port = server.server_address[1]
    
    # Support custom redirect URI for server deployments
    env_redirect = os.environ.get('OAUTH_REDIRECT_URI', '').strip()
    if env_redirect:
        redirect_uri = env_redirect
    else:
        redirect_uri = f'http://127.0.0.1:{port}'

    # Build auth URL
    params = {
        'client_id': CLIENT_ID,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': ' '.join(OAUTH_SCOPES),
        'access_type': 'offline',
        'prompt': 'consent',
    }
    auth_url = f"{URL_AUTH}?{urllib.parse.urlencode(params)}"
    result['auth_url'] = auth_url

    # Open browser
    webbrowser.open(auth_url)

    # Wait for the callback (timeout after 120 seconds)
    server.timeout = 120
    server.handle_request()
    server.server_close()

    if _OAuthCallbackHandler.error:
        result['error'] = f"OAuth error: {_OAuthCallbackHandler.error}"
        return result

    if not _OAuthCallbackHandler.auth_code:
        result['error'] = "No authorization code received (timed out?)"
        return result

    # Exchange code for tokens
    tokens = _exchange_code_for_tokens(_OAuthCallbackHandler.auth_code, redirect_uri)
    if not tokens or 'refresh_token' not in tokens:
        result['error'] = "Failed to exchange code for tokens"
        return result

    result['refresh_token'] = tokens['refresh_token']
    result['access_token'] = tokens.get('access_token', '')

    # Fetch user info
    if result['access_token']:
        user_info = fetch_user_info(result['access_token'])
        if user_info:
            result['email'] = user_info.get('email', '')
            result['name'] = user_info.get('name', '')

    result['success'] = True
    return result


def get_oauth_url_only(auto_save=False) -> tuple[str, int]:
    """
    Generate an OAuth URL and start the callback server in a background thread.
    Returns (auth_url, port). The server waits for one request then stops.
    If auto_save is True, the account will be added to the database automatically.
    """
    _OAuthCallbackHandler.auth_code = None
    _OAuthCallbackHandler.error = None
    _OAuthCallbackHandler.result = None
    _OAuthCallbackHandler.auto_save = auto_save

    callback_port = OAUTH_PORT
    try:
        server = HTTPServer(('0.0.0.0', callback_port), _OAuthCallbackHandler)
    except Exception:
        server = HTTPServer(('0.0.0.0', 0), _OAuthCallbackHandler)
        
    port = server.server_address[1]
    
    # Support custom redirect URI for server deployments
    env_redirect = os.environ.get('OAUTH_REDIRECT_URI', '').strip()
    if env_redirect:
        redirect_uri = env_redirect
    else:
        redirect_uri = f'http://127.0.0.1:{port}'

    params = {
        'client_id': CLIENT_ID,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': ' '.join(OAUTH_SCOPES),
        'access_type': 'offline',
        'prompt': 'consent',
    }
    auth_url = f"{URL_AUTH}?{urllib.parse.urlencode(params)}"

    # Run server in background thread
    def serve():
        server.timeout = 180  # 3 minute timeout
        server.handle_request()
        server.server_close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    return auth_url, port


def check_oauth_result(port: int) -> dict:
    """
    Check if the OAuth callback has been received.
    Returns dict with success, email, name, refresh_token, access_token, error, pending.
    """
    result = {
        'success': False,
        'pending': True,
        'email': '',
        'name': '',
        'refresh_token': '',
        'access_token': '',
        'error': '',
    }

    if _OAuthCallbackHandler.error:
        result['pending'] = False
        result['error'] = f"OAuth error: {_OAuthCallbackHandler.error}"
        return result

    if _OAuthCallbackHandler.result is not None:
        result.update(_OAuthCallbackHandler.result)
        result['pending'] = False
        return result

    return result


def get_available_models(force=False) -> list:
    """Get available models from cache or live fetch if >24h."""
    config = load_config()
    last_fetch = config.get('last_model_fetch', 0)
    current_time = time.time()
    
    # 24 hours = 86400 seconds
    if not force and (current_time - last_fetch < 86400) and config.get('available_models'):
        return config.get('available_models')

    # Fetch from one active account
    accounts = get_accounts()
    active_accounts = [a for a in accounts if a['status'] == 'active']
    if not active_accounts:
        return config.get('available_models', [])

    target = active_accounts[0]
    rt = target.get('_refresh_token')
    if not rt:
        return config.get('available_models', [])

    tokens = refresh_access_token(rt)
    if not tokens or 'access_token' not in tokens:
        return config.get('available_models', [])

    quota = fetch_live_quota(tokens['access_token'])
    model_names = list(quota.get('models', {}).keys())
    
    if model_names:
        config['available_models'] = sorted(model_names)
        config['last_model_fetch'] = current_time
        save_config(config)
        return config['available_models']
        
    return config.get('available_models', [])

