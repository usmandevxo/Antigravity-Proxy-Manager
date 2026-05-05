"""
AGPM Proxy Server - OpenAI-compatible API proxy.

Accepts OpenAI-format requests on /v1/chat/completions
and forwards them to Google's Gemini API using stored account tokens.
Supports streaming (SSE) and non-streaming modes.
"""

import json
import time
import uuid
import threading
import itertools

import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler

from core import (
    get_accounts,
    refresh_access_token,
    decrypt_value,
    _get_conn,
    encrypt_value,
    get_httpx_kwargs,
)

# --- API Constants ---

INTERNAL_BASE_URLS = [
    'https://daily-cloudcode-pa.googleapis.com/v1internal',  # Primary - separate quota bucket
    'https://cloudcode-pa.googleapis.com/v1internal',        # Fallback
]
USER_AGENT = 'antigravity/1.11.3 Linux/x86_64'

# Model alias mapping (same as the Electron app)
MODEL_MAPPING = {
    # Direct (keep as-is)
    'gemini-3-flash': 'gemini-3-flash',
    'gemini-3.1-pro-high': 'gemini-3.1-pro-high',
    'gemini-3.1-pro-low': 'gemini-3.1-pro-low',
    'claude-sonnet-4-6-thinking': 'claude-sonnet-4-6',
    'claude-opus-4-6-thinking': 'claude-opus-4-6-thinking',

    # Gemini aliases
    'gemini-1.5-flash': 'gemini-3-flash',
    'gemini-1.5-pro': 'gemini-3.1-pro-high',
    'gemini-2.0-flash': 'gemini-2.5-flash',
    'gemini-2.0-pro': 'gemini-2.5-pro',

    # Claude aliases
    'claude-sonnet-4': 'claude-sonnet-4-6-thinking',
    'claude-sonnet-4-6': 'claude-sonnet-4-6-thinking',
    'claude-sonnet-4-5': 'claude-sonnet-4-6-thinking',
    'claude-sonnet-4-5-thinking': 'claude-sonnet-4-6-thinking',
    'claude-3.5-sonnet': 'claude-sonnet-4-6-thinking',
    'claude-3-5-sonnet-20241022': 'claude-sonnet-4-6-thinking',
    'claude-opus-4': 'claude-opus-4-6-thinking',
    'claude-opus-4-5-thinking': 'claude-opus-4-6-thinking',

    # GPT aliases
    'gpt-4': 'gemini-3-flash',
    'gpt-4-turbo': 'gemini-3-flash',
    'gpt-4o': 'gemini-3-flash',
    'gpt-4o-mini': 'gemini-3-flash',
    'gpt-3.5-turbo': 'gemini-3-flash',
}

SUPPORTED_MODELS = [
    'gemini-3.1-pro-high',
    'gemini-3.1-pro-low',
    'gemini-3-flash',
    'claude-sonnet-4-6-thinking',
    'claude-opus-4-6-thinking',
]

# --- Token Rotation & Cooldown ---

_account_cycle = None
_cycle_lock = threading.Lock()
_cooldowns = {}  # email -> timestamp when cooldown ends


def _build_account_cycle():
    """Build/rebuild the round-robin account cycle from DB, skipping those in cooldown."""
    global _account_cycle
    accounts = get_accounts()
    now = time.time()
    
    # Filter active accounts that are not in cooldown
    active = [a for a in accounts if a.get('status') == 'active' and _cooldowns.get(a['email'], 0) < now]
    
    if not active:
        # If ALL active accounts are in cooldown, just use all active ones as fallback
        active = [a for a in accounts if a.get('status') == 'active']
        
    if not active:
        _account_cycle = None
        return
        
    _account_cycle = itertools.cycle(active)


def _get_next_account():
    """Get the next account from the round-robin cycle."""
    global _account_cycle
    with _cycle_lock:
        if _account_cycle is None:
            _build_account_cycle()
        if _account_cycle is None:
            return None
        try:
            # Try a few times to get a non-cooldown account from the cycle
            for _ in range(10):
                account = next(_account_cycle)
                if _cooldowns.get(account['email'], 0) < time.time():
                    return account
            return account # fallback
        except StopIteration:
            return None


def mark_account_cooldown(email: str, seconds: int = 60):
    """Mark an account as being in cooldown (e.g. after a 429)."""
    global _account_cycle
    _cooldowns[email] = time.time() + seconds
    # Rebuild cycle to reflect the new cooldown state
    with _cycle_lock:
        _build_account_cycle()


def _get_valid_access_token(account: dict) -> str | None:
    """Get a valid access token, refreshing if expired."""
    # Check if cached token is still valid (5 min buffer)
    expiry = account.get('token_expiry', 0)
    cached_token = account.get('access_token', '')
    if cached_token and expiry > (time.time() * 1000 + 300_000):
        return cached_token

    # Refresh the token
    rt = account.get('_refresh_token')
    if not rt:
        return None

    tokens = refresh_access_token(rt)
    if not tokens or 'access_token' not in tokens:
        return None

    access_token = tokens['access_token']
    new_expiry = int(time.time() * 1000) + (tokens.get('expires_in', 3600) * 1000)

    # Cache back to DB
    conn = _get_conn()
    conn.execute(
        "UPDATE accounts SET access_token = ?, token_expiry = ? WHERE email = ?",
        (access_token, new_expiry, account['email']),
    )
    conn.commit()
    conn.close()

    return access_token


def resolve_model(model_name: str) -> str:
    """Resolve a model alias to the actual internal model name."""
    return MODEL_MAPPING.get(model_name, model_name)


# --- OpenAI -> Gemini Internal Conversion ---

def convert_openai_to_gemini_internal(body: dict, target_model: str, project_id: str = '') -> dict:
    """Convert OpenAI chat request to Gemini internal request format."""
    messages = body.get('messages', [])

    # Build Gemini contents
    contents = []
    system_instruction = None

    for msg in messages:
        role = msg.get('role', 'user')
        content = msg.get('content', '')

        if role == 'system':
            system_instruction = {'parts': [{'text': content}]}
            continue

        gemini_role = 'user' if role == 'user' else 'model'
        parts = []

        if isinstance(content, str):
            parts.append({'text': content})
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, str):
                    parts.append({'text': item})
                elif isinstance(item, dict):
                    if item.get('type') == 'text':
                        parts.append({'text': item.get('text', '')})
                    elif item.get('type') == 'image_url':
                        url = item.get('image_url', {}).get('url', '')
                        if url.startswith('data:'):
                            # Parse data URI
                            mime, _, b64 = url.partition(';base64,')
                            mime = mime.replace('data:', '')
                            parts.append({'inlineData': {'mimeType': mime, 'data': b64}})

        if parts:
            contents.append({'role': gemini_role, 'parts': parts})

    # Build generation config
    gen_config = {}
    if body.get('max_tokens'):
        gen_config['maxOutputTokens'] = body['max_tokens']
    if body.get('temperature') is not None:
        gen_config['temperature'] = body['temperature']
    if body.get('top_p') is not None:
        gen_config['topP'] = body['top_p']

    # Build thinking config for thinking models
    lower_model = target_model.lower()
    if 'thinking' in lower_model:
        gen_config['thinkingConfig'] = {'thinkingBudget': 8192}
        if not gen_config.get('maxOutputTokens'):
            gen_config['maxOutputTokens'] = 65536

    request_body = {
        'contents': contents,
        'generationConfig': gen_config,
    }
    if system_instruction:
        request_body['systemInstruction'] = system_instruction

    # Wrap in internal request format
    internal_req = {
        'requestId': str(uuid.uuid4()),
        'request': request_body,
        'model': target_model,
        'userAgent': USER_AGENT,
        'requestType': 'generate-content',
    }
    # NOTE: We intentionally DON'T send the project field here as it can trigger 
    # strict quota limits on free accounts. The internal API handles this automatically.
    
    return internal_req


# --- Gemini Response -> OpenAI Conversion ---

def convert_gemini_to_openai(gemini_resp: dict, model_name: str) -> dict:
    """Convert Gemini response to OpenAI chat completion format."""
    candidates = gemini_resp.get('candidates', [])

    content = ''
    finish_reason = 'stop'

    if candidates:
        candidate = candidates[0]
        parts = candidate.get('content', {}).get('parts', [])
        text_parts = [p.get('text', '') for p in parts if 'text' in p and not p.get('thought')]
        content = ''.join(text_parts)
        fr = candidate.get('finishReason', 'STOP')
        finish_reason = 'stop' if fr in ('STOP', 'END_TURN') else 'length' if fr == 'MAX_TOKENS' else 'stop'

    usage = gemini_resp.get('usageMetadata', {})

    return {
        'id': f'chatcmpl-{uuid.uuid4().hex[:12]}',
        'object': 'chat.completion',
        'created': int(time.time()),
        'model': model_name,
        'choices': [{
            'index': 0,
            'message': {
                'role': 'assistant',
                'content': content,
            },
            'finish_reason': finish_reason,
        }],
        'usage': {
            'prompt_tokens': usage.get('promptTokenCount', 0),
            'completion_tokens': usage.get('candidatesTokenCount', 0),
            'total_tokens': usage.get('totalTokenCount', 0),
        },
    }


# --- Proxy HTTP Handler ---

class ProxyHandler(BaseHTTPRequestHandler):
    """HTTP handler for the OpenAI-compatible proxy."""

    def do_GET(self):
        if self.path == '/v1/models':
            self._handle_models()
        elif self.path == '/health' or self.path == '/':
            self._send_json(200, {'status': 'ok', 'service': 'AGPM Proxy'})
        else:
            self._send_json(404, {'error': {'message': 'Not found', 'type': 'not_found'}})

    def do_POST(self):
        if self.path == '/v1/chat/completions':
            self._handle_chat_completions()
        else:
            self._send_json(404, {'error': {'message': f'Endpoint {self.path} not found', 'type': 'not_found'}})

    def _handle_models(self):
        """Return list of available models."""
        from core import get_available_models
        data = []
        available = get_available_models()
        
        # Use fetched models if available
        models_to_list = available if available else SUPPORTED_MODELS

        for model_id in models_to_list:
            data.append({
                'id': model_id,
                'object': 'model',
                'created': 1770652800,
                'owned_by': 'antigravity',
            })
        # Add common aliases too
        for alias in sorted(MODEL_MAPPING.keys()):
            if alias not in models_to_list:
                data.append({
                    'id': alias,
                    'object': 'model',
                    'created': 1770652800,
                    'owned_by': 'antigravity',
                })
        self._send_json(200, {'object': 'list', 'data': data})

    def _handle_chat_completions(self):
        """Handle /v1/chat/completions requests with retry and load balancing."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body_bytes = self.rfile.read(content_length)
            body = json.loads(body_bytes)
        except Exception as e:
            self._send_json(400, {'error': {'message': f'Invalid request body: {e}', 'type': 'invalid_request_error'}})
            return

        model_name = body.get('model', 'gemini-3-flash')
        target_model = resolve_model(model_name)
        is_stream = body.get('stream', False)

        MAX_TRIES = 3
        last_error = None
        
        # Check if a specific account is requested via header
        force_account_email = self.headers.get('X-AGPM-Account')

        for attempt in range(MAX_TRIES):
            # Get account
            if force_account_email:
                accounts = get_accounts()
                account = next((a for a in accounts if a['email'] == force_account_email), None)
                if not account:
                    self._send_json(404, {'error': {'message': f'Forced account {force_account_email} not found', 'type': 'not_found'}})
                    return
                # If forcing, we only try once
                MAX_TRIES = 1
            else:
                account = _get_next_account()

            if not account:
                if attempt == 0:
                    self._send_json(503, {'error': {'message': 'No accounts available. Add accounts in the dashboard first.', 'type': 'server_error'}})
                else:
                    self._send_json(last_error['status'], last_error['body'])
                return

            # Get access token
            access_token = _get_valid_access_token(account)
            if not access_token:
                last_error = {'status': 503, 'body': {'error': {'message': f'Failed to get access token for {account["email"]}', 'type': 'auth_error'}}}
                continue  # Try next account

            # Convert to Gemini internal format
            project_id = account.get('quota', {}).get('project_id', '')
            gemini_body = convert_openai_to_gemini_internal(body, target_model, project_id)

            if is_stream:
                success, err = self._handle_streaming(gemini_body, access_token, model_name, account)
            else:
                success, err = self._handle_non_streaming(gemini_body, access_token, model_name, account)

            if success:
                return  # Request was successfully handled

            # If not success, save error and loop to try next account
            last_error = err

        # If we exhausted all tries, send the last error
        if last_error:
            self._send_json(last_error['status'], last_error['body'])

    def _handle_non_streaming(self, gemini_body: dict, access_token: str, model_name: str, account: dict) -> tuple[bool, dict | None]:
        """Non-streaming: call generateContent with endpoint failover. Returns (success, error_data)."""
        last_err = None
        
        for base_url in INTERNAL_BASE_URLS:
            url = f'{base_url}:generateContent'
            try:
                kwargs = get_httpx_kwargs()
                kwargs['timeout'] = 120.0
                resp = httpx.post(
                    url,
                    json=gemini_body,
                    headers={
                        'Authorization': f'Bearer {access_token}',
                        'Content-Type': 'application/json',
                        'User-Agent': USER_AGENT,
                    },
                    **kwargs,
                )

                if resp.status_code != 200:
                    error_text = resp.text[:500]
                    if resp.status_code == 429:
                        mark_account_cooldown(account['email'], 120)
                    
                    err = {
                        'status': resp.status_code,
                        'body': {
                            'error': {
                                'message': f'Upstream error ({account["email"]}): {error_text}',
                                'type': 'upstream_error',
                            }
                        }
                    }
                    # Failover on 429 or 5xx
                    if resp.status_code == 429 or resp.status_code >= 500:
                        last_err = err
                        continue
                    return False, err

                gemini_resp = resp.json()
                # Unwrap if nested in 'response' key
                if 'response' in gemini_resp and 'candidates' not in gemini_resp:
                    gemini_resp = gemini_resp['response']

                openai_resp = convert_gemini_to_openai(gemini_resp, model_name)
                self._send_json(200, openai_resp)
                return True, None

            except Exception as e:
                err = {'status': 502, 'body': {'error': {'message': f'Upstream request failed: {e}', 'type': 'upstream_error'}}}
                last_err = err
                continue  # Try next endpoint
        
        return False, last_err

    def _handle_streaming(self, gemini_body: dict, access_token: str, model_name: str, account: dict) -> tuple[bool, dict | None]:
        """Streaming: call streamGenerateContent with endpoint failover. Returns (success, error_data)."""
        last_err = None
        
        for base_url in INTERNAL_BASE_URLS:
            url = f'{base_url}:streamGenerateContent?alt=sse'
            try:
                kwargs = get_httpx_kwargs()
                kwargs['timeout'] = 120.0
                with httpx.stream(
                    'POST',
                    url,
                    json=gemini_body,
                    headers={
                        'Authorization': f'Bearer {access_token}',
                        'Content-Type': 'application/json',
                        'User-Agent': USER_AGENT,
                    },
                    **kwargs,
                ) as resp:
                    if resp.status_code != 200:
                        error_text = resp.read().decode()[:500]
                        if resp.status_code == 429:
                            mark_account_cooldown(account['email'], 120)
                        
                        err = {
                            'status': resp.status_code,
                            'body': {
                                'error': {
                                    'message': f'Upstream stream error ({account["email"]}): {error_text}',
                                    'type': 'upstream_error',
                                }
                            }
                        }
                        # Failover on 429 or 5xx
                        if resp.status_code == 429 or resp.status_code >= 500:
                            last_err = err
                            break  # Try next endpoint
                        return False, err

                    # Send SSE headers
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Connection', 'keep-alive')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()

                    completion_id = f'chatcmpl-{uuid.uuid4().hex[:12]}'
                    buffer = ''

                    for chunk in resp.iter_text():
                        buffer += chunk
                        lines = buffer.split('\n')
                        buffer = lines.pop()

                        for line in lines:
                            stripped = line.strip()
                            if not stripped.startswith('data: '):
                                continue
                            data_str = stripped[6:]
                            if data_str == '[DONE]':
                                continue

                            try:
                                gemini_chunk = json.loads(data_str)
                                candidates = gemini_chunk.get('candidates', [])
                                if not candidates:
                                    continue

                                candidate = candidates[0]
                                parts = candidate.get('content', {}).get('parts', [])
                                text = ''.join(
                                    p.get('text', '') for p in parts
                                    if 'text' in p and not p.get('thought')
                                )

                                if not text:
                                    continue

                                openai_chunk = {
                                    'id': completion_id,
                                    'object': 'chat.completion.chunk',
                                    'created': int(time.time()),
                                    'model': model_name,
                                    'choices': [{
                                        'index': 0,
                                        'delta': {'content': text},
                                        'finish_reason': None,
                                    }],
                                }
                                sse_line = f'data: {json.dumps(openai_chunk)}\n\n'
                                self.wfile.write(sse_line.encode())
                                self.wfile.flush()
                            except json.JSONDecodeError:
                                continue

                    # Send final [DONE]
                    final_chunk = {
                        'id': completion_id,
                        'object': 'chat.completion.chunk',
                        'created': int(time.time()),
                        'model': model_name,
                        'choices': [{
                            'index': 0,
                            'delta': {},
                            'finish_reason': 'stop',
                        }],
                    }
                    self.wfile.write(f'data: {json.dumps(final_chunk)}\n\n'.encode())
                    self.wfile.write(b'data: [DONE]\n\n')
                    self.wfile.flush()
                    
                    return True, None

            except Exception as e:
                err = {'status': 502, 'body': {'error': {'message': f'Stream failed: {e}', 'type': 'upstream_error'}}}
                last_err = err
                continue  # Try next endpoint
        
        return False, last_err

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()

    def log_message(self, format, *args):
        """Custom log format."""
        print(f"[Proxy] {args[0]}")


# --- Server Control ---

_server: HTTPServer | None = None
_server_thread: threading.Thread | None = None


def start_proxy(port: int = 8050) -> str:
    """Start the proxy server in a background thread. Returns status message."""
    global _server, _server_thread

    if _server is not None:
        return f"Proxy already running on port {_server.server_address[1]}"

    # Rebuild account cycle
    _build_account_cycle()

    try:
        _server = HTTPServer(('0.0.0.0', port), ProxyHandler)
        _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
        _server_thread.start()
        return f"Proxy started on http://127.0.0.1:{port}"
    except OSError as e:
        _server = None
        return f"Failed to start proxy: {e}"


def stop_proxy() -> str:
    """Stop the proxy server."""
    global _server, _server_thread

    if _server is None:
        return "Proxy is not running"

    _server.shutdown()
    _server = None
    _server_thread = None
    return "Proxy stopped"


def is_proxy_running() -> bool:
    return _server is not None


def get_proxy_port() -> int:
    if _server:
        return _server.server_address[1]
    return 0


def reload_accounts():
    """Reload the account cycle from DB."""
    _build_account_cycle()


def main():
    import argparse
    from core import get_proxy_config

    parser = argparse.ArgumentParser(description="AGPM Proxy Server")
    parser.add_argument("--port", type=int, help="Port to run the proxy on")
    args = parser.parse_args()

    config = get_proxy_config()
    port = args.port or config.get("port", 8050)

    print(f"[*] Starting AGPM Proxy on port {port}...")
    # Rebuild account cycle
    _build_account_cycle()

    try:
        server = HTTPServer(("0.0.0.0", port), ProxyHandler)
        print(f"[*] Proxy is ready at http://0.0.0.0:{port}")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Proxy stopping...")
    except Exception as e:
        print(f"[!] Proxy error: {e}")


if __name__ == "__main__":
    main()
