// ─── Auth Guard ───
const _authToken = localStorage.getItem('auth_token');
const _authUser = JSON.parse(localStorage.getItem('auth_user') || 'null');

if (!_authToken) {
    window.location.href = '/login.html';
}

// Verify token is still valid on page load
fetch('/api/auth/me', { headers: { 'Authorization': 'Bearer ' + _authToken } })
    .then(r => { if (!r.ok) { localStorage.clear(); window.location.href = '/login.html'; } })
    .catch(() => { localStorage.clear(); window.location.href = '/login.html'; });

// Authenticated fetch wrapper — auto-injects JWT token
function authFetch(url, options = {}) {
    if (!options.headers) options.headers = {};
    options.headers['Authorization'] = 'Bearer ' + _authToken;
    return fetch(url, options).then(r => {
        if (r.status === 401) { localStorage.clear(); window.location.href = '/login.html'; }
        return r;
    });
}

function logout() {
    localStorage.clear();
    window.location.href = '/login.html';
}

let messages = [];
let _currentSessionId = null;
const chatHistory = document.getElementById('chat-history');
const messageInput = document.getElementById('message-input');
const sendBtn = document.getElementById('send-btn');
const statusBadge = document.getElementById('status-badge');
const progressBar = document.getElementById('progress-bar');
const progressFill = document.getElementById('progress-fill');
const progressText = document.getElementById('progress-text');

// ─── View Switching & Auto Refresh ───
let _currentView = 'pools';
let _pollInterval = null;

async function loadChatSessions() {
    try {
        const res = await authFetch('/api/chat/sessions');
        if (res.ok) {
            const sessions = await res.json();
            const list = document.getElementById('chat-session-list');
            if (list) {
                list.innerHTML = sessions.map(s => `
                    <button class="session-item ${s.id === _currentSessionId ? 'active' : ''}" onclick="switchChatSession(${s.id})">
                        <span class="session-title">${s.title}</span>
                        <span class="session-delete" onclick="deleteChatSession(event, ${s.id})">🗑️</span>
                    </button>
                `).join('');
            }
            if (sessions.length > 0 && !_currentSessionId) {
                switchChatSession(sessions[0].id);
            }
        }
    } catch (e) {
        console.error("Failed to load chat sessions:", e);
    }
}

async function switchChatSession(sessionId) {
    _currentSessionId = sessionId;
    loadChatSessions(); // to update active state
    switchView('chat');
    try {
        const res = await authFetch(`/api/chat/sessions/${sessionId}/history`);
        if (res.ok) {
            const history = await res.json();
            
            const welcome = chatHistory.querySelector('.welcome-card');
            if (welcome) welcome.remove();
            
            messages = history;
            chatHistory.innerHTML = '';
            
            if (history.length === 0) {
                chatHistory.innerHTML = `
                    <div class="welcome-card">
                        <div class="welcome-icon">◆</div>
                        <h2>海外客 AI 销售助手</h2>
                        <p>智能销售开发助手，全自动搜索、挖掘、触达</p>
                        <div class="welcome-features">
                            <div class="feature"><span class="feature-icon">→</span><span>搜索全网目标公司</span></div>
                            <div class="feature"><span class="feature-icon">→</span><span>自动挖掘决策人邮箱</span></div>
                            <div class="feature"><span class="feature-icon">→</span><span>AI 定制化开发信撰写</span></div>
                            <div class="feature"><span class="feature-icon">→</span><span>粘贴客户画像智能提取</span></div>
                        </div>
                        <p class="welcome-hint">输入指令开始，例如：<em>"搜索欧洲的 Padel 设备公司"</em></p>
                    </div>
                `;
            } else {
                history.forEach(msg => {
                    appendMessage(msg.role === 'assistant' ? 'agent' : 'user', msg.content);
                });
            }
        }
    } catch (e) {
        console.error("Failed to load chat history:", e);
    }
}

async function startNewSession() {
    try {
        const res = await authFetch('/api/chat/sessions', { method: 'POST' });
        if (res.ok) {
            const newSession = await res.json();
            await switchChatSession(newSession.id);
        }
    } catch(e) {
        console.error("Failed to create new session:", e);
    }
}

async function deleteChatSession(event, sessionId) {
    event.stopPropagation();
    if(confirm('Delete this chat session?')) {
        await authFetch(`/api/chat/sessions/${sessionId}`, { method: 'DELETE' });
        if (_currentSessionId === sessionId) {
            _currentSessionId = null;
            messages = [];
            chatHistory.innerHTML = '';
        }
        loadChatSessions();
    }
}

function switchView(viewId) {
    _currentView = viewId;
    document.querySelectorAll('.view-panel, .chat-container').forEach(el => el.style.display = 'none');
    
    const viewEl = document.getElementById(`view-${viewId}`);
    if(viewEl) viewEl.style.display = 'flex';
    
    // Update header title based on view
    const titleEl = document.getElementById('current-view-title');
    if (titleEl) {
        const titles = {
            'pools': '客户库管理',
            'personas': '客户画像配置',
            'workflows': '自动化工作流',
            'emails': '发件邮箱配置',
            'replies': '客户回信跟踪',
            'email-logs': '邮件发送记录'
        };
        titleEl.textContent = titles[viewId] || 'Workspace';
    }
    
    document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelector(`.nav-btn[data-view="${viewId}"]`)?.classList.add('active');

    if (viewId === 'pools') loadClientPools();
    if (viewId === 'personas') loadPersonas();
    if (viewId === 'workflows') loadWorkflows();
    if (viewId === 'emails') loadEmails();
    if (viewId === 'replies') loadReplies();
    if (viewId === 'email-logs') loadEmailLogs();
    if (viewId === 'users' && _authUser && _authUser.is_admin) loadUsers();
    
    // Start/restart polling (30s interval to avoid congestion on remote DB)
    if (_pollInterval) clearInterval(_pollInterval);
    _pollInterval = setInterval(() => {
        if (_currentView === 'workflows' && document.getElementById('workflow-modal').style.display !== 'flex') {
            loadWorkflows(true); // pass true to indicate background refresh (no loading indicators)
        }
        if (_currentView === 'pools' && document.getElementById('pool-modal').style.display !== 'flex') {
            loadClientPools();
            if (_currentPoolId && document.getElementById('pool-detail-modal').style.display === 'flex') {
                const activeBtn = document.querySelector('.filter-btn.active');
                loadPoolLeads(_currentPoolId, activeBtn ? activeBtn.getAttribute('data-status') : '');
            }
        }
    }, 30000);
}

// ─── Toasts ───
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = message;
    container.appendChild(toast);
    setTimeout(() => { if(toast.parentNode) toast.remove(); }, 3000);
}

// ─── Chat Logic ───
messageInput.addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = this.scrollHeight + 'px';
});
messageInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

function setStatus(mode, text) {
    statusBadge.className = 'status-badge ' + mode;
    statusBadge.querySelector('span').textContent = text;
}
function showProgress(text, pct) {
    progressBar.style.display = 'flex';
    progressText.textContent = text;
    progressFill.style.width = pct + '%';
}
function hideProgress() {
    progressBar.style.display = 'none';
    progressFill.style.width = '0%';
}

function quickCommand(text) {
    switchView('chat');
    messageInput.value = text;
    sendMessage();
}

function appendMessage(role, content) {
    const welcome = chatHistory.querySelector('.welcome-card');
    if (welcome) welcome.remove();

    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;

    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = role === 'user' ? 'U' : 'AI';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'content';

    if (role === 'agent') contentDiv.innerHTML = marked.parse(content);
    else contentDiv.textContent = content;

    if (role === 'user') {
        msgDiv.appendChild(contentDiv);
        msgDiv.appendChild(avatar);
    } else {
        msgDiv.appendChild(avatar);
        msgDiv.appendChild(contentDiv);
    }

    chatHistory.appendChild(msgDiv);
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

function showTyping() {
    const el = document.createElement('div');
    el.className = 'message agent';
    el.id = 'typing-indicator';
    el.innerHTML = `<div class="avatar">AI</div><div class="content"><div class="typing-dots"><span></span><span></span><span></span></div></div>`;
    chatHistory.appendChild(el);
    chatHistory.scrollTop = chatHistory.scrollHeight;
}
function removeTyping() {
    const el = document.getElementById('typing-indicator');
    if (el) el.remove();
}

async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text) return;

    messageInput.value = '';
    messageInput.style.height = 'auto';
    sendBtn.disabled = true;

    appendMessage('user', text);
    messages.push({ role: 'user', content: text });

    setStatus('working', '处理中...');
    showProgress('正在分析意图...', 10);
    showTyping();

    // Simulate progress steps to give user feedback during long backend operations
    let progressInterval = setInterval(() => {
        const currentText = document.getElementById('progress-text').textContent;
        if (currentText.includes('分析意图')) {
            showProgress('正在通过搜索引擎查找目标...', 35);
        } else if (currentText.includes('搜索引擎')) {
            showProgress('正在调用 Snov.io 接口提取联系人...', 65);
        } else if (currentText.includes('提取联系人')) {
            showProgress('正在汇总并生成最终结果...', 85);
        }
    }, 4000);

    try {
        if (!_currentSessionId) {
            const sessRes = await authFetch('/api/chat/sessions', { method: 'POST' });
            if (sessRes.ok) {
                const newSession = await sessRes.json();
                _currentSessionId = newSession.id;
            }
        }

        const response = await authFetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: _currentSessionId, messages: messages })
        });

        // Refresh sessions to update title if changed
        loadChatSessions();

        clearInterval(progressInterval);
        removeTyping();
        showProgress('完成！', 100);

        if (response.ok) {
            const data = await response.json();
            const reply = data.message.content || data.message;
            appendMessage('agent', reply);
            messages.push({ role: 'assistant', content: reply });
        } else {
            appendMessage('agent', '❌ 服务器返回错误。');
        }
    } catch (err) {
        clearInterval(progressInterval);
        removeTyping();
        appendMessage('agent', '❌ 网络错误。');
    } finally {
        sendBtn.disabled = false;
        messageInput.focus();
        setStatus('ready', '就绪');
        setTimeout(hideProgress, 1500);
    }
}

async function handleFileUpload(event) {
    const file = event.target.files[0];
    if (!file) return;

    setStatus('working', '解析文件中...');
    showProgress('正在提取文档内容...', 50);

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await authFetch('/api/parse_document', {
            method: 'POST',
            body: formData
        });

        if (response.ok) {
            const data = await response.json();
            if (data.success) {
                const currentVal = messageInput.value;
                messageInput.value = currentVal + (currentVal ? '\n\n' : '') + '--- 文件内容: ' + file.name + ' ---\n' + data.text + '\n-------------------\n';
                messageInput.style.height = 'auto';
                messageInput.style.height = messageInput.scrollHeight + 'px';
                appendMessage('agent', '📄 文件 "' + file.name + '" 解析成功，内容已填入输入框，请确认或添加额外指令后发送。');
            } else {
                appendMessage('agent', '❌ 文件解析失败：' + data.message);
            }
        } else {
            appendMessage('agent', '❌ 服务器错误。');
        }
    } catch (err) {
        appendMessage('agent', '❌ 网络错误。');
    } finally {
        setStatus('ready', '就绪');
        setTimeout(hideProgress, 1000);
        event.target.value = '';
    }
}

// ─── Modals ───
function showEmailModal() { document.getElementById('email-modal').style.display = 'flex'; }
function showWorkflowModal(id = null) {
    const form = document.getElementById('workflow-form');
    if (!id) {
        form.reset();
        form.removeAttribute('data-id');
    }
    document.getElementById('workflow-modal').style.display = 'flex';
    loadEmailCheckboxes();
    loadPoolSelect();
    loadPersonaSelect();
}
function closeModals() { document.querySelectorAll('.modal').forEach(m => m.style.display = 'none'); }

// ─── APIs: Workflows ───
async function loadWorkflows(isBackground = false) {
    const res = await authFetch('/api/workflows/');
    const wfs = await res.json();
    const list = document.getElementById('workflow-list');
    list.innerHTML = wfs.map(wf => {
        const statusColor = wf.status === 'active' ? 'var(--status-valid)' : 'var(--text-muted)';
        const statusText = wf.status === 'active' ? '● 运行中' : '○ 已暂停';
        return `
        <div class="card" style="border-left: 3px solid ${statusColor};">
            <div class="card-header">
                <div style="flex:1; min-width:0;">
                    <div class="card-title">${wf.name}</div>
                    <div class="card-subtitle" style="margin-top:4px;">🔑 关键词: ${wf.search_keywords || '—'}</div>
                </div>
                <div style="display:flex; gap:8px; align-items:center; flex-shrink:0;">
                    <button class="btn btn-quick btn-sm" onclick="editWorkflow(${wf.id})">编辑</button>
                    <button class="btn btn-quick btn-sm" onclick="deleteWorkflow(${wf.id})" style="color:var(--status-invalid); border-color: #fbd5ce;">删除</button>
                    <label class="toggle">
                        <input type="checkbox" ${wf.status === 'active' ? 'checked' : ''} onchange="toggleWorkflow(${wf.id})">
                        <span class="slider"></span>
                    </label>
                </div>
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:8px; margin-top:4px;">
                ${wf.client_pool_name ? `<span style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;background:var(--primary-bg);color:var(--primary);border-radius:20px;font-size:0.75rem;font-weight:600;">📁 ${wf.client_pool_name}</span>` : ''}
                <span style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;background:#E6F8F3;color:#00B894;border-radius:20px;font-size:0.75rem;font-weight:600;">📧 ${wf.emails.length} 个邮箱</span>
                <span style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;background:#FFF3E0;color:#E67E22;border-radius:20px;font-size:0.75rem;font-weight:600;">📊 每日 ${wf.daily_limit} 封 | ${wf.send_interval_min}-${wf.send_interval_max}s</span>
            </div>
            <div class="card-stats">
                <div class="stat-item">
                    <span class="stat-val">${wf.leads_count}</span>
                    <span class="stat-label">已挖掘</span>
                </div>
                <div class="stat-item">
                    <span class="stat-val" style="color:var(--status-valid);">${wf.replied_count}</span>
                    <span class="stat-label">已回复</span>
                </div>
                <div class="stat-item">
                    <span class="stat-val" style="color:${statusColor}; font-size:0.85rem;">${statusText}</span>
                    <span class="stat-label">状态</span>
                </div>
            </div>
        </div>
    `;}).join('');
}

async function editWorkflow(id) {
    const res = await authFetch('/api/workflows/');
    const wfs = await res.json();
    const wf = wfs.find(w => w.id === id);
    if (!wf) return;
    
    document.getElementById('wf-name').value = wf.name;
    document.getElementById('wf-keywords').value = wf.search_keywords;
    document.getElementById('wf-positions').value = wf.target_positions;
    document.getElementById('wf-prompt').value = wf.ai_prompt || '';
    document.getElementById('wf-signature').value = wf.email_signature || '';
    
    document.getElementById('wf-limit').value = wf.daily_limit;
    document.getElementById('wf-min-interval').value = wf.send_interval_min;
    document.getElementById('wf-max-interval').value = wf.send_interval_max;
    document.getElementById('wf-auto-followup').checked = wf.auto_followup;
    
    document.getElementById('workflow-form').setAttribute('data-id', wf.id);
    
    showWorkflowModal(id);
    
    // Set pool and persona select after a short delay
    setTimeout(() => {
        if (wf.client_pool_id) {
            document.getElementById('wf-pool-select').value = wf.client_pool_id;
        }
        if (wf.persona_id) {
            document.getElementById('wf-persona-select').value = wf.persona_id;
        }
    }, 150);
    
    // Check emails after a short delay to let them render
    setTimeout(() => {
        wf.emails.forEach(em => {
            const cb = document.querySelector(`#wf-email-checkboxes input[value="${em.id}"]`);
            if (cb) cb.checked = true;
        });
    }, 200);
}

async function toggleWorkflow(id) {
    try {
        const res = await authFetch(`/api/workflows/${id}/toggle`, { method: 'POST' });
        if (res.ok) {
            const data = await res.json();
            showToast(`工作流已${data.status === 'active' ? '启动' : '暂停'}`, 'success');
        } else {
            showToast('操作失败，请重试', 'error');
        }
    } catch (e) {
        showToast('网络错误', 'error');
    } finally {
        loadWorkflows();
    }
}

async function deleteWorkflow(id) {
    if(confirm('确定要删除此工作流及其所有相关的客户线索 (Leads) 吗？此操作不可恢复。')) {
        await authFetch(`/api/workflows/${id}`, { method: 'DELETE' });
        loadWorkflows();
    }
}

document.getElementById('workflow-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const checkboxes = document.querySelectorAll('#wf-email-checkboxes input:checked');
    const emailIds = Array.from(checkboxes).map(cb => parseInt(cb.value));
    
    const data = {
        name: document.getElementById('wf-name').value,
        search_keywords: document.getElementById('wf-keywords').value,
        target_positions: document.getElementById('wf-positions').value,
        ai_prompt: document.getElementById('wf-prompt').value,
        email_signature: document.getElementById('wf-signature').value,
        client_pool_id: document.getElementById('wf-pool-select').value ? parseInt(document.getElementById('wf-pool-select').value) : null,
        persona_id: document.getElementById('wf-persona-select').value ? parseInt(document.getElementById('wf-persona-select').value) : null,
        daily_limit: parseInt(document.getElementById('wf-limit').value),
        send_interval_min: parseInt(document.getElementById('wf-min-interval').value),
        send_interval_max: parseInt(document.getElementById('wf-max-interval').value),
        auto_followup: document.getElementById('wf-auto-followup').checked,
        email_account_ids: emailIds
    };
    
    const id = document.getElementById('workflow-form').getAttribute('data-id');
    const url = id ? `/api/workflows/${id}` : '/api/workflows/';
    const method = id ? 'PUT' : 'POST';
    
    await authFetch(url, {
        method: method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    closeModals();
    loadWorkflows();
});

// ─── APIs: Emails ───
async function loadEmails() {
    const res = await authFetch('/api/email_accounts/');
    const emails = await res.json();
    const list = document.getElementById('email-list');
    list.innerHTML = emails.map(em => `
        <div class="card">
            <div class="card-header">
                <div class="card-title">${em.email}</div>
                <button class="btn btn-quick" onclick="deleteEmail(${em.id})" style="border:none; color:var(--red)">删除</button>
            </div>
            <div class="card-subtitle">SMTP: ${em.smtp_host}:${em.smtp_port}</div>
            <div class="card-subtitle">IMAP: ${em.imap_host || '未配置'}:${em.imap_port}</div>
        </div>
    `).join('');
}

async function loadEmailCheckboxes() {
    const res = await authFetch('/api/email_accounts/');
    const emails = await res.json();
    const container = document.getElementById('wf-email-checkboxes');
    container.innerHTML = emails.map(em => `
        <label style="display:block; margin-bottom:4px; font-size:0.85rem">
            <input type="checkbox" value="${em.id}"> ${em.email}
        </label>
    `).join('');
}

document.getElementById('email-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = {
        email: document.getElementById('smtp-email').value,
        display_name: document.getElementById('smtp-display').value,
        smtp_host: document.getElementById('smtp-host').value,
        smtp_port: parseInt(document.getElementById('smtp-port').value),
        smtp_user: document.getElementById('smtp-email').value,
        smtp_pass: document.getElementById('smtp-pass').value,
        imap_host: document.getElementById('imap-host').value,
        imap_port: parseInt(document.getElementById('imap-port').value),
        use_ssl: document.getElementById('smtp-ssl').checked,
        use_tls: document.getElementById('smtp-tls').checked
    };
    await authFetch('/api/email_accounts/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    closeModals();
    if(document.getElementById('view-emails').style.display !== 'none') loadEmails();
});

async function deleteEmail(id) {
    if(confirm('确定要删除此邮箱吗？')) {
        await authFetch(`/api/email_accounts/${id}`, { method: 'DELETE' });
        loadEmails();
    }
}

async function testSmtp() { alert('SMTP 测试成功'); }
async function testImap() { alert('IMAP 测试成功'); }

// ─── APIs: Replies ───
async function loadReplies() {
    const btn = document.getElementById('btn-refresh-replies');
    if(btn) btn.classList.add('loading');
    
    try {
        const res = await authFetch('/api/replies');
        const replies = await res.json();
        const list = document.getElementById('reply-list');
        
        if (!replies.length) {
            list.innerHTML = `<div class="leads-empty">暂无客户回信 📭</div>`;
            return;
        }
        
        list.innerHTML = replies.map(r => {
            const timeStr = r.last_reply_at ? new Date(r.last_reply_at).toLocaleString('zh-CN') : '未知时间';
            const name = r.first_name ? `${r.first_name} ${r.last_name || ''}` : r.email;
            
            return `
            <div class="timeline-item">
                <div class="reply-header">
                    <span class="reply-sender">${name} (${r.company_name})</span>
                    <span class="reply-time">${timeStr}</span>
                </div>
                <div class="reply-snippet">
                    "${r.reply_snippet || '（无正文提取）'}"
                </div>
                <div class="reply-actions" style="margin-top:12px; display:flex; gap:12px; align-items:center;">
                    <span class="status-pill replied">已回复 (跟进:${r.followup_count})</span>
                    ${r.ai_draft ? `<button class="btn btn-primary" style="padding:4px 12px; margin:0" onclick="quickCommand('查看草稿并回复: ${r.email}')">查看 AI 草稿并回复</button>` : ''}
                </div>
            </div>`;
        }).join('');
    } catch(e) {
        showToast('加载回信失败', 'error');
    } finally {
        if(btn) btn.classList.remove('loading');
    }
}

// ─── APIs: Email Logs ───
async function loadEmailLogs() {
    const btn = document.getElementById('btn-refresh-logs');
    if(btn) btn.classList.add('loading');
    
    try {
        const res = await authFetch('/api/email_logs?direction=outbound&limit=100');
        const logs = await res.json();
        const tbody = document.getElementById('email-logs-tbody');
        
        if (!logs.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="leads-empty">暂无发信记录</td></tr>';
            return;
        }
        
        tbody.innerHTML = logs.map(l => {
            const timeStr = l.sent_at ? new Date(l.sent_at).toLocaleString('zh-CN') : '未知时间';
            return `
                <tr>
                    <td>${timeStr}</td>
                    <td title="${l.to_email}">${l.to_email}</td>
                    <td title="${l.lead_company}">${l.lead_name}<br><small style="color:var(--text-muted)">${l.lead_company}</small></td>
                    <td><span class="status-pill found" style="text-transform:none">${l.from_email}</span></td>
                    <td title="${l.subject}">${l.subject}</td>
                </tr>
            `;
        }).join('');
    } catch(e) {
        showToast('加载记录失败', 'error');
    } finally {
        if(btn) btn.classList.remove('loading');
    }
}

// ─── APIs: Personas ───
let currentEditingPersonaId = null;

async function loadPersonas() {
    const res = await authFetch('/api/personas/');
    const personas = await res.json();
    const list = document.getElementById('persona-list');
    if (!personas.length) {
        list.innerHTML = `<div class="leads-empty">还没有客户画像，点击右上角创建一个吧 👥</div>`;
        return;
    }
    list.innerHTML = personas.map(p => `
        <div class="card">
            <div class="card-header">
                <div>
                    <div class="card-title">${p.name}</div>
                    <div class="card-subtitle">🎯 ${p.target_industry || '未指定'} | 📍 ${p.target_countries || '未指定'}</div>
                </div>
                <div style="display:flex; gap:8px; align-items:center;">
                    <button class="btn btn-quick btn-sm" onclick="editPersona(${p.id})">编辑</button>
                    <button class="btn btn-quick btn-sm" onclick="deletePersona(${p.id})" style="color:var(--status-invalid); border-color: #fbd5ce;">删除</button>
                </div>
            </div>
            <div class="card-subtitle" style="font-size:0.75rem; color:var(--text-primary); margin-top:12px;">
                <strong>目标职位:</strong> ${p.target_roles || '无'}<br>
                <strong>关键词:</strong> ${p.target_keywords || '无'}<br>
                ${p.negative_keywords ? `<strong>排除词:</strong> ${p.negative_keywords}<br>` : ''}
            </div>
            ${p.ai_prompt_template ? `
            <div style="margin-top:12px; padding:8px; background:var(--bg-main); border-radius:4px; font-size:0.75rem; border-left:2px solid var(--primary);">
                <strong>写信指导:</strong> ${p.ai_prompt_template}
            </div>
            ` : ''}
        </div>
    `).join('');
}

function showPersonaModal() {
    currentEditingPersonaId = null;
    document.getElementById('persona-form').reset();
    document.getElementById('persona-modal').style.display = 'flex';
}

async function editPersona(id) {
    const res = await authFetch('/api/personas/');
    const personas = await res.json();
    const p = personas.find(x => x.id === id);
    if(p) {
        currentEditingPersonaId = p.id;
        document.getElementById('persona-name').value = p.name;
        document.getElementById('persona-industry').value = p.target_industry || '';
        document.getElementById('persona-countries').value = p.target_countries || '';
        document.getElementById('persona-positions').value = p.target_roles || '';
        document.getElementById('persona-keywords').value = p.target_keywords || '';
        document.getElementById('persona-negative').value = p.negative_keywords || '';
        document.getElementById('persona-notes').value = p.ai_prompt_template || '';
        document.getElementById('persona-modal').style.display = 'flex';
    }
}

document.getElementById('persona-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
        name: document.getElementById('persona-name').value,
        target_industry: document.getElementById('persona-industry').value,
        target_countries: document.getElementById('persona-countries').value,
        target_roles: document.getElementById('persona-positions').value,
        target_keywords: document.getElementById('persona-keywords').value,
        negative_keywords: document.getElementById('persona-negative').value,
        ai_prompt_template: document.getElementById('persona-notes').value
    };

    let url = '/api/personas/';
    let method = 'POST';
    if(currentEditingPersonaId) {
        url = `/api/personas/${currentEditingPersonaId}`;
        method = 'PUT';
    }

    const res = await authFetch(url, {
        method: method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    if(res.ok) {
        closeModals();
        loadPersonas();
        showToast('客户画像保存成功', 'success');
    } else {
        showToast('保存失败', 'error');
    }
});

async function deletePersona(id) {
    if(!confirm('确定要删除这个画像吗？删除后不可恢复。')) return;
    const res = await authFetch(`/api/personas/${id}`, { method: 'DELETE' });
    if(res.ok) {
        loadPersonas();
        showToast('已删除', 'success');
    }
}

// ─── APIs: Client Pools ───
async function loadClientPools() {
    const res = await authFetch('/api/client_pools/');
    const pools = await res.json();
    const list = document.getElementById('pool-list');
    if (!pools.length) {
        list.innerHTML = `<div class="leads-empty">还没有客户库，点击右上角创建一个吧 📁</div>`;
        return;
    }
    list.innerHTML = pools.map(pool => `
        <div class="card">
            <div class="card-header">
                <div>
                    <div class="card-title">${pool.name}</div>
                    <div class="card-subtitle">${pool.description || '无描述'}</div>
                </div>
                <div style="display:flex; gap:8px; align-items:center;">
                    <button class="btn btn-quick btn-sm" onclick="viewPoolDetail(${pool.id})">详情</button>
                    <button class="btn btn-quick btn-sm" onclick="editPool(${pool.id})">编辑</button>
                    <button class="btn btn-quick btn-sm" onclick="deletePool(${pool.id})" style="color:var(--status-invalid); border-color: #fbd5ce;">删除</button>
                </div>
            </div>
            ${pool.excluded_domains ? `<div class="card-subtitle" style="font-size:0.72rem;">🚫 排除: ${pool.excluded_domains}</div>` : ''}
            <div class="card-stats" style="grid-template-columns: repeat(4, 1fr);">
                <div class="stat-item">
                    <span class="stat-val">${pool.total_leads}</span>
                    <span class="stat-label">总客户</span>
                </div>
                <div class="stat-item">
                    <span class="stat-val" style="color:var(--accent)">${pool.contacted_leads}</span>
                    <span class="stat-label">已联系</span>
                </div>
                <div class="stat-item">
                    <span class="stat-val" style="color:var(--green)">${pool.replied_leads}</span>
                    <span class="stat-label">已回复</span>
                </div>
                <div class="stat-item">
                    <span class="stat-val">${pool.workflow_count}</span>
                    <span class="stat-label">工作流</span>
                </div>
            </div>
        </div>
    `).join('');
}

function showPoolModal(id = null) {
    const form = document.getElementById('pool-form');
    if (!id) {
        form.reset();
        form.removeAttribute('data-id');
    }
    document.getElementById('pool-modal').style.display = 'flex';
}

async function editPool(id) {
    const res = await authFetch(`/api/client_pools/${id}`);
    const pool = await res.json();
    document.getElementById('pool-name').value = pool.name;
    document.getElementById('pool-desc').value = pool.description || '';
    document.getElementById('pool-excluded').value = pool.excluded_domains || '';
    document.getElementById('pool-form').setAttribute('data-id', pool.id);
    showPoolModal(id);
}

async function deletePool(id) {
    if (confirm('确定要删除此客户库吗？所有归属于该库的客户线索 (Lead) 将被删除！此操作不可恢复。')) {
        await authFetch(`/api/client_pools/${id}`, { method: 'DELETE' });
        loadClientPools();
    }
}

document.getElementById('pool-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = {
        name: document.getElementById('pool-name').value,
        description: document.getElementById('pool-desc').value,
        excluded_domains: document.getElementById('pool-excluded').value
    };
    const id = document.getElementById('pool-form').getAttribute('data-id');
    const url = id ? `/api/client_pools/${id}` : '/api/client_pools/';
    const method = id ? 'PUT' : 'POST';
    await authFetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    closeModals();
    loadClientPools();
});

// Pool select for workflow modal
async function loadPoolSelect() {
    try {
        const res = await authFetch('/api/client_pools/');
        const pools = await res.json();
        const select = document.getElementById('wf-pool-select');
        if (select) {
            select.innerHTML = '<option value="">-- 不绑定 --</option>' + pools.map(p => `<option value="${p.id}">${p.name} (${p.total_leads} 客户)</option>`).join('');
        }
    } catch(e) {
        console.error('loadPoolSelect error:', e);
    }
}

// Persona select for workflow modal
let _personasCache = [];
async function loadPersonaSelect() {
    try {
        const res = await authFetch('/api/personas/');
        _personasCache = await res.json();
        const select = document.getElementById('wf-persona-select');
        if (select) {
            select.innerHTML = '<option value="">-- 不绑定画像 --</option>' + _personasCache.map(p => `<option value="${p.id}">${p.name}</option>`).join('');
        }
    } catch(e) {
        console.error('loadPersonaSelect error:', e);
    }
}

function handlePersonaChange() {
    const select = document.getElementById('wf-persona-select');
    if (!select || !select.value) return;
    
    const persona = _personasCache.find(p => p.id == select.value);
    if (!persona) return;
    
    if (persona.target_keywords) document.getElementById('wf-keywords').value = persona.target_keywords;
    if (persona.target_roles) document.getElementById('wf-positions').value = persona.target_roles;
    if (persona.ai_prompt_template) document.getElementById('wf-prompt').value = persona.ai_prompt_template;
    showToast('✨ 已自动填充画像内容', 'success');
}

// Pool Detail Modal
let _currentPoolId = null;
async function viewPoolDetail(poolId) {
    _currentPoolId = poolId;
    const res = await authFetch(`/api/client_pools/${poolId}`);
    const pool = await res.json();
    
    document.getElementById('pool-detail-title').textContent = `📁 ${pool.name}`;
    document.getElementById('pool-detail-stats').innerHTML = `
        <div class="pool-stat-card">
            <span class="stat-val">${pool.total_leads}</span>
            <span class="stat-label">总客户</span>
        </div>
        <div class="pool-stat-card">
            <span class="stat-val" style="color:var(--accent)">${pool.contacted_leads}</span>
            <span class="stat-label">已联系</span>
        </div>
        <div class="pool-stat-card">
            <span class="stat-val" style="color:var(--green)">${pool.replied_leads}</span>
            <span class="stat-label">已回复</span>
        </div>
        <div class="pool-stat-card">
            <span class="stat-val">${pool.workflow_count}</span>
            <span class="stat-label">关联工作流</span>
        </div>
    `;
    
    // Reset filter to 'all'
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    document.querySelector('.filter-btn[data-status=""]').classList.add('active');
    
    await loadPoolLeads(poolId, '');
    document.getElementById('pool-detail-modal').style.display = 'flex';
}

async function loadPoolLeads(poolId, status) {
    const url = status ? `/api/client_pools/${poolId}/leads?status=${status}` : `/api/client_pools/${poolId}/leads`;
    const res = await authFetch(url);
    const leads = await res.json();
    const tbody = document.getElementById('pool-leads-tbody');
    
    if (!leads.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="leads-empty">暂无客户数据</td></tr>';
        return;
    }
    
    tbody.innerHTML = leads.map(l => {
        const name = [l.first_name, l.last_name].filter(Boolean).join(' ') || '—';
        const created = l.created_at ? new Date(l.created_at).toLocaleDateString('zh-CN') : '—';
        const linkedin = l.linkedin_url ? `<a href="${l.linkedin_url}" target="_blank" style="color:var(--primary);text-decoration:none;">🔗 领英</a>` : '—';
        return `
            <tr>
                <td title="${name}">${name}</td>
                <td title="${l.email || ''}">${l.email || '—'}</td>
                <td>${linkedin}</td>
                <td title="${l.company_name || l.domain}">${l.company_name || l.domain}</td>
                <td>${l.job_title || '—'}</td>
                <td><span class="status-pill ${l.status}">${l.status}</span></td>
                <td>${created}</td>
                <td><button class="btn btn-quick btn-sm" onclick="editLead(${l.id})">编辑</button></td>
            </tr>
        `;
    }).join('');
}

function filterPoolLeads(status) {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    document.querySelector(`.filter-btn[data-status="${status}"]`).classList.add('active');
    if (_currentPoolId) loadPoolLeads(_currentPoolId, status);
}

// ─── Export CSV ───
function exportPoolCSV() {
    if (!_currentPoolId) return;
    const btn = document.getElementById('btn-export-csv');
    if(btn) btn.classList.add('loading');
    
    // Find active status filter
    const activeBtn = document.querySelector('.filter-btn.active');
    const status = activeBtn ? activeBtn.getAttribute('data-status') : '';
    
    let url = `/api/export/leads?pool_id=${_currentPoolId}`;
    if (status) url += `&status=${status}`;
    
    // Trigger download
    window.location.href = url;
    
    setTimeout(() => {
        if(btn) btn.classList.remove('loading');
        showToast('✅ 导出已开始', 'success');
    }, 1000);
}

// ─── APIs: Leads ───
let _currentLeadsCache = [];

async function editLead(id) {
    // We fetch the leads again or use a cache. Let's fetch it directly if we can, or just use the pool leads fetch.
    const url = `/api/client_pools/${_currentPoolId}/leads`;
    const res = await authFetch(url);
    const leads = await res.json();
    _currentLeadsCache = leads;
    
    const lead = leads.find(l => l.id === id);
    if (!lead) return;
    
    document.getElementById('lead-first-name').value = lead.first_name || '';
    document.getElementById('lead-last-name').value = lead.last_name || '';
    document.getElementById('lead-email').value = lead.email || '';
    document.getElementById('lead-company').value = lead.company_name || '';
    document.getElementById('lead-domain').value = lead.domain || '';
    document.getElementById('lead-linkedin').value = lead.linkedin_url || '';
    document.getElementById('lead-job-title').value = lead.job_title || '';
    document.getElementById('lead-status').value = lead.status || 'found';
    
    document.getElementById('lead-form').setAttribute('data-id', lead.id);
    document.getElementById('lead-modal').style.display = 'flex';
}

document.getElementById('lead-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = document.getElementById('lead-form').getAttribute('data-id');
    if (!id) return;
    
    // We need workflow_id and client_pool_id to satisfy LeadCreate schema
    const lead = _currentLeadsCache.find(l => l.id === parseInt(id));
    if (!lead) return;

    const data = {
        workflow_id: lead.workflow_id,
        client_pool_id: lead.client_pool_id,
        first_name: document.getElementById('lead-first-name').value,
        last_name: document.getElementById('lead-last-name').value,
        email: document.getElementById('lead-email').value,
        linkedin_url: document.getElementById('lead-linkedin').value || null,
        company_name: document.getElementById('lead-company').value,
        domain: document.getElementById('lead-domain').value,
        job_title: document.getElementById('lead-job-title').value,
        status: document.getElementById('lead-status').value
    };
    
    const res = await authFetch(`/api/leads/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    
    if (res.ok) {
        document.getElementById('lead-modal').style.display = 'none';
        showToast('✅ 客户资料已更新', 'success');
        
        // Refresh the leads table
        const activeBtn = document.querySelector('.filter-btn.active');
        const status = activeBtn ? activeBtn.getAttribute('data-status') : '';
        loadPoolLeads(_currentPoolId, status);
    } else {
        showToast('❌ 更新失败', 'error');
    }
});

// ─── Engine Logs Modal ───
async function showEngineLogsModal() {
    document.getElementById('logs-modal').style.display = 'flex';
    await refreshEngineLogs();
}

async function refreshEngineLogs() {
    try {
        const pre = document.getElementById('engine-logs-pre');
        pre.innerHTML = '正在加载日志...';
        const res = await authFetch('/api/engine_logs');
        const data = await res.json();
        pre.innerHTML = data.logs || '（暂无日志内容）';
        pre.scrollTop = pre.scrollHeight; // Auto-scroll to bottom
    } catch (e) {
        showToast('加载日志失败', 'error');
    }
}

// init
loadChatSessions();
switchView('chat');

// Show logged-in user info
if (_authUser) {
    const navUser = document.getElementById('nav-username');
    if (navUser) {
        navUser.textContent = (_authUser.display_name || _authUser.username) + (_authUser.is_admin ? ' (管理员)' : '');
    }
    if (_authUser.is_admin) {
        const adminMenu = document.getElementById('admin-menu');
        if (adminMenu) adminMenu.style.display = 'block';
    }
}

// ─── User Management (Admin Only) ───
async function loadUsers() {
    try {
        const res = await authFetch('/api/auth/users');
        if (res.ok) {
            const users = await res.json();
            const tbody = document.getElementById('users-tbody');
            if (!tbody) return;
            if (users.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;">暂无用户</td></tr>';
                return;
            }
            tbody.innerHTML = users.map(u => `
                <tr>
                    <td>${u.id}</td>
                    <td>${u.username}</td>
                    <td>${u.display_name || '—'}</td>
                    <td>${u.is_admin ? '<span class="status-badge" style="background:rgba(124,92,252,0.1);color:#7c5cfc;">管理员</span>' : '普通用户'}</td>
                    <td>${u.is_active ? '<span style="color:#00e676;">启用</span>' : '<span style="color:#ff5252;">禁用</span>'}</td>
                    <td>${new Date(u.created_at).toLocaleDateString()}</td>
                </tr>
            `).join('');
        }
    } catch (e) {
        console.error("Load users failed:", e);
    }
}

function showUserModal() {
    document.getElementById('user-form').reset();
    document.getElementById('user-modal').style.display = 'flex';
}

function closeUserModal() {
    document.getElementById('user-modal').style.display = 'none';
}

async function saveUser(e) {
    e.preventDefault();
    const payload = {
        username: document.getElementById('user-username').value,
        password: document.getElementById('user-password').value,
        display_name: document.getElementById('user-displayname').value,
        is_admin: document.getElementById('user-isadmin').checked
    };
    try {
        const res = await authFetch('/api/auth/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            showToast('账号创建成功', 'success');
            closeUserModal();
            loadUsers();
        } else {
            const err = await res.json();
            showToast('创建失败: ' + (err.detail || '未知错误'), 'error');
        }
    } catch (e) {
        showToast('请求失败', 'error');
    }
}
