"""
Kiro Core - Configuration and Authentication Management for Kiro AI.
Inspired by AGPM Core.
"""

import os
import json
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
JSON_DB_PATH = os.path.join(DATA_DIR, 'kiro_data.json')
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


# --- Database Setup (JSON) ---

def _read_db() -> dict:
    """Read the JSON database, initialize if missing."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(JSON_DB_PATH):
        try:
            with open(JSON_DB_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Ensure all base keys exist
                data.setdefault('accounts', [])
                data.setdefault('models', [])
                data.setdefault('next_account_id', 1)
                return data
        except Exception as e:
            print(f"[!] Failed to read JSON DB: {e}")
            
    # Default structure
    return {
        "accounts": [],
        "models": [],
        "next_account_id": 1
    }


def _write_db(data: dict) -> bool:
    """Write data dict back to the JSON database."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        # Use a temporary file for atomic write
        temp_path = JSON_DB_PATH + '.tmp'
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        os.replace(temp_path, JSON_DB_PATH)
        return True
    except Exception as e:
        print(f"[!] Failed to write JSON DB: {e}")
        return False


# --- Account CRUD ---

def get_accounts() -> list[dict]:
    """Get all accounts from the local Kiro database."""
    db = _read_db()
    rows = db.get('accounts', [])
    # Sort by last_used DESC
    rows.sort(key=lambda x: x.get('last_used', 0), reverse=True)

    accounts = []
    for row in rows:
        acc = dict(row)
        # Decrypt tokens for internal use (but strip in API response)
        acc['_refresh_token'] = decrypt_value(acc.get('refresh_token', ''))
        acc['_access_token'] = decrypt_value(acc.get('access_token', ''))
        accounts.append(acc)
    return accounts


def add_account(profile_arn: str, refresh_token: str, name: str = '', region: str = 'us-east-1', access_token: str = '', user_id: str = '', email: str = '') -> dict:
    """Add a Kiro account in the local database.
    Returns: dict with 'status' and 'id' (the new auto-increment id).
    """
    db = _read_db()
    now = int(time.time() * 1000)
    encrypted_rt = encrypt_value(refresh_token)
    encrypted_at = encrypt_value(access_token)

    if not user_id:
        user_id = profile_arn

    new_id = db.get('next_account_id', 1)
    
    new_acc = {
        'id': new_id,
        'profile_arn': profile_arn,
        'user_id': user_id,
        'email': email,
        'name': name,
        'region': region,
        'refresh_token': encrypted_rt,
        'access_token': encrypted_at,
        'status': 'active',
        'created_at': now,
        'last_used': now
    }
    
    db['accounts'].append(new_acc)
    db['next_account_id'] = new_id + 1
    _write_db(db)
    
    print(f"[*] Account added (id={new_id}): {user_id}", flush=True)
    return {'status': 'added', 'id': new_id}


def remove_account(account_id: int) -> bool:
    """Remove an account by its unique integer ID."""
    db = _read_db()
    original_len = len(db['accounts'])
    db['accounts'] = [a for a in db['accounts'] if a.get('id') != account_id]
    
    if len(db['accounts']) < original_len:
        _write_db(db)
        return True
    return False


# --- Models CRUD ---

def get_saved_models() -> list[dict]:
    """Get all saved models from the local database."""
    db = _read_db()
    return db.get('models', [])

def save_models(models: list[dict]):
    """Replace the saved models in the local database."""
    db = _read_db()
    db['models'] = []
    for m in models:
        db['models'].append({
            'id': m.get('id'),
            'name': m.get('name'),
            'provider': m.get('provider')
        })
    _write_db(db)


# --- Admin Auth & Portal Config ---

def get_admin_creds() -> tuple[str, str]:
    """Get admin username and password from config, default to admin/admin."""
    config = load_config()
    auth = config.get('auth', {})
    username = auth.get('username', 'admin')
    password = auth.get('password', 'admin')
    return username, password


def update_account_access_token(account_id: int, access_token: str) -> bool:
    """Update the access token for an existing account by its unique ID."""
    db = _read_db()
    for acc in db.get('accounts', []):
        if acc.get('id') == account_id:
            acc['access_token'] = encrypt_value(access_token)
            acc['last_used'] = int(time.time() * 1000)
            _write_db(db)
            return True
    return False


# Kiro Electron User-Agent — required for CloudFront to allow the refresh endpoint
# Kiro Electron User-Agent
_KIRO_UA = "KiroIDE-0.1.36-e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# Path to the project-local token cache file
_KIRO_TOKEN_CACHE = os.path.join(DATA_DIR, 'kiro-auth-token.json')


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


def refresh_access_token(account_id: int) -> str | None:
    """Exchange the refresh token for a new access token via Kiro backend."""
    db = _read_db()
    row = next((a for a in db.get('accounts', []) if a.get('id') == account_id), None)
    
    if not row:
        return None
        
    refresh_token = decrypt_value(row['refresh_token'])
    
    # Also try the cache file's refresh token if DB one is missing
    if not refresh_token:
        cache = _read_kiro_token_cache()
        if cache.get('accountId') == account_id:
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
                update_account_access_token(account_id, new_access_token)
                # Also update the Kiro cache file so Kiro stays in sync
                expires_at = (datetime.datetime.utcnow() + datetime.timedelta(seconds=data.get('expiresIn', 3600))).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                _write_kiro_token_cache({'accessToken': new_access_token, 'expiresAt': expires_at, 'accountId': account_id})
                print(f"[*] Token refreshed successfully for account id={account_id}")
                return new_access_token
        else:
            print(f"[*] Token refresh failed: {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        print(f"[*] Exception during token refresh: {e}")
        
    return None


def get_access_token(account_id: int, force_refresh: bool = False) -> str | None:
    """Get the current access token for an account by its unique ID.
    
    Fast path: read from Kiro's SSO cache file which Kiro keeps fresh.
    If that token is expired (or force_refresh=True), call refresh_access_token.
    Falls back to the DB-cached token as a last resort.
    """
    db = _read_db()
    row = next((a for a in db.get('accounts', []) if a.get('id') == account_id), None)
    if not row:
        return None
        
    profile_arn = row.get('profile_arn')
    
    # Fast path: check if Kiro's own cache file has a fresh token for this specific account
    if not force_refresh:
        cache = _read_kiro_token_cache()
        if cache.get('accountId') == account_id:
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
    fresh_token = refresh_access_token(account_id)
    if fresh_token:
        return fresh_token
    
    # Last resort: return the DB-cached token (may be expired but worth trying)
    if row.get('access_token'):
        cached_token = decrypt_value(row['access_token'])
        if cached_token:
            print(f"[*] Using stale DB token as last resort for account id={account_id}")
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


DEFAULT_PROMPT_SETTINGS = {
    'template': 'System Instructions:\n{{system}}\n\nUser Request:\n{{request}}',
    'custom_shortcodes': [
        {
            'tag': '[EXPERT]',
            'value': 'You are an expert senior software engineer with deep knowledge of best practices, clean code, and scalable architecture.'
        },
        {
            'tag': '[CLEAN_CODE]',
            'value': 'Please ensure the code follows SOLID principles, is well-documented, and uses meaningful variable names.'
        },
        {
            'tag': '[BUG_HUNTER]',
            'value': 'Focus on finding edge cases, potential race conditions, and logic errors in the following code.'
        },
        {
            'tag': '[REFRACTOR]',
            'value': 'Refactor the provided code to improve readability and performance without changing its external behavior.'
        }
    ]
}


def get_prompt_settings() -> dict:
    """Get prompt template and custom shortcodes from the database."""
    db = _read_db()
    return db.get('prompt_settings', DEFAULT_PROMPT_SETTINGS)


def save_prompt_settings(settings: dict) -> bool:
    """Save prompt template and custom shortcodes to the database."""
    db = _read_db()
    db['prompt_settings'] = settings
    return _write_db(db)


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

# Path for persisting OAuth sessions across restarts
_OAUTH_SESSIONS_PATH = os.path.join(DATA_DIR, 'oauth_sessions.json')

# In-memory cache (always synced from/to disk)
_oauth_sessions = {}


def _load_oauth_sessions():
    """Load OAuth sessions from disk into memory."""
    global _oauth_sessions
    try:
        if os.path.exists(_OAUTH_SESSIONS_PATH):
            with open(_OAUTH_SESSIONS_PATH, 'r') as f:
                _oauth_sessions = json.load(f)
    except Exception:
        _oauth_sessions = {}


def _save_oauth_sessions():
    """Persist current OAuth sessions to disk."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        now = time.time()
        # Prune sessions older than 30 minutes before saving
        pruned = {k: v for k, v in _oauth_sessions.items() if now - v.get('created_at', 0) < 1800}
        with open(_OAUTH_SESSIONS_PATH, 'w') as f:
            json.dump(pruned, f)
    except Exception as e:
        print(f"[!] Failed to save OAuth sessions: {e}")


# Load persisted sessions on module import
_load_oauth_sessions()


def generate_pkce_pair():
    """Generate PKCE code_verifier and code_challenge."""
    # 32 bytes of randomness results in a 43-character URL-safe string (minimum required)
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode('ascii')).digest()).decode('ascii').rstrip('=')
    return code_verifier, code_challenge

def get_oauth_url_only(auto_save=False) -> str:
    """Generate PKCE auth URL for manual flow."""
    code_verifier, code_challenge = generate_pkce_pair()
    state = str(uuid.uuid4())
    redirect_uri = f'http://localhost:{OAUTH_PORT}'
    
    _oauth_sessions[state] = {
        'code_verifier': code_verifier,
        'auto_save': auto_save,
        'created_at': time.time(),
        'redirect_uri': redirect_uri
    }
    
    # Cleanup old sessions (older than 30 mins) and persist to disk
    now = time.time()
    for s in list(_oauth_sessions.keys()):
        if now - _oauth_sessions[s]['created_at'] > 1800:
            del _oauth_sessions[s]
    _save_oauth_sessions()

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
        
        print(f"[*] OAuth callback: state={state}, code={code[:12]}...", flush=True)
        
        # Re-load sessions from disk (in case server was restarted after session was created)
        _load_oauth_sessions()
        
        print(f"[*] Available sessions: {list(_oauth_sessions.keys())}", flush=True)
        
        if state not in _oauth_sessions:
            return {'success': False, 'error': 'Invalid or expired state session. Please click "Auto Login (OAuth)" again to generate a fresh link, sign in, then paste the new callback URL.'}
            
        session_data = _oauth_sessions.pop(state)
        _save_oauth_sessions()  # Remove consumed session from disk
        code_verifier = session_data['code_verifier']
        auto_save = session_data['auto_save']

        # Kiro's auth server uses different redirect_uri for auth vs token exchange:
        # Auth URL: http://localhost:3128 (base only)
        # Token exchange: http://localhost:3128/oauth/callback?login_option=google (full callback)
        # This matches Kiro IDE's fullRedirectUri = `${redirectUri}${callback.path}?login_option=${callback.loginOption}`
        login_option = params.get('login_option', [''])[0]
        base_uri = session_data.get('redirect_uri', f'http://localhost:{OAUTH_PORT}')
        if login_option:
            redirect_uri = f"{base_uri}/oauth/callback?login_option={login_option}"
        else:
            redirect_uri = f"{base_uri}/oauth/callback"
        
        print(f"[*] Token exchange: redirect_uri={redirect_uri}", flush=True)
        
        tokens = _exchange_code_for_tokens(code, code_verifier, redirect_uri)
        if not tokens or 'refreshToken' not in tokens:
            error_detail = tokens.get('error') if isinstance(tokens, dict) else "Unknown error"
            return {'success': False, 'error': f"Token exchange failed: {error_detail}"}
            
        refresh_token = tokens['refreshToken']
        access_token = tokens.get('accessToken', '')
        profile_arn = tokens.get('profileArn', '')
        
        if not profile_arn:
            return {'success': False, 'error': 'Authentication failed: Missing profile ARN in response.'}
            
        name = "Kiro Auto Account"
        result_status = 'skipped'
        new_account_id = None
        if auto_save:
            parts = profile_arn.split('/')
            if len(parts) > 1:
                name = f"Profile {parts[-1]}"
            result = add_account(profile_arn, refresh_token, name=name, region='us-east-1', access_token=access_token)
            result_status = result['status']
            new_account_id = result['id']
            
        return {
            'success': True,
            'name': name,
            'profile_arn': profile_arn,
            'status': result_status,
            'account_id': new_account_id
        }
    except Exception as e:
        print(f"[!] Exception processing callback: {e}", flush=True)
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
                'redirect_uri': redirect_uri,
                'invitation_code': ''
            },
            headers={
                'Content-Type': 'application/json',
                'User-Agent': _KIRO_UA
            },
            timeout=30.0
        )
        data = resp.json() if 'application/json' in resp.headers.get('content-type', '') else {}
        print(f"[*] Token exchange result: {resp.status_code} | body: {resp.text[:500]}", flush=True)
        if resp.is_success:
            return data
        # Return the error message from the server if possible
        error_msg = data.get('error_description') or data.get('error') or resp.text[:100]
        return {'error': f"Server returned {resp.status_code}: {error_msg}"}
    except Exception as e:
        print(f"[*] Exception during token exchange: {e}", flush=True)
        return {'error': str(e)}
