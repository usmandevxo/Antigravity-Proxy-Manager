from flask import Flask, jsonify, request, render_template, send_from_directory, session, redirect, url_for, Response, stream_with_context
from werkzeug.middleware.proxy_fix import ProxyFix
import urllib.parse
import core
import proxy
import os
import sys
import subprocess
import httpx
from pathlib import Path
import socket
from functools import wraps
import json
import uuid
import time
import itertools
import threading

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
portal_cfg = core.get_portal_config()
app.secret_key = portal_cfg['secret_key']

# Session cookie configuration for domain/reverse proxy access
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
# If serving over HTTPS, set SESSION_COOKIE_SECURE = True
public_url = portal_cfg.get('public_url', '')
if public_url.startswith('https://'):
    app.config['SESSION_COOKIE_SECURE'] = True
else:
    app.config['SESSION_COOKIE_SECURE'] = False

@app.before_request
def log_request_info():
    if not request.path.startswith('/static'):
        print(f"[*] Request: {request.method} {request.path} | Cookie: {'session' in request.cookies}")

def is_port_in_use(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', port)) == 0
    except Exception:
        return False

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            # For API requests, return JSON instead of redirecting to login page
            if request.path.startswith('/d-api/'):
                return jsonify({'success': False, 'error': 'Unauthorized', 'login_required': True}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_redirect_uri():
    """Dynamically determine the OAuth redirect URI."""
    portal_cfg = core.get_portal_config()
    public_url = portal_cfg.get('public_url', '').strip()
    
    if public_url:
        if not public_url.startswith(('http://', 'https://')):
            public_url = 'http://' + public_url
            
        parsed = urllib.parse.urlparse(public_url)
        scheme = parsed.scheme or 'http'
        host = parsed.netloc or parsed.path.split('/')[0]
        
        # If the user provided a port in public_url, use it. 
        # Otherwise, default to 5005 which is where the callback server listens.
        if ':' in host:
            final_uri = f"{scheme}://{host}".rstrip('/')
        else:
            final_uri = f"{scheme}://{host}:5005"
            
        print(f"[*] Generated Redirect URI: {final_uri} (Source: public_url settings)")
        return final_uri
    
    # Fallback to detecting from request
    # Respect X-Forwarded-Proto for proxies, fallback to request.scheme
    scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
    # request.host includes the port, we want to strip it and use 5005
    host_only = request.host.split(':')[0]
    final_uri = f"{scheme}://{host_only}:5005"
    print(f"[*] Generated Redirect URI: {final_uri} (Source: Request Detection)")
    return final_uri

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        admin_user, admin_pass = core.get_admin_creds()
        
        if username == admin_user and password == admin_pass:
            session['logged_in'] = True
            cfg = core.get_portal_config()
            return redirect(url_for('dashboard', slug=cfg['admin_slug']))
        else:
            return render_template('login.html', error='Invalid credentials')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/start-login')
def direct_login():
    """Directly start the OAuth flow without opening the panel."""
    redirect_uri = get_redirect_uri()
    auth_url, port = core.get_oauth_url_only(auto_save=True, redirect_uri=redirect_uri)
    return redirect(auth_url)

@app.route('/')
def root_redirect():
    if 'logged_in' in session:
        cfg = core.get_portal_config()
        return redirect(url_for('dashboard', slug=cfg['admin_slug']))
    return redirect(url_for('login'))

@app.route('/<slug>')
@login_required
def dashboard(slug):
    cfg = core.get_portal_config()
    if slug != cfg['admin_slug']:
        return redirect(url_for('dashboard', slug=cfg['admin_slug']))
    return render_template('index.html', admin_slug=cfg['admin_slug'], host_url=request.host_url.rstrip('/'), portal_port=cfg['port'])

@app.route('/d-api/accounts', methods=['GET'])
@app.route('/d-api/accounts/', methods=['GET'])
@login_required
def get_accounts():
    try:
        accounts = core.get_accounts()
        for acc in accounts:
            # Strip sensitive tokens from frontend response
            acc.pop('_refresh_token', None)
            acc.pop('refresh_token', None)
            acc.pop('access_token', None)
            acc.pop('quota_json', None)
        return jsonify({'accounts': accounts})
    except Exception as e:
        print(f"[!] Critical error in get_accounts: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'accounts': [], 'error': 'Database or internal error', 'details': str(e)}), 500

@app.route('/d-api/accounts/oauth/start', methods=['POST'])
@app.route('/d-api/accounts/oauth/start/', methods=['POST'])
@login_required
def oauth_start():
    # Always use 127.0.0.1:5005 — this is the URI registered in Google Cloud Console.
    # The callback server runs locally; if the user is on a remote machine,
    # they copy the "code" from the failed redirect URL and paste it manually.
    redirect_uri = f'http://127.0.0.1:{core.OAUTH_PORT}'
    print(f"[*] OAuth Start | Redirect URI: {redirect_uri}")
    auth_url, port = core.get_oauth_url_only(auto_save=True, redirect_uri=redirect_uri)
    return jsonify({'auth_url': auth_url, 'port': port, 'redirect_uri': redirect_uri})

@app.route('/d-api/accounts/oauth/callback', methods=['POST'])
@login_required
def oauth_manual_callback():
    """Exchange an auth code manually (for remote/domain users who can't auto-redirect)."""
    data = request.json
    code = data.get('code')
    if not code:
        return jsonify({'success': False, 'error': 'Missing code'}), 400
    
    # Must use the same redirect_uri that was sent to Google
    redirect_uri = f'http://127.0.0.1:{core.OAUTH_PORT}'
    
    tokens = core._exchange_code_for_tokens(code, redirect_uri)
    if not tokens or 'refresh_token' not in tokens:
        return jsonify({'success': False, 'error': 'Failed to exchange code. It may have expired — try again.'})
    
    refresh_token = tokens['refresh_token']
    access_token = tokens.get('access_token', '')
    
    email, name, project_id = '', '', ''
    if access_token:
        user_info = core.fetch_user_info(access_token)
        if user_info:
            email = user_info.get('email', '')
            name = user_info.get('name', '')
        project_id = core.fetch_project_id(access_token)
    
    if not email:
        return jsonify({'success': False, 'error': 'Could not retrieve account email from Google'})
    
    test_success, test_msg, verification_url = core._test_gemini_connection(access_token, project_id)
    status_val = 'active' if test_success else 'rejected'
    added = core.add_account(email, refresh_token, name=name, project_id=project_id, status=status_val)
    
    return jsonify({
        'success': True,
        'added': added,
        'email': email,
        'name': name,
        'test_success': test_success,
        'verification_url': verification_url,
        'error': 'Account already exists' if not added else None
    })

@app.route('/d-api/accounts/oauth/check/<int:port>', methods=['GET'])
@login_required
def oauth_check(port):
    res = core.check_oauth_result(port)
    return jsonify(res)

@app.route('/d-api/accounts/manual', methods=['POST'])
@login_required
def add_manual():
    data = request.json
    email = data.get('email')
    token = data.get('token')
    proxy_url = data.get('proxy_url', '')
    if not email or not token:
        return jsonify({'success': False, 'error': 'Missing email or token'}), 400
    added = core.add_account(email, token, proxy_url=proxy_url)
    return jsonify({'success': added, 'error': 'Account already exists' if not added else None})

@app.route('/d-api/accounts/<email>', methods=['DELETE'])
@login_required
def remove_account(email):
    success = core.remove_account(email)
    return jsonify({'success': success})

@app.route('/d-api/accounts/<email>/refresh', methods=['POST'])
@login_required
def refresh_quota(email):
    msg = core.refresh_account_quota(email)
    success = "OK" in msg
    return jsonify({'success': success, 'message': msg})

@app.route('/d-api/accounts/<email>/verify', methods=['POST'])
@login_required
def verify_account(email):
    try:
        accounts = core.get_accounts()
        target = next((a for a in accounts if a['email'] == email), None)
        if not target:
            return jsonify({'success': False, 'message': f'Account {email} not found'}), 404

        rt = target.get('_refresh_token')
        if not rt:
            return jsonify({'success': False, 'message': f'No refresh token for {email}'}), 400

        tokens = core.refresh_access_token(rt)
        if not tokens or 'access_token' not in tokens:
            return jsonify({'success': False, 'message': f'Failed to refresh access token for {email}'}), 500

        access_token = tokens['access_token']
        project_id = target.get('quota', {}).get('project_id', '')
        if not project_id:
            project_id = core.fetch_project_id(access_token)

        test_success, test_msg, verification_url = core._test_gemini_connection(access_token, project_id)
        status_val = 'active' if test_success else 'rejected'

        # Update status in DB
        conn = core._get_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE accounts SET status = ?, last_used = ? WHERE email = ?", (status_val, int(time.time() * 1000), email))
        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'test_success': test_success,
            'verification_url': verification_url,
            'message': test_msg
        })
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'message': f'Exception: {str(e)}', 'traceback': traceback.format_exc()}), 500

@app.route('/d-api/models', methods=['GET'])
@app.route('/d-api/models/', methods=['GET'])
@login_required
def get_models():
    models = core.get_available_models()
    return jsonify({
        'models': models,
        'mapping': proxy.MODEL_MAPPING
    })

# --- Consolidated Proxy Routes ---

@app.route('/v1/models', methods=['GET'])
@app.route('/v1/models/', methods=['GET'])
def v1_models():
    """OpenAI-compatible models list."""
    available = core.get_available_models()
    models_to_list = available if available else proxy.SUPPORTED_MODELS
    
    models = core.KNOWN_MODELS
    data = []
    for m_id, provider in models:
        data.append({
            'id': m_id,
            'object': 'model',
            'created': int(time.time()),
            'owned_by': provider.lower()
        })
    return jsonify({'object': 'list', 'data': data})

@app.route('/v1/chat/completions', methods=['POST', 'OPTIONS'])
@app.route('/v1/chat/completions/', methods=['POST', 'OPTIONS'])
def v1_chat_completions():
    """OpenAI-compatible chat completions with streaming support."""
    if request.method == 'OPTIONS':
        return '', 204
        
    try:
        body = request.get_json()
    except Exception as e:
        return jsonify({'error': {'message': f'Invalid request body: {e}', 'type': 'invalid_request_error'}}), 400

    model_name = body.get('model', 'gemini-3-flash')
    target_model = proxy.resolve_model(model_name)
    is_stream = body.get('stream', False)
    
    # Load balancing / Account selection
    force_account_email = request.headers.get('X-AGPM-Account')
    
    def get_account_to_use():
        if force_account_email:
            accounts = core.get_accounts()
            return next((a for a in accounts if a['email'] == force_account_email), None)
        return proxy._get_next_account()

    MAX_TRIES = 3
    last_error = None
    
    if force_account_email:
        MAX_TRIES = 1

    for attempt in range(MAX_TRIES):
        account = get_account_to_use()
        if not account:
            if attempt == 0:
                return jsonify({'error': {'message': 'No accounts available. Add accounts in the dashboard first.', 'type': 'server_error'}}), 503
            break
            
        access_token = proxy._get_valid_access_token(account)
        if not access_token:
            last_error = (jsonify({'error': {'message': f'Failed to get access token for {account["email"]}', 'type': 'auth_error'}}), 503)
            continue

        project_id = account.get('quota', {}).get('project_id', '')
        gemini_body = proxy.convert_openai_to_gemini_internal(body, target_model, project_id)
        
        if is_stream:
            return handle_stream_request(gemini_body, access_token, model_name, account)
        else:
            success, err_data = handle_non_stream_request(gemini_body, access_token, model_name, account)
            if success:
                return jsonify(err_data) # actually contains the success data
            last_error = (jsonify(err_data['body']), err_data['status'])
            
    if last_error:
        return last_error
    return jsonify({'error': {'message': 'Request failed after multiple attempts', 'type': 'server_error'}}), 500

def handle_non_stream_request(gemini_body, access_token, model_name, account):
    last_err = None
    for base_url in proxy.INTERNAL_BASE_URLS:
        url = f'{base_url}:generateContent'
        try:
            kwargs = core.get_httpx_kwargs()
            kwargs['timeout'] = 120.0
            resp = httpx.post(
                url,
                json=gemini_body,
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json',
                    'User-Agent': proxy.USER_AGENT,
                },
                **kwargs,
            )
            if resp.status_code != 200:
                if resp.status_code == 429:
                    proxy.mark_account_cooldown(account['email'], 120)
                err = {'status': resp.status_code, 'body': {'error': {'message': f'Upstream error: {resp.text[:500]}', 'type': 'upstream_error'}}}
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_err = err
                    continue
                return False, err
            
            gemini_resp = resp.json()
            if 'response' in gemini_resp and 'candidates' not in gemini_resp:
                gemini_resp = gemini_resp['response']
            return True, proxy.convert_gemini_to_openai(gemini_resp, model_name)
        except Exception as e:
            last_err = {'status': 502, 'body': {'error': {'message': str(e), 'type': 'upstream_error'}}}
            continue
    return False, last_err

def handle_stream_request(gemini_body, access_token, model_name, account):
    def generate():
        completion_id = f'chatcmpl-{uuid.uuid4().hex[:12]}'
        last_err = None
        for base_url in proxy.INTERNAL_BASE_URLS:
            url = f'{base_url}:streamGenerateContent?alt=sse'
            try:
                kwargs = core.get_httpx_kwargs()
                kwargs['timeout'] = 120.0
                with httpx.stream(
                    'POST',
                    url,
                    json=gemini_body,
                    headers={
                        'Authorization': f'Bearer {access_token}',
                        'Content-Type': 'application/json',
                        'User-Agent': proxy.USER_AGENT,
                    },
                    **kwargs,
                ) as resp:
                    if resp.status_code != 200:
                        if resp.status_code == 429:
                            proxy.mark_account_cooldown(account['email'], 120)
                        err_msg = 'Upstream stream error'
                        if resp.status_code == 429 or resp.status_code >= 500:
                            last_err = err_msg
                            break
                        yield f"data: {json.dumps({'error': {'message': err_msg, 'type': 'upstream_error'}})}\n\n"
                        return

                    buffer = ''
                    for chunk in resp.iter_text():
                        buffer += chunk
                        lines = buffer.split('\n')
                        buffer = lines.pop()
                        for line in lines:
                            stripped = line.strip()
                            if not stripped.startswith('data: '): continue
                            data_str = stripped[6:]
                            if data_str == '[DONE]': continue
                            try:
                                gemini_chunk = json.loads(data_str)
                                candidates = gemini_chunk.get('candidates', [])
                                if not candidates: continue
                                text = ''.join(p.get('text', '') for p in candidates[0].get('content', {}).get('parts', []) if 'text' in p and not p.get('thought'))
                                if not text: continue
                                openai_chunk = {
                                    'id': completion_id, 'object': 'chat.completion.chunk', 'created': int(time.time()),
                                    'model': model_name, 'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}]
                                }
                                yield f"data: {json.dumps(openai_chunk)}\n\n"
                            except: continue
                    final_chunk = {
                        'id': completion_id, 'object': 'chat.completion.chunk', 'created': int(time.time()),
                        'model': model_name, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]
                    }
                    yield f"data: {json.dumps(final_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                    return  # Success, don't try next endpoint
            except Exception as e:
                last_err = str(e)
                continue
        # All endpoints failed
        yield f"data: {json.dumps({'error': {'message': f'All endpoints failed: {last_err}', 'type': 'upstream_error'}})}\n\n"

    return Response(stream_with_context(generate()), content_type='text/event-stream')

@app.route('/d-api/terminal-command/<email>')
@login_required
def get_terminal_command(email):
    accounts = core.get_accounts()
    target = next((a for a in accounts if a['email'] == email), None)
    if not target:
        return jsonify({'success': False, 'error': 'Account not found'}), 404
        
    rt = target.get('_refresh_token')
    if not rt:
        return jsonify({'success': False, 'error': 'Refresh token not found'}), 400
        
    command = core.get_terminal_login_command(email, rt)
    return jsonify({'success': True, 'command': command})

@app.route('/d-api/accounts/<email>/status', methods=['POST'])
@login_required
def update_account_status(email):
    data = request.json
    status = data.get('status')
    if not status or status not in ['active', 'rejected', 'logged_out']:
        return jsonify({'success': False, 'message': 'Invalid status'}), 400
        
    conn = core._get_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE accounts SET status = ? WHERE email = ?", (status, email))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': f'Status updated to {status}'})

@app.route('/d-api/models/fetch', methods=['POST'])
@login_required
def fetch_models():
    models = core.get_available_models(force=True)
    if models:
        return jsonify({'success': True, 'models': models, 'message': f'Successfully fetched {len(models)} models.'})
    return jsonify({'success': False, 'message': 'Failed to fetch models. Make sure you have at least one active account.'})

@app.route('/d-api/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'GET':
        config = core.load_config()
        portal_config = core.get_portal_config()
        admin_user, _ = core.get_admin_creds()
        proxy_config = config.get('proxy', {})
        return jsonify({
            'proxy_port': proxy_config.get('port', config.get('proxy_port', 8050)),
            'upstream_proxy': proxy_config.get('upstream_proxy', {}).get('url', config.get('upstream_proxy', '')),
            'proxy_auto_start': proxy_config.get('auto_start', True),
            'portal_port': portal_config['port'],
            'admin_slug': portal_config['admin_slug'],
            'public_url': portal_config.get('public_url', ''),
            'admin_username': admin_user
        })
    else:
        data = request.json
        config = core.load_config()
        
        if 'proxy' not in config:
            config['proxy'] = {}
        
        if 'proxy_port' in data:
            config['proxy']['port'] = int(data['proxy_port'])
            config['proxy_port'] = int(data['proxy_port']) # legacy fallback
        if 'upstream_proxy' in data:
            if 'upstream_proxy' not in config['proxy']:
                config['proxy']['upstream_proxy'] = {}
            config['proxy']['upstream_proxy']['url'] = data['upstream_proxy']
            config['proxy']['upstream_proxy']['enabled'] = bool(data['upstream_proxy'])
            config['upstream_proxy'] = data['upstream_proxy'] # legacy fallback
        if 'proxy_auto_start' in data:
            config['proxy']['auto_start'] = bool(data['proxy_auto_start'])
            
        core.save_config(config)
        
        portal_port = data.get('portal_port', core.get_portal_config()['port'])
        admin_slug = data.get('admin_slug', core.get_portal_config()['admin_slug'])
        public_url = data.get('public_url', core.get_portal_config().get('public_url', ''))
        core.save_portal_config(int(portal_port), admin_slug, public_url)
            
        if 'admin_username' in data and 'admin_password' in data:
            if data['admin_username'] and data['admin_password']:
                core.save_admin_creds(data['admin_username'], data['admin_password'])
                
        return jsonify({'success': True})

@app.route('/d-api/system/restart', methods=['POST'])
@login_required
def system_restart():
    """Restart the AGPM service via systemctl."""
    try:
        # We use a thread to delay the restart so the response can be sent
        def delayed_restart():
            time.sleep(1)
            subprocess.run(["systemctl", "--user", "restart", "agpm-web.service"])
            
        threading.Thread(target=delayed_restart).start()
        return jsonify({'success': True, 'message': 'System is restarting...'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/debug')
def debug_status():
    try:
        admin_user, _ = core.get_admin_creds()
        accounts = core.get_accounts()
        portal_cfg = core.get_portal_config()
        db_exists = os.path.exists(core.DB_PATH)
        return jsonify({
            'session': dict(session),
            'admin_user': admin_user,
            'account_count': len(accounts),
            'db_path': core.DB_PATH,
            'db_exists': db_exists,
            'portal_port': portal_cfg['port'],
            'secret_key_loaded': bool(app.secret_key)
        })
    except Exception as e:
        return jsonify({'error': str(e), 'trace': 'Check server logs'})

@app.route('/d-api/service/status', methods=['GET'])
@login_required
def service_status():
    try:
        result = subprocess.run(["systemctl", "--user", "is-enabled", "agpm-web.service"], capture_output=True, text=True)
        enabled = result.returncode == 0
        installed = True
        if "No such file or directory" in result.stderr or "Unit files" in result.stderr or "could not be found" in result.stderr:
            installed = False
        return jsonify({'installed': installed, 'enabled': enabled})
    except Exception:
        return jsonify({'installed': False, 'enabled': False})

@app.route('/d-api/service/toggle', methods=['POST'])
@login_required
def service_toggle():
    data = request.json
    enable = data.get('enable', True)
    try:
        if enable:
            service_dir = Path.home() / ".config" / "systemd" / "user"
            service_dir.mkdir(parents=True, exist_ok=True)
            service_file = service_dir / "agpm-web.service"
            
            python_exec = sys.executable
            web_script = os.path.abspath(__file__)
            cwd = os.path.dirname(web_script)

            service_content = f"""[Unit]
Description=AGPM Web Portal by Usman
After=network.target

[Service]
Type=simple
WorkingDirectory="{cwd}"
ExecStart="{python_exec}" "{web_script}"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
            with open(service_file, "w") as f:
                f.write(service_content)
                
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "--user", "enable", "agpm-web.service"], check=True)
            msg = "Web portal will automatically start on boot."
        else:
            subprocess.run(["systemctl", "--user", "disable", "agpm-web.service"], check=True)
            msg = "Auto-start on boot disabled."
        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/d-api/proxy/status', methods=['GET'])
@app.route('/d-api/proxy/status/', methods=['GET'])
@login_required
def proxy_status():
    portal_cfg = core.get_portal_config()
    port = portal_cfg['port']
    
    return jsonify({
        'running_internal': True,
        'port_used': True,
        'port': port
    })

@app.route('/d-api/proxy/test', methods=['POST'])
@login_required
def proxy_test():
    data = request.json
    email = data.get('email')
    prompt = data.get('prompt', 'Hello!')
    model = data.get('model', 'gemini-3-flash')
    
    if not email:
        return jsonify({'success': False, 'message': 'Missing email'}), 400
        
    config = core.load_config()
    proxy_port = config.get('proxy_port', 8050)
    
    if not is_port_in_use(proxy_port):
        return jsonify({'success': False, 'message': 'Proxy server is not running. Please start it first.'}), 400
        
    try:
        proxy_url = f"http://127.0.0.1:{proxy_port}"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False
        }
        headers = {
            "Content-Type": "application/json",
            "X-AGPM-Account": email  # Force use of this specific account
        }
        
        # Make request to the local proxy
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(f"{proxy_url}/v1/chat/completions", json=payload, headers=headers)
            
            if resp.is_success:
                return jsonify({'success': True, 'response': resp.json()})
            else:
                return jsonify({'success': False, 'message': f"Proxy error ({resp.status_code}): {resp.text}"})
                
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})



def main():
    portal_config = core.get_portal_config()
    proxy_config = core.get_proxy_config()
    
    print(f"[*] Starting AGPM Unified Server on port {portal_config['port']}...")
    app.run(host='0.0.0.0', port=portal_config['port'], debug=False)


if __name__ == '__main__':
    main()
