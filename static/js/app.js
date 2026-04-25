document.addEventListener('DOMContentLoaded', () => {
    // Initialize Lucide Icons
    lucide.createIcons();

    // Tabs logic
    const navItems = document.querySelectorAll('.ds-sidebar__link[data-tab]');
    const tabs = document.querySelectorAll('.tab-content');

    navItems.forEach(item => {
        item.addEventListener('click', () => {
            navItems.forEach(nav => nav.classList.remove('active'));
            tabs.forEach(tab => tab.classList.remove('active'));

            item.classList.add('active');
            const tabId = item.getAttribute('data-tab');
            const tabContent = document.getElementById(`tab-${tabId}`);
            if (tabContent) tabContent.classList.add('active');

            if (tabId === 'accounts') loadAccounts();
            if (tabId === 'models') loadModels();
            if (tabId === 'settings') loadSettings();
        });
    });

    // Global Toast System
    window.showToast = (message, type = 'success') => {
        const container = document.getElementById('toast-container');
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        
        const icon = type === 'success' ? 'check-circle' : 'alert-circle';
        toast.innerHTML = `<i data-lucide="${icon}"></i> <span>${message}</span>`;
        
        container.appendChild(toast);
        lucide.createIcons();

        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translateX(50px)';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    };

    // --- System Status ---
    const updateSystemStatus = async () => {
        try {
            const res = await fetch('/api/proxy/status');
            const data = await res.json();
            const dot = document.getElementById('system-status-dot');
            const text = document.getElementById('system-status-text');

            if (data.port_used || data.running_internal) {
                dot.className = 'pulse-dot running';
                text.textContent = `System Online (Port ${data.port})`;
            } else {
                dot.className = 'pulse-dot stopped';
                text.textContent = `Offline? (Port ${data.port})`;
            }
            
            // Update API Base URL display if it exists
            const apiBase = document.getElementById('api-base-url');
            if (apiBase) apiBase.textContent = `http://${window.location.hostname}:${data.port}/v1`;
        } catch (e) {
            console.error('Failed to get system status', e);
        }
    };

    // Initial status check
    updateSystemStatus();
    setInterval(updateSystemStatus, 10000); // poll every 10s

    // --- Accounts Management ---
    const loadAccounts = async () => {
        const tbody = document.getElementById('accounts-tbody');
        tbody.innerHTML = '<tr><td colspan="5" class="text-center py-4 text-dim">Loading accounts...</td></tr>';
        
        try {
            const res = await fetch('/api/accounts');
            const data = await res.json();
            
            if (data.accounts.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" class="text-center py-4 text-dim">No accounts found. Add one to start.</td></tr>';
                return;
            }

            tbody.innerHTML = '';
            data.accounts.forEach(acc => {
                const tr = document.createElement('tr');
                
                // Calculate Usage (simplified)
                let usage = 'N/A';
                const quota = acc.quota;
                if (quota && quota.models) {
                    const m = quota.models['cloudaicompanion.googleapis.com/gemini-2.5-flash'] || Object.values(quota.models)[0];
                    if (m && m.percentage !== undefined) {
                        usage = `${m.percentage}%`;
                    }
                }

                tr.innerHTML = `
                    <td><strong>${acc.email}</strong></td>
                    <td class="text-dim">${acc.name || 'Unknown'}</td>
                    <td><span class="badge ${acc.status === 'active' ? 'badge--success' : 'badge--warning'}">${acc.status}</span></td>
                    <td>${usage}</td>
                    <td>
                        <div style="display:flex; gap: 0.5rem">
                            <button class="btn btn--ghost btn--icon btn--sm" title="Test Account" onclick="openTestModal('${acc.email}')">
                                <i data-lucide="play"></i>
                            </button>
                            <button class="btn btn--ghost btn--icon btn--sm" title="Refresh Quota" onclick="refreshAccount('${acc.email}')">
                                <i data-lucide="refresh-cw"></i>
                            </button>
                            <button class="btn btn--danger btn--icon btn--sm" title="Remove Account" onclick="removeAccount('${acc.email}')">
                                <i data-lucide="trash-2"></i>
                            </button>
                        </div>
                    </td>
                `;
                tbody.appendChild(tr);
            });
            lucide.createIcons();
        } catch (e) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center py-4 text-dim" style="color:var(--accent-danger)">Failed to load accounts</td></tr>';
        }
    };

    window.refreshAccount = async (email) => {
        showToast(`Refreshing ${email}...`, 'success');
        try {
            const res = await fetch(`/api/accounts/${email}/refresh`, { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                showToast(data.message, 'success');
                loadAccounts();
            } else {
                showToast(data.message, 'error');
            }
        } catch (e) {
            showToast('Failed to refresh', 'error');
        }
    };

    window.removeAccount = async (email) => {
        if (!confirm(`Are you sure you want to remove ${email}?`)) return;
        try {
            const res = await fetch(`/api/accounts/${email}`, { method: 'DELETE' });
            const data = await res.json();
            if (data.success) {
                showToast('Account removed', 'success');
                loadAccounts();
            } else {
                showToast('Failed to remove account', 'error');
            }
        } catch (e) {
            showToast('Network error', 'error');
        }
    };

    document.getElementById('btn-refresh-all').addEventListener('click', async () => {
        const res = await fetch('/api/accounts');
        const data = await res.json();
        for (const acc of data.accounts) {
            await window.refreshAccount(acc.email);
        }
    });

    // --- Proxy Test Modal ---
    const testModal = document.getElementById('modal-test');
    let currentTestEmail = '';

    window.openTestModal = (email) => {
        currentTestEmail = email;
        document.getElementById('test-email-display').textContent = email;
        document.getElementById('test-result-container').classList.add('hidden');
        document.getElementById('test-result-text').textContent = '';
        document.getElementById('test-prompt').value = '';
        testModal.classList.add('active');
        
        // Ensure models are loaded for the select
        loadModels();
    };

    document.getElementById('btn-close-test-modal').addEventListener('click', () => {
        testModal.classList.remove('active');
    });

    document.getElementById('btn-run-test').addEventListener('click', async () => {
        const promptInput = document.getElementById('test-prompt');
        const prompt = promptInput.value;
        const model = document.getElementById('test-model').value;
        const resultContainer = document.getElementById('test-result-container');
        const resultText = document.getElementById('test-result-text');
        const btn = document.getElementById('btn-run-test');

        if (!prompt) {
            showToast('Please enter a prompt', 'error');
            return;
        }

        btn.disabled = true;
        btn.textContent = 'Testing...';
        resultContainer.classList.remove('hidden');
        resultText.textContent = 'Processing request through proxy...';

        try {
            const res = await fetch('/api/proxy/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: currentTestEmail, prompt, model })
            });
            const data = await res.json();
            
            if (data.success) {
                const content = data.response.choices[0].message.content;
                resultText.textContent = content;
                showToast('Proxy test successful', 'success');
            } else {
                resultText.textContent = `Error: ${data.message}`;
                showToast('Proxy test failed', 'error');
            }
        } catch (e) {
            resultText.textContent = `Network Error: ${e.message}`;
            showToast('Network error', 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = 'Run Proxy Test';
        }
    });

    // --- OAuth Flow ---
    let oauthPollInterval;
    const modal = document.getElementById('oauth-modal');

    document.getElementById('btn-add-account').addEventListener('click', async () => {
        try {
            const res = await fetch('/api/accounts/oauth/start', { method: 'POST' });
            const data = await res.json();
            
            if (data.auth_url) {
                window.open(data.auth_url, '_blank');
                modal.classList.add('active');

                // Manual code submission for server deployments
                const modalBody = modal.querySelector('.card');
                if (!document.getElementById('manual-code-container')) {
                    const div = document.createElement('div');
                    div.id = 'manual-code-container';
                    div.style.marginTop = 'var(--space-6)';
                    div.style.paddingTop = 'var(--space-4)';
                    div.style.borderTop = '1px solid var(--color-border)';
                    div.innerHTML = `
                        <p class="text-dim" style="font-size: var(--text-sm); margin-bottom: var(--space-3);">Redirect failed? Paste the code here:</p>
                        <div style="display:flex; gap: 8px;">
                            <input type="text" id="input-oauth-code" class="form-input" style="flex:1" placeholder="Paste code from URL here...">
                            <button class="btn btn--primary btn--sm" id="btn-submit-code">Save</button>
                        </div>
                    `;
                    modalBody.appendChild(div);
                    
                    document.getElementById('btn-submit-code').addEventListener('click', async () => {
                        const code = document.getElementById('input-oauth-code').value.trim();
                        if (!code) return;
                        
                        try {
                            const cbRes = await fetch('/api/accounts/oauth/callback', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ code, port: data.port })
                            });
                            const cbData = await cbRes.json();
                            if (cbData.success) {
                                showToast('Account added successfully!', 'success');
                                clearInterval(oauthPollInterval);
                                modal.classList.remove('active');
                                loadAccounts();
                            } else {
                                showToast('Failed to add account with that code.', 'error');
                            }
                        } catch (e) {
                            showToast('Error submitting code', 'error');
                        }
                    });
                }
                
                oauthPollInterval = setInterval(async () => {
                    const checkRes = await fetch(`/api/accounts/oauth/check/${data.port}`);
                    const checkData = await checkRes.json();
                    
                    if (!checkData.pending) {
                        clearInterval(oauthPollInterval);
                        modal.classList.remove('active');
                        
                        if (checkData.success) {
                            if (checkData.added) {
                                showToast(`Successfully added ${checkData.email}`, 'success');
                            } else {
                                showToast(`Account ${checkData.email} already exists`, 'error');
                            }
                            loadAccounts();
                        } else {
                            showToast(checkData.error || 'OAuth failed', 'error');
                        }
                    }
                }, 2000);
            }
        } catch (e) {
            showToast('Failed to start OAuth flow', 'error');
        }
    });

    document.getElementById('btn-cancel-oauth').addEventListener('click', () => {
        clearInterval(oauthPollInterval);
        modal.classList.remove('active');
    });

    // --- Models ---
    const loadModels = async () => {
        const tbody = document.getElementById('models-tbody');
        if (!tbody) return;
        tbody.innerHTML = '<tr><td colspan="2" class="text-center py-4 text-dim">Loading models...</td></tr>';
        
        try {
            const res = await fetch('/api/models');
            const data = await res.json();
            
            tbody.innerHTML = '';
            
            // Show real models
            if (data.models && data.models.length > 0) {
                data.models.forEach(model => {
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td><strong>${model}</strong></td>
                        <td class="text-dim">Native Model</td>
                    `;
                    tbody.appendChild(tr);
                });
                
                // Update test modal select
                const testSelect = document.getElementById('test-model');
                const currentVal = testSelect.value;
                testSelect.innerHTML = '';
                data.models.forEach(model => {
                    const opt = document.createElement('option');
                    opt.value = model;
                    opt.textContent = model;
                    if (model === currentVal) opt.selected = true;
                    testSelect.appendChild(opt);
                });
            }

            // Show aliases
            for (const [alias, internal] of Object.entries(data.mapping)) {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><strong>${alias}</strong></td>
                    <td class="text-dim">Alias for ${internal}</td>
                `;
                tbody.appendChild(tr);
            }
            
            lucide.createIcons();
        } catch (e) {
            tbody.innerHTML = '<tr><td colspan="2" class="text-center py-4 text-dim" style="color:var(--accent-danger)">Failed to load models</td></tr>';
        }
    };

    document.getElementById('btn-fetch-models').addEventListener('click', async () => {
        const btn = document.getElementById('btn-fetch-models');
        const oldHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i data-lucide="refresh-cw" class="spin"></i> Fetching...';
        lucide.createIcons();
        
        try {
            const res = await fetch('/api/models/fetch', { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                showToast(data.message, 'success');
                loadModels();
            } else {
                showToast(data.message, 'error');
            }
        } catch (e) {
            showToast('Network error', 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = oldHtml;
            lucide.createIcons();
        }
    });

    // --- Settings ---
    const loadSettings = async () => {
        try {
            const res = await fetch('/api/settings');
            const data = await res.json();
            document.getElementById('input-portal-port').value = data.portal_port || 5000;
            document.getElementById('input-upstream-proxy').value = data.upstream_proxy || '';
            document.getElementById('input-admin-slug').value = data.admin_slug || 'admin';
            document.getElementById('input-admin-username').value = data.admin_username || 'admin';
        } catch (e) {}

        try {
            const svcRes = await fetch('/api/service/status');
            const svcData = await svcRes.json();
            const autostartToggle = document.getElementById('input-service-autostart');
            const msgEl = document.getElementById('service-status-msg');
            
            if (svcData.installed) {
                autostartToggle.checked = svcData.enabled;
                msgEl.textContent = svcData.enabled ? 'Service is enabled and will start on boot.' : 'Service is disabled.';
                msgEl.style.color = svcData.enabled ? 'var(--accent-success)' : 'var(--text-dim)';
            } else {
                autostartToggle.checked = false;
                msgEl.textContent = 'Service is not installed yet. Toggling will install it.';
            }
        } catch (e) {}
    };

    document.getElementById('btn-save-settings').addEventListener('click', async () => {
        const port = document.getElementById('input-portal-port').value;
        const upstream = document.getElementById('input-upstream-proxy').value;
        
        try {
            const res = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    portal_port: parseInt(port), 
                    proxy_port: parseInt(port), // sync them
                    upstream_proxy: upstream
                })
            });
            const data = await res.json();
            if (data.success) {
                showToast('Settings saved. Restart required for port changes.', 'success');
                updateSystemStatus();
            }
        } catch (e) {
            showToast('Failed to save settings', 'error');
        }
    });

    document.getElementById('btn-save-security').addEventListener('click', async () => {
        const adminSlug = document.getElementById('input-admin-slug').value;
        const username = document.getElementById('input-admin-username').value;
        const password = document.getElementById('input-admin-password').value;
        
        try {
            const res = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    admin_slug: adminSlug,
                    admin_username: username,
                    admin_password: password
                })
            });
            const data = await res.json();
            if (data.success) {
                showToast('Security settings updated. Restart required for slug changes.', 'success');
                if (password) document.getElementById('input-admin-password').value = '';
            }
        } catch (e) {
            showToast('Failed to update security', 'error');
        }
    });

    // --- Restart System ---
    document.getElementById('btn-restart-system').addEventListener('click', async () => {
        if (!confirm('Are you sure you want to restart the AGPM services? This will take a few seconds.')) return;
        
        const btn = document.getElementById('btn-restart-system');
        btn.disabled = true;
        btn.innerHTML = '<i data-lucide="loader" class="spin"></i> Restarting...';
        lucide.createIcons();
        
        try {
            const res = await fetch('/api/system/restart', { method: 'POST' });
            const data = await res.json();
            
            if (data.success) {
                showToast('Restart signal sent. Dashboard will reload soon.', 'success');
                // Poll for availability
                setTimeout(() => {
                    const checkInterval = setInterval(async () => {
                        try {
                            const ping = await fetch('/api/settings');
                            if (ping.ok) {
                                clearInterval(checkInterval);
                                location.reload();
                            }
                        } catch(e) {}
                    }, 2000);
                }, 3000);
            } else {
                showToast(data.message, 'error');
                btn.disabled = false;
                btn.innerHTML = '<i data-lucide="rotate-ccw"></i> Restart AGPM Services';
                lucide.createIcons();
            }
        } catch (e) {
            showToast('Restart initiated. Reloading...', 'success');
            setTimeout(() => location.reload(), 5000);
        }
    });

    document.getElementById('input-service-autostart').addEventListener('change', async (e) => {
        const isChecked = e.target.checked;
        const msgEl = document.getElementById('service-status-msg');
        msgEl.textContent = isChecked ? 'Enabling...' : 'Disabling...';
        msgEl.style.color = 'var(--text-dim)';
        
        try {
            // If it was never installed, we install it first if they toggle it ON.
            // But since our install route is gone, we'll rely on the toggle route which does enable.
            // Oh wait, our service toggle route does `systemctl enable`. It requires the unit file.
            // We should make sure the service toggle route also writes the unit file if it doesn't exist.
            
            const res = await fetch('/api/service/toggle', { 
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enable: isChecked })
            });
            const data = await res.json();
            
            msgEl.textContent = data.message;
            if (data.success) {
                msgEl.style.color = isChecked ? 'var(--accent-success)' : 'var(--text-dim)';
                showToast(`Auto-start ${isChecked ? 'enabled' : 'disabled'}`, 'success');
            } else {
                msgEl.style.color = 'var(--accent-danger)';
                e.target.checked = !isChecked; // Revert visually
                showToast('Action failed', 'error');
            }
        } catch (err) {
            msgEl.textContent = 'Network error';
            msgEl.style.color = 'var(--accent-danger)';
            e.target.checked = !isChecked; // Revert visually
        }
    });

    // Load initial tab data
    loadAccounts();
    loadModels();
});
