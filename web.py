from flask import Flask, jsonify, request, render_template, send_from_directory, session, redirect, url_for
import core
import proxy
import os
import sys
import subprocess
import httpx
from pathlib import Path
import socket
from functools import wraps

app = Flask(__name__)
portal_cfg = core.get_portal_config()
app.secret_key = portal_cfg['secret_key']

# Ensure Flask trusts proxy headers (X-Forwarded-For, etc.)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

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
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

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
    auth_url, port = core.get_oauth_url_only(auto_save=True)
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
    return render_template('index.html', admin_slug=cfg['admin_slug'])

@app.route('/api/accounts', methods=['GET'])
@login_required
def get_accounts():
    accounts = core.get_accounts()
    for acc in accounts:
        acc.pop('_refresh_token', None)
        acc.pop('refresh_token', None)
        acc.pop('access_token', None)
        acc.pop('quota_json', None)
    return jsonify({'accounts': accounts})

@app.route('/api/accounts/oauth/start', methods=['POST'])
@login_required
def oauth_start():
    auth_url, port = core.get_oauth_url_only(auto_save=True)
    return jsonify({'auth_url': auth_url, 'port': port})

@app.route('/api/accounts/oauth/check/<int:port>', methods=['GET'])
@login_required
def oauth_check(port):
    res = core.check_oauth_result(port)
    return jsonify(res)

@app.route('/api/accounts/manual', methods=['POST'])
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

@app.route('/api/accounts/<email>', methods=['DELETE'])
@login_required
def remove_account(email):
    success = core.remove_account(email)
    return jsonify({'success': success})

@app.route('/api/accounts/<email>/refresh', methods=['POST'])
@login_required
def refresh_quota(email):
    msg = core.refresh_account_quota(email)
    success = "OK" in msg
    return jsonify({'success': success, 'message': msg})

@app.route('/api/models', methods=['GET'])
@login_required
def get_models():
    models = core.get_available_models()
    return jsonify({
        'models': models,
        'mapping': proxy.MODEL_MAPPING
    })

@app.route('/api/models/fetch', methods=['POST'])
@login_required
def fetch_models():
    models = core.get_available_models(force=True)
    if models:
        return jsonify({'success': True, 'models': models, 'message': f'Successfully fetched {len(models)} models.'})
    return jsonify({'success': False, 'message': 'Failed to fetch models. Make sure you have at least one active account.'})

@app.route('/api/settings', methods=['GET', 'POST'])
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
        core.save_portal_config(int(portal_port), admin_slug)
            
        if 'admin_username' in data and 'admin_password' in data:
            if data['admin_username'] and data['admin_password']:
                core.save_admin_creds(data['admin_username'], data['admin_password'])
                
        return jsonify({'success': True})

@app.route('/api/service/status', methods=['GET'])
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

@app.route('/api/service/toggle', methods=['POST'])
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


@app.route('/api/proxy/status', methods=['GET'])
@login_required
def proxy_status():
    config = core.load_config()
    port = config.get('proxy_port', 8050)
    is_running_internal = proxy.is_proxy_running()
    port_used = is_port_in_use(port)
    
    return jsonify({
        'running_internal': is_running_internal,
        'port_used': port_used,
        'port': port
    })

@app.route('/api/proxy/start', methods=['POST'])
@login_required
def proxy_start():
    config = core.load_config()
    port = config.get('proxy_port', 8050)
    if is_port_in_use(port):
        return jsonify({'success': False, 'message': f'Port {port} is already in use.'}), 400
    msg = proxy.start_proxy(port)
    return jsonify({'success': proxy.is_proxy_running(), 'message': msg})

@app.route('/api/proxy/stop', methods=['POST'])
@login_required
def proxy_stop():
    msg = proxy.stop_proxy()
    return jsonify({'success': not proxy.is_proxy_running(), 'message': msg})

@app.route('/api/proxy/test', methods=['POST'])
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



if __name__ == '__main__':
    portal_config = core.get_portal_config()
    proxy_config = core.get_proxy_config()
    
    # Auto-start proxy if enabled
    if proxy_config.get('auto_start', True):
        print(f"[*] Auto-starting proxy on port {proxy_config['port']}...")
        if not is_port_in_use(proxy_config['port']):
            msg = proxy.start_proxy(proxy_config['port'])
            print(f"[*] {msg}")
        else:
            print(f"[!] Proxy port {proxy_config['port']} is already in use.")

    print(f"[*] Starting AGPM Web Portal on port {portal_config['port']}...")
    app.run(host='0.0.0.0', port=portal_config['port'], debug=False)
