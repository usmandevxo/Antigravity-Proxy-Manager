"""
Kiro Web Portal - Administrative Interface for Kiro AI.
Inspired by AGPM Web.
"""

from flask import Flask, jsonify, request, render_template, session, redirect, url_for, Response, stream_with_context
from werkzeug.middleware.proxy_fix import ProxyFix
from functools import wraps
import os
import sys
import uuid
import time
import datetime
import json
import httpx
import core

MODEL_MAPPING = {
    # OpenAI-style aliases -> CodeWhisperer short model IDs
    'claude-sonnet-4.5': 'claude-sonnet-4.5',
    'claude-sonnet-4': 'claude-sonnet-4',
    'claude-haiku-4.5': 'claude-haiku-4.5',
    'claude-3-5-sonnet': 'claude-sonnet-4.5',
    'claude-3-5-sonnet-latest': 'claude-sonnet-4.5',
    'claude-3-5-sonnet-20241022': 'claude-sonnet-4.5',
    'claude-3-5-sonnet-20240620': 'claude-sonnet-4.5',
    'claude-3-opus': 'claude-sonnet-4',
    'claude-3-opus-20240229': 'claude-sonnet-4',
    'claude-3-haiku': 'claude-haiku-4.5',
    'claude-3-5-haiku-20241022': 'claude-haiku-4.5',
    'gpt-4o': 'claude-sonnet-4.5',
    'gpt-4': 'claude-sonnet-4',
    'gpt-3.5-turbo': 'claude-haiku-4.5',
    'gemini-1.5-pro': 'claude-sonnet-4.5',
    'gemini-1.5-flash': 'claude-haiku-4.5',
    'gemini-2.0-pro': 'claude-sonnet-4.5',
    'gemini-2.0-flash': 'claude-haiku-4.5',
    'gemini-2.5-pro': 'claude-sonnet-4.5',
    'gemini-exp-1206': 'claude-sonnet-4.5',
    'qwen': 'qwen3-coder-next',
    'qwen-coder': 'qwen3-coder-next',
    'qwen3-coder-next': 'qwen3-coder-next',
    'deepseek': 'deepseek-3.2',
    'deepseek-3.2': 'deepseek-3.2',
    'minimax': 'minimax-m2.5',
    'glm': 'glm-5',
    'glm-5': 'glm-5',
    'auto': 'auto'
}

app = Flask(__name__, template_folder='templates', static_folder='static')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

portal_cfg = core.get_portal_config()
app.secret_key = portal_cfg['secret_key']

app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

public_url = portal_cfg.get('public_url', '')
if public_url.startswith('https://'):
    app.config['SESSION_COOKIE_SECURE'] = True
else:
    app.config['SESSION_COOKIE_SECURE'] = False


@app.before_request
def log_request_info():
    if not request.path.startswith('/static'):
        print(f"[*] Kiro Request: {request.method} {request.path} | Cookie: {'session' in request.cookies}")


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            if request.path.startswith('/d-api/'):
                return jsonify({'success': False, 'error': 'Unauthorized', 'login_required': True}), 401
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


# --- Account API ---

@app.route('/d-api/accounts', methods=['GET'])
@login_required
def get_accounts():
    accounts = core.get_accounts()
    # Strip sensitive tokens before sending to frontend
    for acc in accounts:
        acc.pop('refresh_token', None)
        acc.pop('access_token', None)
        acc.pop('_refresh_token', None)
        acc.pop('_access_token', None)
    return jsonify({'accounts': accounts})


@app.route('/d-api/accounts', methods=['POST'])
@login_required
def add_account():
    data = request.json
    profile_arn = data.get('profile_arn')
    refresh_token = data.get('refresh_token')
    name = data.get('name', '')
    region = data.get('region', 'us-east-1')

    if not profile_arn or not refresh_token:
        return jsonify({'success': False, 'error': 'Missing Profile ARN or Refresh Token'}), 400

    status = core.add_account(profile_arn, refresh_token, name, region)
    return jsonify({'success': status['status'] == 'added', 'status': status['status'], 'id': status['id']})


@app.route('/d-api/accounts/<int:account_id>', methods=['DELETE'])
@login_required
def delete_account(account_id):
    success = core.remove_account(account_id)
    return jsonify({'success': success})


@app.route('/d-api/settings/prompts', methods=['GET', 'POST'])
@login_required
def prompt_settings():
    if request.method == 'POST':
        data = request.json
        success = core.save_prompt_settings(data)
        return jsonify({'success': success})
    
    settings = core.get_prompt_settings()
    return jsonify(settings)


@app.route('/d-api/settings/prompts/reset', methods=['POST'])
@login_required
def reset_prompt_settings():
    db = core._read_db()
    if 'prompt_settings' in db:
        del db['prompt_settings']
    core._write_db(db)
    return jsonify({'success': True, 'settings': core.get_prompt_settings()})


@app.route('/d-api/accounts/<int:account_id>/info', methods=['GET'])
@login_required
def account_info(account_id):
    token = core.get_access_token(account_id)
    if not token:
        return jsonify({'success': False, 'error': 'No active access token found for this account.'}), 401
        
    url = "https://codewhisperer.us-east-1.amazonaws.com/getUsageLimits"
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'User-Agent': 'aws-sdk-js/3.0.0'
    }
    
    try:
        with httpx.Client() as client:
            resp = client.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                return jsonify({'success': True, 'data': resp.json()})
            else:
                return jsonify({'success': False, 'error': f'AWS API error {resp.status_code}: {resp.text}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/d-api/accounts/oauth/start', methods=['POST'])
@login_required
def oauth_start():
    try:
        # Generate OAuth URL for manual pasting
        auth_url = core.get_oauth_url_only(auto_save=True)
        return jsonify({
            'success': True,
            'url': auth_url
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/d-api/accounts/oauth/callback', methods=['POST'])
@login_required
def oauth_callback():
    data = request.json
    pasted_url = data.get('url')
    if not pasted_url:
        return jsonify({'success': False, 'error': 'Missing URL'})
        
    try:
        result = core.process_manual_callback(pasted_url)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/d-api/accounts/<int:account_id>/switch-cmd', methods=['GET'])
@login_required
def account_switch_cmd(account_id):
    """Generate a shell command that switches the active Kiro IDE account."""
    accounts = core.get_accounts()
    acc = next((a for a in accounts if a['id'] == account_id), None)
    if not acc:
        return jsonify({'success': False, 'error': 'Account not found'}), 404

    # Get a fresh access token so the command works immediately
    access_token = core.get_access_token(account_id)
    if not access_token:
        return jsonify({'success': False, 'error': 'Could not retrieve a valid access token. Try refreshing.'}), 401

    profile_arn = acc['profile_arn']
    # get_accounts() already decrypts the refresh token into _refresh_token
    refresh_token = acc.get('_refresh_token', '')

    # Read provider/auth_method from the local kiro cache file (best available source)
    cache = core._read_kiro_token_cache()
    provider = cache.get('provider', 'Google')
    auth_method = cache.get('authMethod', 'social')

    import datetime
    expires_at = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S.000Z')

    token_payload = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "profileArn": profile_arn,
        "expiresAt": expires_at,
        "authMethod": auth_method,
        "provider": provider
    }
    token_json = json.dumps(token_payload, indent=2)

    # Build a safe single-line shell command using printf
    escaped = token_json.replace("'", "'\\''")
    account_name = acc.get('name', profile_arn)
    shell_cmd = (
        f"mkdir -p ~/.aws/sso/cache && "
        f"printf '%s' '{escaped}' > ~/.aws/sso/cache/kiro-auth-token.json && "
        f"echo '✅ Kiro account switched to: {account_name}'"
    )

    return jsonify({
        'success': True,
        'name': account_name,
        'profile_arn': profile_arn,
        'command': shell_cmd
    })



@app.route('/d-api/models', methods=['GET'])
@login_required
def get_dashboard_models():
    """Return the list of models from the local DB. If empty, prompt to refresh."""
    try:
        saved_models = core.get_saved_models()
        if saved_models:
            return jsonify({'success': True, 'models': saved_models})
            
        # Fallback to local known models if DB is empty
        models_data = []
        for m_id, provider in core.KNOWN_MODELS:
            models_data.append({'id': m_id, 'name': m_id, 'provider': provider})
        return jsonify({'success': True, 'models': models_data})
                
    except Exception as e:
        print(f"[*] /d-api/models error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/d-api/models/refresh', methods=['POST'])
@login_required
def refresh_dashboard_models():
    """Fetch the latest models from AWS CodeWhisperer and save them to the DB."""
    try:
        models_data = []
        accounts = core.get_accounts()
        if not accounts:
            return jsonify({'success': False, 'error': 'No active accounts to fetch models.'}), 400
            
        # Extract account ID from X-Account-Id, x-api-key, or Authorization
        target_id = request.headers.get('X-Account-Id')
        if not target_id:
            target_id = request.headers.get('x-api-key')
        if not target_id:
            auth = request.headers.get('Authorization', '')
            if auth.startswith('Bearer '):
                target_id = auth.split(' ')[1]
                
        active_account = None
        if target_id:
            try:
                tid = int(target_id)
                active_account = next((a for a in accounts if a['id'] == tid), None)
            except (ValueError, TypeError):
                pass
        
        if not active_account:
            active_account = accounts[0]
            
        profile_arn = active_account['profile_arn']
        account_id = active_account['id']
        token = core.get_access_token(account_id)
        if not token:
            return jsonify({'success': False, 'error': 'Failed to get access token.'}), 400
            
        url = "https://codewhisperer.us-east-1.amazonaws.com/ListAvailableModels?origin=AI_EDITOR"
        headers = {"Authorization": f"Bearer {token}"}
        
        with httpx.Client() as client:
            resp = client.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                for model in data.get('models', []):
                    m_id = model.get('modelId')
                    if not m_id:
                        continue
                    name = model.get('modelName', 'Unknown')
                    name_lower = name.lower()
                    if 'claude' in name_lower:
                        provider = 'Anthropic'
                    elif 'deepseek' in name_lower:
                        provider = 'DeepSeek'
                    elif 'minimax' in name_lower:
                        provider = 'MiniMax'
                    elif 'glm' in name_lower:
                        provider = 'Zhipu'
                    elif 'qwen' in name_lower:
                        provider = 'Alibaba'
                    elif 'llama' in name_lower:
                        provider = 'Meta'
                    elif 'mistral' in name_lower or 'mixtral' in name_lower:
                        provider = 'Mistral'
                    else:
                        provider = 'Amazon'
                    models_data.append({
                        'id': m_id,
                        'name': name,
                        'provider': provider
                    })
                
                # Save to DB
                core.save_models(models_data)
                return jsonify({'success': True, 'models': models_data})
            else:
                return jsonify({'success': False, 'error': f'Failed to fetch: {resp.text}'}), resp.status_code
                
    except Exception as e:
        print(f"[*] /d-api/models/refresh error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# --- Proxy API (OpenAI Compatible) ---

@app.route('/v1/models', methods=['GET'])
def v1_models():
    data = []
    
    # 1. Add our local aliases so IDEs can use them
    for alias in MODEL_MAPPING.keys():
        data.append({
            'id': alias,
            'object': 'model',
            'created': int(time.time()),
            'owned_by': 'antigravity'
        })
        
    # 2. Try to fetch real models from DB
    try:
        saved_models = core.get_saved_models()
        if saved_models:
            for model in saved_models:
                data.append({
                    'id': model['id'],
                    'object': 'model',
                    'created': int(time.time()),
                    'owned_by': model['provider'].lower()
                })
            return jsonify({'object': 'list', 'data': data})
    except Exception as e:
        print(f"[*] /v1/models DB fetch error: {e}")
        
    # 3. Fallback to KNOWN_MODELS if fetch fails or no accounts
    for m_id, provider in core.KNOWN_MODELS:
        if m_id not in MODEL_MAPPING:
            data.append({
                'id': m_id,
                'object': 'model',
                'created': int(time.time()),
                'owned_by': provider.lower()
            })
            
    return jsonify({'object': 'list', 'data': data})


def process_prompt_template(system_text, user_text, model_name=""):
    """Apply the user-defined prompt template and custom shortcodes."""
    settings = core.get_prompt_settings()
    template = settings.get('template', 'System Instructions:\n{{system}}\n\nUser Request:\n{{request}}')
    custom = settings.get('custom_shortcodes', [])
    
    # Dynamic system tags
    now = datetime.datetime.now()
    dynamic = {
        '{{date}}': now.strftime("%Y-%m-%d"),
        '{{time}}': now.strftime("%H:%M:%S"),
        '{{datetime}}': now.strftime("%Y-%m-%d %H:%M:%S"),
        '{{model}}': model_name
    }
    
    # 1. Apply dynamic and custom shortcodes to source texts
    for tag, val in dynamic.items():
        system_text = system_text.replace(tag, val)
        user_text = user_text.replace(tag, val)
        
    for item in custom:
        tag = item.get('tag', '')
        val = item.get('value', '')
        if tag:
            system_text = system_text.replace(tag, val)
            user_text = user_text.replace(tag, val)
            
    # 2. Apply main template
    result = template.replace('{{system}}', system_text).replace('{{request}}', user_text)
    
    # 3. Final pass for shortcodes in template itself
    for tag, val in dynamic.items():
        result = result.replace(tag, val)
        
    for item in custom:
        tag = item.get('tag', '')
        val = item.get('value', '')
        if tag:
            result = result.replace(tag, val)
            
    return result


@app.route('/v1/chat/completions', methods=['POST', 'OPTIONS'])
def v1_chat_completions():
    if request.method == 'OPTIONS':
        return '', 204
        
    try:
        body = request.get_json()
    except Exception as e:
        return jsonify({'error': {'message': f'Invalid request body: {e}', 'type': 'invalid_request_error'}}), 400

    model_name = body.get('model', 'claude-sonnet-4.5')
    aws_model_id = MODEL_MAPPING.get(model_name, model_name)
    is_stream = body.get('stream', False)
    messages = body.get('messages', [])
    
    accounts = core.get_accounts()
    if not accounts:
        return jsonify({'error': {'message': 'No Kiro accounts available. Add accounts in the dashboard first.', 'type': 'server_error'}}), 503

    # Pick account from header if specified (used by dashboard test tool), else pick the most recently used active account
    target_id = request.headers.get('X-Account-Id')
    if not target_id:
        target_id = request.headers.get('x-api-key')
    if not target_id:
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            target_id = auth.split(' ')[1]
            
    active_account = None
    if target_id:
        try:
            tid = int(target_id)
            active_account = next((a for a in accounts if a['id'] == tid), None)
        except (ValueError, TypeError):
            pass
    if not active_account:
        active_account = accounts[0]
        
    profile_arn = active_account['profile_arn']
    account_id = active_account['id']
    access_token = core.get_access_token(account_id)
    
    if not access_token:
        return jsonify({'error': {'message': 'Failed to retrieve or refresh access token for Kiro account.', 'type': 'server_error'}}), 401

    url = "https://codewhisperer.us-east-1.amazonaws.com/generateAssistantResponse"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "aws-sdk-js/3.0.0"
    }
    
    # Build conversation history from OpenAI messages
    history = []
    system_prompts = []
    current_content = "Hello"
    if messages:
        # Extract system prompts first
        for msg in messages:
            if msg.get('role') == 'system':
                sys_content = msg.get('content', '')
                if isinstance(sys_content, list):
                    texts = [c.get('text', '') for c in sys_content if c.get('type') == 'text']
                    sys_content = "\n".join(texts)
                if sys_content:
                    system_prompts.append(sys_content)

        last_msg_content = messages[-1].get('content', '')
        if isinstance(last_msg_content, list):
            # Extract text blocks, stringify the rest or ignore images since CW requires string
            texts = []
            for c in last_msg_content:
                if c.get('type') == 'text':
                    texts.append(c.get('text', ''))
                elif c.get('type') == 'image_url':
                    texts.append('[Image Attached - Note: AWS CW proxy currently drops image data]')
            current_content = "\n".join(texts)
        else:
            current_content = last_msg_content
            
        # Apply prompt template and shortcodes
        combined_system = "\n\n".join(system_prompts) if system_prompts else ""
        current_content = process_prompt_template(combined_system, current_content, model_name=model_name)

        # Map previous messages to CW history format
        for msg in messages[:-1]:
            role = msg.get('role', 'user')
            if role == 'system':
                continue
                
            msg_content = msg.get('content', '')
            if isinstance(msg_content, list):
                texts = []
                for c in msg_content:
                    if c.get('type') == 'text':
                        texts.append(c.get('text', ''))
                    elif c.get('type') == 'image_url':
                        texts.append('[Image Attached - Note: AWS CW proxy currently drops image data]')
                msg_content = "\n".join(texts)

            if role == 'user':
                history.append({
                    "userInputMessage": {
                        "content": msg_content,
                        "modelId": aws_model_id,
                        "origin": "AI_EDITOR"
                    }
                })
            elif role == 'assistant':
                history.append({
                    "assistantResponseMessage": {
                        "content": msg_content
                    }
                })
        
    cw_payload = {
        "conversationState": {
            "conversationId": str(uuid.uuid4()),
            "history": history,
            "currentMessage": {
                "userInputMessage": {
                    "content": current_content,
                    "modelId": aws_model_id,
                    "origin": "AI_EDITOR"
                }
            },
            "chatTriggerType": "MANUAL"
        },
        "profileArn": profile_arn
    }

    print(f"[*] Model: {model_name} -> {aws_model_id}")

    if is_stream:
        def generate():
            completion_id = f'chatcmpl-{uuid.uuid4().hex[:12]}'
            
            # Send initial chunk
            yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': model_name, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"
            
            try:
                # Use httpx to make the streaming request
                with httpx.stream("POST", url, json=cw_payload, headers=headers, timeout=60.0) as resp:
                    if resp.status_code != 200:
                        err_text = resp.read().decode('utf-8', errors='ignore')
                        print(f"[*] AWS CodeWhisperer Error: {resp.status_code} - {err_text}")
                        # If unauthorized or bad request, we yield an error chunk
                        err_msg = f"API Error: {resp.status_code} - {err_text}"
                        yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': model_name, 'choices': [{'index': 0, 'delta': {'content': err_msg}, 'finish_reason': 'stop'}]})}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    import re
                    buffer = ""
                    for chunk in resp.iter_text():
                        buffer += chunk
                        
                        # Use regex to find assistantResponseEvent JSON payloads
                        # AWS returns chunks like: {"content":"Hello","modelId":"auto"}
                        matches = re.findall(r'\{"content":".*?","modelId":".*?"\}', buffer)
                        
                        for match in matches:
                            try:
                                data = json.loads(match)
                                content = data.get("content", "")
                                if content:
                                    yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': model_name, 'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]})}\n\n"
                            except Exception as e:
                                print("Parse error:", e)
                                
                        # Clear buffer but keep last few chars in case of split JSON across chunks
                        # Since we process matches, we can remove the matched parts or just clear it.
                        # Simple approach: clear buffer up to the last processed match
                        if matches:
                            last_match_idx = buffer.rfind(matches[-1])
                            if last_match_idx != -1:
                                buffer = buffer[last_match_idx + len(matches[-1]):]
                        
                        if len(buffer) > 1024:
                            buffer = buffer[-512:]
            except Exception as e:
                print(f"[*] Stream error: {e}")
                
            yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': model_name, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
            yield "data: [DONE]\n\n"
            
        return Response(stream_with_context(generate()), content_type='text/event-stream')
    else:
        # Non-streaming fallback
        completion_id = f'chatcmpl-{uuid.uuid4().hex[:12]}'
        try:
            with httpx.stream("POST", url, json=cw_payload, headers=headers, timeout=60.0) as resp:
                if resp.status_code != 200:
                    err_text = resp.read().decode('utf-8', errors='ignore')
                    return jsonify({'error': {'message': f'AWS Error {resp.status_code}: {err_text}', 'type': 'api_error'}}), 502
                
                import re
                buffer = ""
                full_content = ""
                for chunk in resp.iter_text():
                    buffer += chunk
                    matches = re.findall(r'\{"content":".*?","modelId":".*?"\}', buffer)
                    for match in matches:
                        try:
                            data = json.loads(match)
                            full_content += data.get("content", "")
                        except:
                            pass
                    if matches:
                        last_match_idx = buffer.rfind(matches[-1])
                        if last_match_idx != -1:
                            buffer = buffer[last_match_idx + len(matches[-1]):]
                    if len(buffer) > 1024:
                        buffer = buffer[-512:]
                        
            return jsonify({
                'id': completion_id,
                'object': 'chat.completion',
                'created': int(time.time()),
                'model': model_name,
                'choices': [{
                    'index': 0,
                    'message': {
                        'role': 'assistant',
                        'content': full_content
                    },
                    'finish_reason': 'stop'
                }]
            })
        except Exception as e:
            print(f"[*] Proxy error: {e}")
            return jsonify({'error': {'message': str(e), 'type': 'internal_error'}}), 500


@app.route('/v1/messages', methods=['POST', 'OPTIONS'])
def v1_messages():
    if request.method == 'OPTIONS':
        return '', 204
        
    try:
        body = request.get_json()
    except Exception as e:
        return jsonify({'error': {'message': f'Invalid request body: {e}', 'type': 'invalid_request_error'}}), 400

    model_name = body.get('model', 'claude-sonnet-4.5')
    aws_model_id = MODEL_MAPPING.get(model_name, model_name)
    is_stream = body.get('stream', False)
    messages = body.get('messages', [])
    
    accounts = core.get_accounts()
    if not accounts:
        return jsonify({'error': {'message': 'No Kiro accounts available. Add accounts in the dashboard first.', 'type': 'api_error'}}), 503

    # Use X-Account-Id header to select account if present, otherwise default
    target_id = request.headers.get('X-Account-Id')
    if not target_id:
        target_id = request.headers.get('x-api-key')
    if not target_id:
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            target_id = auth.split(' ')[1]
            
    active_account = None
    if target_id:
        try:
            tid = int(target_id)
            active_account = next((a for a in accounts if a['id'] == tid), None)
        except (ValueError, TypeError):
            pass
    if not active_account:
        active_account = accounts[0]
        
    profile_arn = active_account['profile_arn']
    account_id = active_account['id']
    access_token = core.get_access_token(account_id)
    
    if not access_token:
        return jsonify({'error': {'message': 'Failed to retrieve or refresh access token for Kiro account.', 'type': 'api_error'}}), 401

    url = "https://codewhisperer.us-east-1.amazonaws.com/generateAssistantResponse"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "aws-sdk-js/3.0.0"
    }
    
    # Build conversation history from Anthropic messages
    history = []
    system_prompts = []
    current_content = "Hello"
    
    # Extract top-level system prompt if present (Anthropic specific)
    top_system = body.get('system', '')
    if isinstance(top_system, list):
        texts = [c.get('text', '') for c in top_system if c.get('type') == 'text']
        top_system = "\n".join(texts)
    if top_system:
        system_prompts.append(top_system)

    if messages:
        # Extract system roles from messages array
        for msg in messages:
            if msg.get('role') == 'system':
                content = msg.get('content', '')
                if isinstance(content, list):
                    content = next((c.get('text', '') for c in content if c.get('type') == 'text'), '')
                if content:
                    system_prompts.append(content)

        # Anthropic messages are usually {"role": "user", "content": "..."}
        # But content can also be a list of blocks. For simplicity, we extract text if needed.
        last_msg = messages[-1]
        content_obj = last_msg.get('content', '')
        if isinstance(content_obj, list):
            current_content = next((c.get('text', '') for c in content_obj if c.get('type') == 'text'), '')
        else:
            current_content = content_obj
            
        # Apply prompt template and shortcodes
        combined_system = "\n\n".join(system_prompts) if system_prompts else ""
        current_content = process_prompt_template(combined_system, current_content, model_name=model_name)

        for msg in messages[:-1]:
            role = msg.get('role', 'user')
            if role == 'system':
                continue
                
            content = msg.get('content', '')
            if isinstance(content, list):
                content = next((c.get('text', '') for c in content if c.get('type') == 'text'), '')
            if role == 'user':
                history.append({
                    "userInputMessage": {
                        "content": content,
                        "modelId": aws_model_id,
                        "origin": "AI_EDITOR"
                    }
                })
            elif role == 'assistant':
                history.append({
                    "assistantResponseMessage": {
                        "content": content
                    }
                })
        
    cw_payload = {
        "conversationState": {
            "conversationId": str(uuid.uuid4()),
            "history": history,
            "currentMessage": {
                "userInputMessage": {
                    "content": current_content,
                    "modelId": aws_model_id,
                    "origin": "AI_EDITOR"
                }
            },
            "chatTriggerType": "MANUAL"
        },
        "profileArn": profile_arn
    }

    print(f"[*] Anthropic Model: {model_name} -> {aws_model_id}")
    msg_id = f'msg_{uuid.uuid4().hex[:24]}'

    if is_stream:
        def generate():
            # Initial Anthropic stream events
            yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'model': model_name, 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}})}\n\n"
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
            
            try:
                with httpx.stream("POST", url, json=cw_payload, headers=headers, timeout=60.0) as resp:
                    if resp.status_code != 200:
                        err_text = resp.read().decode('utf-8', errors='ignore')
                        print(f"[*] AWS CodeWhisperer Error: {resp.status_code} - {err_text}")
                        yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': f'API Error: {resp.status_code}'}})}\n\n"
                        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                        yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'error', 'stop_sequence': None}, 'usage': {'output_tokens': 0}})}\n\n"
                        yield "event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n"
                        return

                    import re
                    buffer = ""
                    for chunk in resp.iter_text():
                        buffer += chunk
                        matches = re.findall(r'\{"content":".*?","modelId":".*?"\}', buffer)
                        for match in matches:
                            try:
                                data = json.loads(match)
                                content = data.get("content", "")
                                if content:
                                    yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': content}})}\n\n"
                            except Exception as e:
                                pass
                                
                        if matches:
                            last_match_idx = buffer.rfind(matches[-1])
                            if last_match_idx != -1:
                                buffer = buffer[last_match_idx + len(matches[-1]):]
                        if len(buffer) > 1024:
                            buffer = buffer[-512:]
            except Exception as e:
                print(f"[*] Stream error: {e}")
                
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
            yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 0}})}\n\n"
            yield "event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n"
            
        return Response(stream_with_context(generate()), content_type='text/event-stream')
    else:
        # Non-streaming fallback
        try:
            with httpx.stream("POST", url, json=cw_payload, headers=headers, timeout=60.0) as resp:
                if resp.status_code != 200:
                    err_text = resp.read().decode('utf-8', errors='ignore')
                    return jsonify({'error': {'message': f'AWS Error {resp.status_code}: {err_text}', 'type': 'api_error'}}), 502
                
                import re
                buffer = ""
                full_content = ""
                for chunk in resp.iter_text():
                    buffer += chunk
                    matches = re.findall(r'\{"content":".*?","modelId":".*?"\}', buffer)
                    for match in matches:
                        try:
                            data = json.loads(match)
                            full_content += data.get("content", "")
                        except:
                            pass
                    if matches:
                        last_match_idx = buffer.rfind(matches[-1])
                        if last_match_idx != -1:
                            buffer = buffer[last_match_idx + len(matches[-1]):]
                    if len(buffer) > 1024:
                        buffer = buffer[-512:]
                        
            return jsonify({
                'id': msg_id,
                'type': 'message',
                'role': 'assistant',
                'model': model_name,
                'content': [{'type': 'text', 'text': full_content}],
                'stop_reason': 'end_turn',
                'stop_sequence': None,
                'usage': {
                    'input_tokens': 0,
                    'output_tokens': 0
                }
            })
        except Exception as e:
            print(f"[*] Proxy error: {e}")
            return jsonify({'error': {'message': str(e), 'type': 'api_error'}}), 500


def main():
    portal_config = core.get_portal_config()
    print(f"[*] Starting Kiro Web Portal v1.0.1 on port {portal_config['port']}...")
    app.run(host='0.0.0.0', port=portal_config['port'], debug=False)


if __name__ == '__main__':
    main()
