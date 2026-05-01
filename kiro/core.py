"""
Kiro Core - Configuration and Authentication Management for Kiro AI.
Inspired by AGPM Core.
"""

import os
import json
import sqlite3
import time
import secrets
import datetime
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import webbrowser
import threading
import base64
import hashlib
import uuid
import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# --- Paths ---
KIRO_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(KIRO_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'kiro.db')
CONFIG_PATH = os.path.join(DATA_DIR, 'config.json')
KEY_PATH = os.path.join(DATA_DIR, '.mk')


def load_config() -> dict:
    """Load config.json from kiro/data/."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_config(config_data: dict) -> bool:
    """Save config.json to kiro/data/."""
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2)
        return True
    except Exception:
        return False


# --- Encryption (local key) ---

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
    if not plaintext:
        return ""
    key = _get_or_create_master_key()
    iv = secrets.token_bytes(16)
    aesgcm = AESGCM(key)
    combined = aesgcm.encrypt(iv, plaintext.encode('utf-8'), None)
    tag = combined[-16:]
    ciphertext = combined[:-16]
    return f"{iv.hex()}:{tag.hex()}:{ciphertext.hex()}"


def decrypt_value(encrypted: str) -> str | None:
    """Decrypt an iv:tag:ciphertext hex string. Returns None on failure."""
    if not encrypted:
        return ""
    if encrypted.startswith('{') or encrypted.startswith('['):
        return encrypted
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
    """Create the Kiro database and accounts table if they don't exist."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                profile_arn TEXT NOT NULL UNIQUE,
                name TEXT DEFAULT '',
                region TEXT DEFAULT 'us-east-1',
                refresh_token TEXT NOT NULL,
                access_token TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at INTEGER NOT NULL,
                last_used INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS models (
                id TEXT PRIMARY KEY,
                name TEXT,
                provider TEXT
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[!] Database initialization failed: {e}")


def _get_conn():
    """Get a connection to the Kiro database, initializing if needed."""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# --- Account CRUD ---

def get_accounts() -> list[dict]:
    """Get all accounts from the local Kiro database."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM accounts ORDER BY last_used DESC")
    rows = cursor.fetchall()
    conn.close()

    accounts = []
    for row in rows:
        acc = dict(row)
        # Decrypt tokens for internal use (but strip in API response)
        acc['_refresh_token'] = decrypt_value(acc.get('refresh_token', ''))
        acc['_access_token'] = decrypt_value(acc.get('access_token', ''))
        accounts.append(acc)
    return accounts


def add_account(profile_arn: str, refresh_token: str, name: str = '', region: str = 'us-east-1', access_token: str = '') -> bool:
    """Add a new Kiro account to the local database."""
    conn = _get_conn()
    cursor = conn.cursor()

    # Check for duplicate
    cursor.execute("SELECT profile_arn FROM accounts WHERE profile_arn = ?", (profile_arn,))
    if cursor.fetchone():
        conn.close()
        return False  # Already exists

    account_id = secrets.token_hex(16)
    now = int(time.time() * 1000)
    encrypted_rt = encrypt_value(refresh_token)
    encrypted_at = encrypt_value(access_token)

    cursor.execute(
        """INSERT INTO accounts
           (id, profile_arn, name, region, refresh_token, access_token, status, created_at, last_used)
           VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
        (account_id, profile_arn, name, region, encrypted_rt, encrypted_at, now, now),
    )
    conn.commit()
    conn.close()
    return True


def remove_account(profile_arn: str) -> bool:
    """Remove an account by Profile ARN."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM accounts WHERE profile_arn = ?", (profile_arn,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# --- Models CRUD ---

def get_saved_models() -> list[dict]:
    """Get all saved models from the local database."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM models")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def save_models(models: list[dict]):
    """Replace the saved models in the local database."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM models")
    for m in models:
        cursor.execute(
            "INSERT INTO models (id, name, provider) VALUES (?, ?, ?)",
            (m['id'], m['name'], m['provider'])
        )
    conn.commit()
    conn.close()


# --- Admin Auth & Portal Config ---

def get_admin_creds() -> tuple[str, str]:
    """Get admin username and password from config, default to admin/admin."""
    config = load_config()
    auth = config.get('auth', {})
    username = auth.get('username', 'admin')
    password = auth.get('password', 'admin')
    return username, password


def update_account_access_token(profile_arn: str, access_token: str) -> bool:
    """Update the access token for an existing account."""
    conn = _get_conn()
    cursor = conn.cursor()
    encrypted_at = encrypt_value(access_token)
    now = int(time.time() * 1000)
    cursor.execute(
        "UPDATE accounts SET access_token = ?, last_used = ? WHERE profile_arn = ?",
        (encrypted_at, now, profile_arn)
    )
    conn.commit()
    conn.close()
    return True


# Kiro Electron User-Agent — required for CloudFront to allow the refresh endpoint
_KIRO_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Kiro/0.1.36 Chrome/132.0.6834.210 "
    "Electron/34.5.2 Safari/537.36"
)

# Path to the Kiro SSO token cache that Kiro itself reads/writes
_KIRO_TOKEN_CACHE = os.path.expanduser("~/.aws/sso/cache/kiro-auth-token.json")


def _read_kiro_token_cache() -> dict:
    """Read the Kiro SSO token cache file."""
    try:
        with open(_KIRO_TOKEN_CACHE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def _write_kiro_token_cache(data: dict):
    """Write updated tokens back to the Kiro SSO token cache file."""
    try:
        existing = _read_kiro_token_cache()
        existing.update(data)
        with open(_KIRO_TOKEN_CACHE, 'w') as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        print(f"[*] Failed to update Kiro token cache: {e}")


def refresh_access_token(profile_arn: str) -> str | None:
    """Exchange the refresh token for a new access token via Kiro backend."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT refresh_token FROM accounts WHERE profile_arn = ?", (profile_arn,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
        
    refresh_token = decrypt_value(row['refresh_token'])
    
    # Also try the cache file's refresh token if DB one is missing
    if not refresh_token:
        cache = _read_kiro_token_cache()
        refresh_token = cache.get('refreshToken', '')
    
    if not refresh_token:
        return None
        
    url = "https://prod.us-east-1.auth.desktop.kiro.dev/refreshToken"
    try:
        resp = httpx.post(
            url,
            json={'refreshToken': refresh_token},
            headers={
                'Content-Type': 'application/json',
                'User-Agent': _KIRO_UA
            },
            timeout=30.0
        )
        if resp.is_success:
            data = resp.json()
            new_access_token = data.get('accessToken', '')
            if new_access_token:
                update_account_access_token(profile_arn, new_access_token)
                # Also update the Kiro cache file so Kiro stays in sync
                expires_at = (datetime.datetime.utcnow() + datetime.timedelta(seconds=data.get('expiresIn', 3600))).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                _write_kiro_token_cache({'accessToken': new_access_token, 'expiresAt': expires_at})
                print(f"[*] Token refreshed successfully for {profile_arn}")
                return new_access_token
        else:
            print(f"[*] Token refresh failed: {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        print(f"[*] Exception during token refresh: {e}")
        
    return None


def get_access_token(profile_arn: str, force_refresh: bool = False) -> str | None:
    """Get the current access token for a profile.
    
    Fast path: read from Kiro's SSO cache file which Kiro keeps fresh.
    If that token is expired (or force_refresh=True), call refresh_access_token.
    Falls back to the DB-cached token as a last resort.
    """
    # Fast path: check if Kiro's own cache file has a fresh token
    if not force_refresh:
        cache = _read_kiro_token_cache()
        cached_access = cache.get('accessToken', '')
        expires_at_str = cache.get('expiresAt', '')
        if cached_access and expires_at_str:
            try:
                # Parse ISO format, strip trailing Z for fromisoformat
                expires_at = datetime.datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
                now = datetime.datetime.now(datetime.timezone.utc)
                # Use token if it has more than 5 minutes remaining
                if expires_at - now > datetime.timedelta(minutes=5):
                    return cached_access
            except Exception:
                pass
    
    # Token is expired or missing — refresh it
    fresh_token = refresh_access_token(profile_arn)
    if fresh_token:
        return fresh_token
    
    # Last resort: return the DB-cached token (may be expired but worth trying)
    conn = _get_conn()
    row = conn.cursor().execute("SELECT access_token FROM accounts WHERE profile_arn = ?", (profile_arn,)).fetchone()
    conn.close()
    if row and row['access_token']:
        cached_token = decrypt_value(row['access_token'])
        if cached_token:
            print(f"[*] Using stale DB token as last resort for {profile_arn}")
            return cached_token
    
    return None


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
    
    if 'secret_key' not in portal:
        portal['secret_key'] = secrets.token_hex(32)
        config['portal'] = portal
        save_config(config)
        
    return {
        'port': portal.get('port', 5005),
        'admin_slug': portal.get('admin_slug', 'admin'),
        'public_url': portal.get('public_url', ''),
        'secret_key': portal['secret_key']
    }


def save_portal_config(port, admin_slug='admin', public_url='') -> bool:
    """Save portal configuration."""
    config = load_config()
    if 'portal' not in config:
        config['portal'] = {}
    config['portal']['port'] = port
    config['portal']['admin_slug'] = admin_slug
    config['portal']['public_url'] = public_url
    return save_config(config)


# --- Models ---

KNOWN_MODELS = [
    # Anthropic
    ("anthropic.claude-3-5-sonnet-20241022-v2:0", "Anthropic"),
    ("anthropic.claude-3-5-sonnet-20240620-v1:0", "Anthropic"),
    ("anthropic.claude-3-opus-20240229-v1:0", "Anthropic"),
    ("anthropic.claude-3-sonnet-20240229-v1:0", "Anthropic"),
    ("anthropic.claude-3-haiku-20240307-v1:0", "Anthropic"),
    
    # Meta
    ("meta.llama3-1-405b-instruct-v1:0", "Meta"),
    ("meta.llama3-1-70b-instruct-v1:0", "Meta"),
    ("meta.llama3-70b-instruct-v1:0", "Meta"),
    ("meta.llama3-8b-instruct-v1:0", "Meta"),
    
    # Mistral
    ("mistral.mistral-large-2402-v1:0", "Mistral"),
    ("mistral.mixtral-8x7b-instruct-v0:1", "Mistral"),
    
    # Amazon
    ("amazon.titan-text-premier-v1:0", "Amazon"),
    ("amazon.nova-pro-v1:0", "Amazon"),
    ("amazon.nova-lite-v1:0", "Amazon"),
    ("amazon.nova-micro-v1:0", "Amazon"),
    
    # Alibaba (Qwen)
    ("qwen2.5-coder-32b-instruct", "Alibaba"),
    ("qwen2.5-coder-7b-instruct", "Alibaba"),
    
    # Aliases
    ("claude-sonnet-4.5", "Anthropic"),
    ("claude-3-5-sonnet", "Anthropic"),
    ("claude-3-opus", "Anthropic"),
]

# --- OAuth Flow Setup ---

OAUTH_PORT = 3128

# Store active OAuth sessions in memory: state -> code_verifier
_oauth_sessions = {}

def generate_pkce_pair():
    """Generate PKCE code_verifier and code_challenge."""
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode('ascii')).digest()).decode('ascii').rstrip('=')
    return code_verifier, code_challenge

def get_oauth_url_only(auto_save=False) -> str:
    """Generate PKCE auth URL for manual flow."""
    code_verifier, code_challenge = generate_pkce_pair()
    state = str(uuid.uuid4())
    
    _oauth_sessions[state] = {
        'code_verifier': code_verifier,
        'auto_save': auto_save,
        'created_at': time.time()
    }
    
    # Cleanup old sessions (older than 30 mins)
    now = time.time()
    for s in list(_oauth_sessions.keys()):
        if now - _oauth_sessions[s]['created_at'] > 1800:
            del _oauth_sessions[s]

    redirect_uri = f'http://localhost:{OAUTH_PORT}'

    params = {
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
        'redirect_uri': redirect_uri,
        'redirect_from': 'KiroIDE'
    }
    
    return f"https://app.kiro.dev/signin?{urllib.parse.urlencode(params)}"

def process_manual_callback(pasted_url: str) -> dict:
    """Process the URL pasted by the user to extract code and exchange it."""
    try:
        parsed = urllib.parse.urlparse(pasted_url)
        params = urllib.parse.parse_qs(parsed.query)
        
        if 'error' in params:
            return {'success': False, 'error': f"OAuth error: {params['error'][0]}"}
            
        if 'code' not in params or 'state' not in params:
            return {'success': False, 'error': 'Invalid URL: Missing code or state parameters.'}
            
        code = params['code'][0]
        state = params['state'][0]
        
        if state not in _oauth_sessions:
            return {'success': False, 'error': 'Invalid or expired state session. Please try authenticating again.'}
            
        session_data = _oauth_sessions.pop(state)
        code_verifier = session_data['code_verifier']
        auto_save = session_data['auto_save']
        
        login_option = params.get('login_option', [''])[0]
        
        # Reconstruct the exact redirect_uri expected by Kiro's backend
        # e.g., http://localhost:3128/oauth/callback?login_option=google
        base_redirect = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if login_option:
            redirect_uri = f"{base_redirect}?login_option={login_option}"
        else:
            redirect_uri = base_redirect
        
        tokens = _exchange_code_for_tokens(code, code_verifier, redirect_uri)
        if not tokens or 'refreshToken' not in tokens:
            return {'success': False, 'error': 'Failed to exchange code for tokens.'}
            
        refresh_token = tokens['refreshToken']
        access_token = tokens.get('accessToken', '')
        profile_arn = tokens.get('profileArn', '')
        
        if not profile_arn:
            return {'success': False, 'error': 'Authentication failed: Missing profile ARN in response.'}
            
        added = False
        name = "Kiro Auto Account"
        if auto_save:
            parts = profile_arn.split('/')
            if len(parts) > 1:
                name = f"Profile {parts[-1]}"
            added = add_account(profile_arn, refresh_token, name=name, region='us-east-1', access_token=access_token)
            
        return {
            'success': True,
            'name': name,
            'profile_arn': profile_arn,
            'added': added
        }
    except Exception as e:
        return {'success': False, 'error': f"Exception processing callback: {e}"}

def _exchange_code_for_tokens(code: str, code_verifier: str, redirect_uri: str) -> dict | None:
    """Exchange auth code via Kiro desktop auth API."""
    url = "https://prod.us-east-1.auth.desktop.kiro.dev/oauth/token"
    try:
        resp = httpx.post(
            url,
            json={
                'code': code,
                'code_verifier': code_verifier,
                'redirect_uri': redirect_uri
            },
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'kiro-agent/1.0'
            },
            timeout=30.0
        )
        if resp.is_success:
            return resp.json()
        print(f"[*] Token exchange failed: {resp.status_code} - {resp.text}")
        return None
    except Exception as e:
        print(f"[*] Exception during token exchange: {e}")
        return None
