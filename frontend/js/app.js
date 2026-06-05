document.addEventListener('DOMContentLoaded', () => {
    // Navigation
    const navLinks = document.querySelectorAll('.nav-links li[data-target]');
    const modules = document.querySelectorAll('.module');

    navLinks.forEach(link => {
        link.addEventListener('click', () => {
            navLinks.forEach(l => l.classList.remove('active'));
            modules.forEach(m => m.classList.remove('active'));
            
            link.classList.add('active');
            document.getElementById(link.dataset.target).classList.add('active');
            
            if (link.dataset.target === 'academic-module') loadAcademicData();
            if (link.dataset.target === 'meals-module') loadMealsData();
            if (link.dataset.target === 'reading-module') loadReadingData();
            if (link.dataset.target === 'memory-module') {
                loadPapersData();
            }
        });
    });

    // Memory Hub Search Binding
    const memorySearchInput = document.getElementById('memory-search-input');
    const memorySearchScope = document.getElementById('memory-search-scope');
    const memorySearchBtn = document.getElementById('memory-search-btn');
    if (memorySearchBtn) {
        memorySearchBtn.addEventListener('click', () => {
            loadPapersData(memorySearchInput.value, memorySearchScope ? memorySearchScope.value : 'all');
        });
    }
    if (memorySearchInput) {
        memorySearchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                loadPapersData(memorySearchInput.value, memorySearchScope ? memorySearchScope.value : 'all');
            }
        });
    }

    const mealDateInput = document.getElementById('meal-date-input');
    const mealTypeInput = document.getElementById('meal-type-input');
    const mealContentInput = document.getElementById('meal-content-input');
    const addMealBtn = document.getElementById('add-meal-btn');
    const mealAnalysisDays = document.getElementById('meal-analysis-days');
    const foodRuleNameInput = document.getElementById('food-rule-name-input');
    const foodRuleNotesInput = document.getElementById('food-rule-notes-input');
    const addFoodRuleBtn = document.getElementById('add-food-rule-btn');
    if (mealDateInput) {
        const now = new Date();
        const localDate = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
        mealDateInput.value = localDate;
    }
    if (addMealBtn) {
        addMealBtn.addEventListener('click', addMealLog);
    }
    if (mealContentInput) {
        mealContentInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                addMealLog();
            }
        });
    }
    if (mealAnalysisDays) {
        mealAnalysisDays.addEventListener('change', loadMealsData);
    }
    if (addFoodRuleBtn) {
        addFoodRuleBtn.addEventListener('click', addFoodRule);
    }
    if (foodRuleNameInput) {
        foodRuleNameInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                addFoodRule();
            }
        });
    }

    // Chat / WebSocket
    const chatLog = document.getElementById('chat-log');
    const chatInput = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const conversationList = document.getElementById('conversation-list');
    const newConversationBtn = document.getElementById('new-conversation-btn');
    
    let ws = null;
    let reconnectTimer = null;
    let reconnectAttempts = 0;
    let disconnectNotice = null;
    let currentConversationId = localStorage.getItem('dailyAgentConversationId') || '';

    let thinkingBubble = null;
    let streamingMessage = null;
    let streamingBubble = null;
    let streamingText = '';

    function wsUrl() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${protocol}//${window.location.host}/ws/chat`;
    }

    function setSendEnabled(enabled) {
        sendBtn.disabled = !enabled;
        sendBtn.style.opacity = enabled ? '' : '0.55';
        sendBtn.style.cursor = enabled ? '' : 'not-allowed';
    }

    function showConnectionNotice(text) {
        if (!disconnectNotice) {
            disconnectNotice = document.createElement('div');
            disconnectNotice.className = 'connection-notice';
            chatLog.appendChild(disconnectNotice);
        }
        disconnectNotice.textContent = text;
        chatLog.scrollTop = chatLog.scrollHeight;
    }

    function clearConnectionNotice() {
        if (disconnectNotice) {
            disconnectNotice.remove();
            disconnectNotice = null;
        }
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        const delay = Math.min(10000, 800 * Math.max(1, reconnectAttempts));
        showConnectionNotice(`WebSocket disconnected. Reconnecting in ${(delay / 1000).toFixed(1)}s...`);
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            reconnectAttempts += 1;
            connectWebSocket();
        }, delay);
    }

    function connectWebSocket() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        setSendEnabled(false);
        showConnectionNotice('Connecting to agent...');
        ws = new WebSocket(wsUrl());

        ws.onopen = () => {
            reconnectAttempts = 0;
            clearConnectionNotice();
            setSendEnabled(true);
            if (currentConversationId) {
                ws.send(JSON.stringify({type: 'open_conversation', conversation_id: currentConversationId}));
            }
        };

        ws.onmessage = handleWsMessage;

        ws.onerror = () => {
            showConnectionNotice('WebSocket error. Checking connection...');
        };

        ws.onclose = () => {
            hideThinking();
            finishStreamingMessage();
            setSendEnabled(false);
            scheduleReconnect();
        };
    }

    function showThinking() {
        if (thinkingBubble) return; // Already thinking
        
        const div = document.createElement('div');
        div.className = 'message agent thinking-bubble';
        div.id = 'thinking-bubble';
        
        const bubble = document.createElement('div');
        bubble.className = 'bubble';
        
        const container = document.createElement('div');
        container.className = 'thinking-container';
        container.innerHTML = `
            <div class="thinking-spinner"></div>
            <span class="thinking-text">Thinking...</span>
        `;
        
        bubble.appendChild(container);
        div.appendChild(bubble);
        chatLog.appendChild(div);
        chatLog.scrollTop = chatLog.scrollHeight;
        
        thinkingBubble = div;
    }

    function hideThinking() {
        if (thinkingBubble) {
            thinkingBubble.remove();
            thinkingBubble = null;
        }
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[ch]));
    }

    function safeClass(value) {
        return String(value ?? '').replace(/[^\w\u4e00-\u9fa5-]/g, '-');
    }

    function statusClass(value) {
        const status = String(value ?? '');
        if (status.includes('成功') || status === 'COMPLETED') return 'completed';
        if (status.includes('失败') || status === 'FAILED') return 'failed';
        if (status.includes('运行') || status === 'RUNNING') return 'running';
        return 'pending';
    }

    function safeImagePath(filename) {
        return encodeURIComponent(String(filename ?? '').split('/').pop());
    }

    function safeExternalUrl(url) {
        try {
            const parsed = new URL(url);
            return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : '';
        } catch (e) {
            return '';
        }
    }

    function setStatus(element, message, colorVar) {
        element.innerHTML = `<span style="color:${colorVar}">${escapeHtml(message)}</span>`;
    }

    function displayMealType(type) {
        const mapping = {
            breakfast: '早餐',
            lunch: '午餐',
            dinner: '晚餐',
            snack: '夜宵',
            night: '夜宵',
            other: '其他'
        };
        return mapping[String(type || '').toLowerCase()] || type || '其他';
    }

    function handleWsMessage(event) {
        const data = JSON.parse(event.data);
        
        if (data.type === 'ask_permission') {
            const intent = data.intent;
            const input = data.tool_input;
            hideThinking(); // Hide thinking indicator on user interaction requests
            appendPermissionCard(intent, input);
        } else if (data.type === 'log') {
            appendLog(data.content);
        } else if (data.type === 'message') {
            hideThinking();
            appendMessage('agent', data.content);
        } else if (data.type === 'message_start') {
            hideThinking();
            startStreamingMessage();
        } else if (data.type === 'message_delta') {
            appendStreamingDelta(data.content || '');
        } else if (data.type === 'message_end') {
            finishStreamingMessage();
        } else if (data.type === 'done') {
            hideThinking();
            finishStreamingMessage();
        } else if (data.type === 'conversation') {
            applyConversation(data.conversation, data.conversations, true);
        } else if (data.type === 'conversation_saved') {
            applyConversation(data.conversation, data.conversations, false);
            if (data.compressed) {
                console.log('Conversation compressed');
            }
        } else if (data.type === 'error') {
            hideThinking();
            finishStreamingMessage();
            appendMessage('system', 'Error: ' + data.content);
        } else if (data.type === 'status') {
            clearConnectionNotice();
        }
    }

    function sendMessage() {
        const text = chatInput.value.trim();
        if (!text) return;
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            showConnectionNotice('Agent is reconnecting. Please wait a moment...');
            connectWebSocket();
            return;
        }
        
        appendMessage('user', text);
        ws.send(JSON.stringify({ type: 'chat', content: text, conversation_id: currentConversationId }));
        chatInput.value = '';
        
        showThinking(); // Show neon glowing spinner circle immediately!
    }

    sendBtn.addEventListener('click', sendMessage);
    chatInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    connectWebSocket();

    async function loadConversations() {
        try {
            const res = await fetch('/api/conversations');
            const conversations = await res.json();
            renderConversationList(conversations);
            if (!currentConversationId && conversations.length) {
                currentConversationId = conversations[0].id;
                localStorage.setItem('dailyAgentConversationId', currentConversationId);
            }
            if (currentConversationId) {
                await openConversation(currentConversationId);
            } else {
                await createNewConversation();
            }
        } catch (err) {
            console.error('Failed to load conversations', err);
        }
    }

    function renderConversationList(conversations) {
        if (!conversationList) return;
        const items = conversations || [];
        if (!items.length) {
            conversationList.innerHTML = '<div class="conversation-empty">暂无历史对话</div>';
            return;
        }
        conversationList.innerHTML = items.map(conv => `
            <div class="conversation-item ${conv.id === currentConversationId ? 'active' : ''}" data-id="${escapeHtml(conv.id)}">
                <button class="conversation-open" title="${escapeHtml(conv.title || '新对话')}">
                    <span class="conversation-title">${escapeHtml(conv.title || '新对话')}</span>
                </button>
                <button class="conversation-delete" title="删除对话" aria-label="删除对话">×</button>
            </div>
        `).join('');
        conversationList.querySelectorAll('.conversation-open').forEach(btn => {
            btn.addEventListener('click', () => {
                const item = btn.closest('.conversation-item');
                openConversation(item ? item.dataset.id : '');
            });
        });
        conversationList.querySelectorAll('.conversation-delete').forEach(btn => {
            btn.addEventListener('click', (event) => {
                event.stopPropagation();
                const item = btn.closest('.conversation-item');
                if (item) deleteConversation(item.dataset.id);
            });
        });
    }

    async function openConversation(conversationId) {
        if (!conversationId) return;
        const res = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}`);
        const conversation = await res.json();
        applyConversation(conversation, null, true);
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({type: 'open_conversation', conversation_id: conversation.id}));
        }
    }

    async function createNewConversation() {
        const res = await fetch('/api/conversations', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({})
        });
        const conversation = await res.json();
        applyConversation(conversation, null, true);
        loadConversations();
    }

    async function deleteConversation(conversationId) {
        if (!conversationId) return;
        if (!confirm('删除这个对话？')) return;
        const res = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}`, { method: 'DELETE' });
        const data = await res.json();
        applyConversation(data.conversation, data.conversations, true);
        if (ws && ws.readyState === WebSocket.OPEN && data.conversation && data.conversation.id) {
            ws.send(JSON.stringify({type: 'open_conversation', conversation_id: data.conversation.id}));
        }
    }

    function applyConversation(conversation, conversations, rerenderMessages) {
        if (!conversation || !conversation.id) return;
        currentConversationId = conversation.id;
        localStorage.setItem('dailyAgentConversationId', currentConversationId);
        if (conversations) renderConversationList(conversations);
        else if (conversationList && ![...conversationList.querySelectorAll('.conversation-item')].some(item => item.dataset.id === currentConversationId)) {
            loadConversations();
        }
        if (conversationList) {
            conversationList.querySelectorAll('.conversation-item').forEach(item => {
                item.classList.toggle('active', item.dataset.id === currentConversationId);
            });
        }
        if (rerenderMessages) renderConversationMessages(conversation);
    }

    function renderConversationMessages(conversation) {
        chatLog.innerHTML = '';
        if (conversation.summary) {
            appendMessage('system', `历史摘要：${conversation.summary}`);
        }
        const messages = conversation.messages || [];
        if (!messages.length && !conversation.summary) {
            appendMessage('system', 'System Initialized. Awaiting your command.');
            return;
        }
        messages.forEach(msg => {
            if (msg.role === 'user' || msg.role === 'assistant') {
                appendMessage(msg.role === 'assistant' ? 'agent' : 'user', msg.content || '');
            }
        });
    }

    if (newConversationBtn) {
        newConversationBtn.addEventListener('click', () => {
            createNewConversation();
        });
    }
    loadConversations();

    function appendPermissionCard(intent, input) {
        const div = document.createElement('div');
        div.className = 'message system permission-request';
        
        const bubble = document.createElement('div');
        bubble.className = 'bubble permission-bubble';
        
        const titleDiv = document.createElement('div');
        titleDiv.className = 'permission-title';
        titleDiv.textContent = '⚠️ Action Approval Required';
        bubble.appendChild(titleDiv);
        
        const metaDiv = document.createElement('div');
        metaDiv.className = 'permission-meta';
        metaDiv.innerHTML = `Risk Level: <strong style="color: ${intent.risk === 'high' ? 'var(--danger)' : '#ffb703'}">${escapeHtml(intent.risk)}</strong> | Tool: <strong>${escapeHtml(intent.tool)}</strong>`;
        bubble.appendChild(metaDiv);
        
        const codePre = document.createElement('pre');
        codePre.className = 'permission-code';
        codePre.textContent = JSON.stringify(input, null, 2);
        bubble.appendChild(codePre);
        
        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'permission-actions';
        
        const rejectBtn = document.createElement('button');
        rejectBtn.className = 'btn danger';
        rejectBtn.textContent = 'Reject';
        
        const approveBtn = document.createElement('button');
        approveBtn.className = 'btn success';
        approveBtn.textContent = 'Approve';
        
        actionsDiv.appendChild(rejectBtn);
        actionsDiv.appendChild(approveBtn);
        bubble.appendChild(actionsDiv);
        
        div.appendChild(bubble);
        chatLog.appendChild(div);
        chatLog.scrollTop = chatLog.scrollHeight;
        
        approveBtn.addEventListener('click', () => {
            ws.send(JSON.stringify({ type: 'permission_answer', answer: true }));
            const statusDiv = document.createElement('div');
            statusDiv.className = 'permission-status approved';
            statusDiv.textContent = '✅ Approved and Executed';
            bubble.replaceChild(statusDiv, actionsDiv);
        });
        
        rejectBtn.addEventListener('click', () => {
            ws.send(JSON.stringify({ type: 'permission_answer', answer: false }));
            const statusDiv = document.createElement('div');
            statusDiv.className = 'permission-status rejected';
            statusDiv.textContent = '❌ Action Blocked & Rejected';
            bubble.replaceChild(statusDiv, actionsDiv);
        });
    }

    function startStreamingMessage() {
        if (streamingBubble) return;

        streamingText = '';
        const div = document.createElement('div');
        div.className = 'message agent streaming-message';

        const bubble = document.createElement('div');
        bubble.className = 'bubble';
        div.appendChild(bubble);
        chatLog.appendChild(div);
        chatLog.scrollTop = chatLog.scrollHeight;

        streamingBubble = bubble;
        streamingMessage = div;
    }

    function appendStreamingDelta(text) {
        if (!text) return;
        if (!streamingBubble) startStreamingMessage();
        streamingText += text;
        streamingBubble.innerHTML = formatText(streamingText);
        chatLog.scrollTop = chatLog.scrollHeight;
    }

    function finishStreamingMessage() {
        if (!streamingBubble) return;
        if (!streamingText) {
            if (streamingMessage) streamingMessage.remove();
            streamingBubble = null;
            streamingMessage = null;
            return;
        }
        streamingBubble.innerHTML = formatText(streamingText);
        if (streamingMessage) streamingMessage.classList.remove('streaming-message');
        streamingBubble = null;
        streamingMessage = null;
        streamingText = '';
    }

    function appendMessage(role, text) {
        const div = document.createElement('div');
        div.className = `message ${role}`;
        
        const bubble = document.createElement('div');
        bubble.className = 'bubble';
        bubble.innerHTML = formatText(text);
        
        div.appendChild(bubble);
        chatLog.appendChild(div);
        chatLog.scrollTop = chatLog.scrollHeight;
    }

    function appendLog(text) {
        const wasThinking = !!thinkingBubble;
        hideThinking(); // Temporarily hide to ensure logs are appended above the spinner
        
        const div = document.createElement('div');
        div.style.color = 'var(--text-muted)';
        div.style.fontSize = '0.85rem';
        div.style.margin = '0 1.2rem 0.5rem';
        div.style.fontFamily = 'monospace';
        // strip ansi colors
        const cleanText = text.replace(/\x1b\[[0-9;]*m/g, '');
        div.textContent = cleanText;
        chatLog.appendChild(div);
        chatLog.scrollTop = chatLog.scrollHeight;
        
        if (wasThinking) {
            showThinking(); // Re-show spinner at the absolute bottom
        }
    }

    function formatText(text) {
        // simple markdown to html for display
        return escapeHtml(text).replace(/\n/g, '<br>')
                   .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                   .replace(/`(.*?)`/g, '<code>$1</code>');
    }

    // Academic Data
    document.getElementById('refresh-academic').addEventListener('click', loadAcademicData);
    const refreshReadingBtn = document.getElementById('refresh-reading');
    if (refreshReadingBtn) {
        refreshReadingBtn.addEventListener('click', loadReadingData);
    }
    
    let activeKeywords = [];

    async function loadKeywords() {
        const container = document.getElementById('keyword-tags-container');
        if (!container) return;
        try {
            const res = await fetch('/api/config');
            const data = await res.json();
            activeKeywords = data.keywords || [];
            renderKeywords();
        } catch (e) {
            console.error("Failed to load keywords", e);
            container.innerHTML = '<span style="color:var(--danger); font-size:0.85rem">Failed to load keywords.</span>';
        }
    }

    function renderKeywords() {
        const container = document.getElementById('keyword-tags-container');
        if (!container) return;
        container.innerHTML = '';
        if (activeKeywords.length === 0) {
            container.innerHTML = '<span style="color:var(--text-muted); font-size:0.85rem">No active keywords. Add some below!</span>';
            return;
        }
        activeKeywords.forEach((kw, index) => {
            const tagSpan = document.createElement('span');
            tagSpan.className = 'keyword-tag';
            tagSpan.innerHTML = `
                ${escapeHtml(kw)}
                <span class="remove" data-index="${index}">&times;</span>
            `;
            container.appendChild(tagSpan);
        });

        // Add event listeners for remove buttons
        container.querySelectorAll('.remove').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const idx = parseInt(e.target.dataset.index);
                activeKeywords.splice(idx, 1);
                renderKeywords();
            });
        });
    }

    // Add keyword button
    document.getElementById('add-keyword-btn').addEventListener('click', () => {
        const input = document.getElementById('new-keyword-input');
        const val = input.value.trim();
        if (val && !activeKeywords.includes(val)) {
            activeKeywords.push(val);
            input.value = '';
            renderKeywords();
        }
    });

    // Support pressing Enter in keyword input
    document.getElementById('new-keyword-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            document.getElementById('add-keyword-btn').click();
        }
    });

    // Save & Sync button
    document.getElementById('save-keywords-btn').addEventListener('click', async () => {
        const btn = document.getElementById('save-keywords-btn');
        const origText = btn.textContent;
        btn.textContent = '保存中...';
        btn.disabled = true;
        try {
            const res = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ keywords: activeKeywords })
            });
            const data = await res.json();
            if (data.status === 'success') {
                btn.textContent = '已保存 ✓';
                btn.style.borderColor = 'var(--secondary)';
                setTimeout(() => {
                    btn.textContent = origText;
                    btn.style.borderColor = '';
                    btn.disabled = false;
                }, 1500);
            } else {
                throw new Error("Failed to save");
            }
        } catch (e) {
            btn.textContent = '保存失败';
            btn.style.borderColor = 'var(--danger)';
            setTimeout(() => {
                btn.textContent = origText;
                btn.style.borderColor = '';
                btn.disabled = false;
            }, 1500);
        }
    });

    async function loadAcademicData() {
        loadKeywords();
        const grid = document.getElementById('academic-grid');
        grid.innerHTML = '<p>加载中...</p>';
        try {
            const res = await fetch('/api/favorites');
            const data = await res.json();
            grid.innerHTML = '';
            
            data.forEach(item => {
                const card = document.createElement('div');
                card.className = 'card';
                card.style.position = 'relative';
                const hasRepo = item.has_repo ? '✅ 已找到仓库' : '❌ 暂无仓库';
                const repoUrl = safeExternalUrl(item.repo);
                card.innerHTML = `
                    <button class="favorite-delete-btn" title="Delete favorite" aria-label="Delete favorite">×</button>
                    <h4>${escapeHtml(item.title)}</h4>
                    <p><strong>状态：</strong> ${hasRepo}</p>
                    <p><strong>收藏时间：</strong> ${escapeHtml(item.collected_at || '未知')}</p>
                    ${item.last_pushed_at ? `<p><strong>最近更新：</strong> ${escapeHtml(item.last_pushed_at)}</p>` : ''}
                    <div class="tag">${repoUrl ? `<a href="${repoUrl}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:none">查看仓库</a>` : '待补充'}</div>
                `;
                const deleteBtn = card.querySelector('.favorite-delete-btn');
                deleteBtn.addEventListener('click', async (event) => {
                    event.stopPropagation();
                    if (!confirm(`从学术动态面板删除《${item.title}》？`)) return;
                    try {
                        const delRes = await fetch('/api/favorites', {
                            method: 'DELETE',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({title: item.title, link: item.link})
                        });
                        if (!delRes.ok) {
                            const err = await delRes.json().catch(() => ({}));
                            throw new Error(err.message || '删除失败');
                        }
                        loadAcademicData();
                    } catch (err) {
                        alert(err.message || '删除失败。');
                    }
                });
                grid.appendChild(card);
            });
        } catch (e) {
            grid.innerHTML = '<p>加载失败。</p>';
        }
    }

    // Meals Data
    async function loadMealsData() {
        const timeline = document.getElementById('meals-timeline');
        const analysisContainer = document.getElementById('meal-analysis-content');
        const days = mealAnalysisDays ? mealAnalysisDays.value : '7';
        
        timeline.innerHTML = '加载中...';
        if (analysisContainer) analysisContainer.innerHTML = '加载中...';

        try {
            const [mealsRes, analysisRes] = await Promise.all([
                fetch('/api/meals'), fetch(`/api/meals/analysis?days=${encodeURIComponent(days)}`)
            ]);
            const meals = await mealsRes.json();
            const analysis = await analysisRes.json();

            timeline.innerHTML = '';
            renderMealAnalysis(analysis);
            loadFoodRuleCount();
            
            // Group meals by date
            const groupedMeals = {};
            meals.forEach(m => {
                if (!groupedMeals[m.date]) {
                    groupedMeals[m.date] = [];
                }
                groupedMeals[m.date].push(m);
            });
            
            // Sort dates descending (latest first)
            const sortedDates = Object.keys(groupedMeals).sort((a, b) => b.localeCompare(a));
            
            sortedDates.forEach(date => {
                const dayMeals = groupedMeals[date];
                const item = document.createElement('div');
                item.className = 'timeline-item';
                
                // Sort meals in standard order: Breakfast -> Lunch -> Dinner -> Night Snack -> Other
                const order = {'早餐': 1, '午餐': 2, '晚餐': 3, '夜宵': 4, '其他': 5};
                dayMeals.sort((a, b) => (order[a.type] || 99) - (order[b.type] || 99));
                
                let chipsHtml = '';
                dayMeals.forEach(m => {
                    const mealType = displayMealType(m.type);
                    const analysis = m.analysis || {};
                    const matchedNames = analysis.matched_names || [];
                    const tags = analysis.tags || [];
                    const unknownFoods = analysis.unknown_foods || [];
                    let icon = '🍽️';
                    if (mealType === '早餐') icon = '🥞';
                    else if (mealType === '午餐') icon = '🍜';
                    else if (mealType === '晚餐') icon = '🥩';
                    else if (mealType === '夜宵') icon = '🍢';
                    const detail = [
                        matchedNames.length ? `已识别：${matchedNames.join('、')}` : '未匹配到本地规则',
                        tags.length ? `标签：${tags.slice(0, 5).join('、')}` : '',
                        unknownFoods.length ? `未识别：${unknownFoods.join('、')}` : ''
                    ].filter(Boolean).join(' | ');
                    
                    chipsHtml += `
                        <span class="meal-chip ${safeClass(mealType)}" title="${escapeHtml(detail)}">
                            <span class="meal-icon">${icon}</span>
                            <span class="meal-type-label">${escapeHtml(mealType)}</span>
                            <span class="meal-name">${escapeHtml(m.content)}</span>
                            ${analysis.health_score !== null && analysis.health_score !== undefined ? `<span class="meal-score">${escapeHtml(analysis.health_score)}</span>` : ''}
                            <button class="meal-action-btn meal-edit-btn" data-date="${escapeHtml(m.date)}" data-type="${escapeHtml(mealType)}" data-content="${escapeHtml(m.content)}" title="编辑">✎</button>
                            <button class="meal-action-btn meal-delete-btn" data-date="${escapeHtml(m.date)}" data-type="${escapeHtml(mealType)}" data-content="${escapeHtml(m.content)}" title="删除">×</button>
                        </span>
                    `;
                });
                
                item.innerHTML = `
                    <div class="date">${escapeHtml(date)}</div>
                    <div class="meals-row">
                        ${chipsHtml}
                    </div>
                `;
                timeline.appendChild(item);
            });

            timeline.querySelectorAll('.meal-edit-btn').forEach(btn => {
                btn.addEventListener('click', (event) => {
                    event.stopPropagation();
                    editMealLog(btn.dataset.date, btn.dataset.type, btn.dataset.content);
                });
            });
            timeline.querySelectorAll('.meal-delete-btn').forEach(btn => {
                btn.addEventListener('click', (event) => {
                    event.stopPropagation();
                    deleteMealLog(btn.dataset.date, btn.dataset.type, btn.dataset.content);
                });
            });

        } catch (e) {
            console.error(e);
            timeline.innerHTML = '饮食记录加载失败。';
            if (analysisContainer) analysisContainer.innerHTML = '营养趋势加载失败。';
        }
    }

    function renderMealAnalysis(analysis) {
        const container = document.getElementById('meal-analysis-content');
        if (!container) return;
        const score = analysis.average_health_score;
        const counts = analysis.counts || {};
        const topTags = analysis.top_tags || [];
        const unknownFoods = analysis.unknown_foods || [];
        const suggestions = analysis.suggestions || [];
        container.innerHTML = `
            <div class="meal-stat-grid">
                <div class="meal-stat">
                    <span>${escapeHtml(analysis.total_meals || 0)}</span>
                    <label>餐次</label>
                </div>
                <div class="meal-stat">
                    <span>${score === null || score === undefined ? '-' : escapeHtml(score)}</span>
                    <label>平均分</label>
                </div>
                <div class="meal-stat">
                    <span>${escapeHtml(counts.vegetable || 0)}</span>
                    <label>蔬菜</label>
                </div>
                <div class="meal-stat">
                    <span>${escapeHtml(counts.protein_high || 0)}</span>
                    <label>蛋白质</label>
                </div>
                <div class="meal-stat warn">
                    <span>${escapeHtml(counts.salt_high || 0)}</span>
                    <label>高盐</label>
                </div>
                <div class="meal-stat warn">
                    <span>${escapeHtml(counts.sugar_high || 0)}</span>
                    <label>高糖</label>
                </div>
            </div>
            <div class="meal-tag-row">
                ${topTags.map(([tag, count]) => `<span>${escapeHtml(tag)} · ${escapeHtml(count)}</span>`).join('') || '<span>暂无标签</span>'}
            </div>
            ${unknownFoods.length ? `
                <div class="meal-unknown-panel">
                    <div class="meal-unknown-title">未识别食物</div>
                    <div class="meal-unknown-list">
                        ${unknownFoods.map(food => `
                            <button class="meal-unknown-btn" data-food="${escapeHtml(food)}">${escapeHtml(food)}</button>
                        `).join('')}
                    </div>
                </div>
            ` : ''}
            <div class="meal-suggestions">
                ${suggestions.map(text => `<p>${escapeHtml(text)}</p>`).join('')}
            </div>
        `;
        container.querySelectorAll('.meal-unknown-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                prefillFoodRule(btn.dataset.food || '');
            });
        });
    }

    async function addMealLog() {
        const statusDiv = document.getElementById('meal-entry-status');
        const content = mealContentInput ? mealContentInput.value.trim() : '';
        if (!content) {
            if (statusDiv) setStatus(statusDiv, '请输入这一餐吃了什么。', 'var(--danger)');
            return;
        }
        if (statusDiv) setStatus(statusDiv, '正在保存...', 'var(--secondary)');
        try {
            const res = await fetch('/api/meals', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    date: mealDateInput ? mealDateInput.value : '',
                    type: mealTypeInput ? mealTypeInput.value : '其他',
                    content
                })
            });
            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.message || '保存失败');
            }
            mealContentInput.value = '';
            if (statusDiv) setStatus(statusDiv, '已记录并完成分析。', 'var(--success)');
            loadMealsData();
        } catch (err) {
            if (statusDiv) setStatus(statusDiv, err.message || '保存失败。', 'var(--danger)');
        }
    }

    async function editMealLog(oldDate, oldType, oldContent) {
        const newDate = prompt('修改日期（YYYY-MM-DD）：', oldDate || '');
        if (newDate === null) return;
        const newType = prompt('修改餐别（早餐/午餐/晚餐/夜宵/其他）：', oldType || '其他');
        if (newType === null) return;
        const newContent = prompt('修改饮食内容：', oldContent || '');
        if (newContent === null) return;
        if (!newContent.trim()) {
            alert('饮食内容不能为空。');
            return;
        }
        try {
            const res = await fetch('/api/meals', {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    old_date: oldDate,
                    old_type: oldType,
                    date: newDate.trim(),
                    type: newType.trim(),
                    content: newContent.trim()
                })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.message || '更新失败');
            loadMealsData();
        } catch (err) {
            alert(err.message || '更新饮食记录失败。');
        }
    }

    async function deleteMealLog(date, type, content) {
        if (!confirm(`删除 ${date} ${type} 的记录「${content}」？`)) return;
        try {
            const res = await fetch('/api/meals', {
                method: 'DELETE',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({date, type})
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.message || '删除失败');
            loadMealsData();
        } catch (err) {
            alert(err.message || '删除饮食记录失败。');
        }
    }

    function prefillFoodRule(foodName) {
        if (!foodName) return;
        if (foodRuleNameInput) {
            foodRuleNameInput.value = foodName;
            foodRuleNameInput.focus();
        }
        if (foodRuleNotesInput && !foodRuleNotesInput.value.trim()) {
            foodRuleNotesInput.value = '来自饮食记录的未识别食物，请按常见中文饮食场景粗略标注。';
        }
        const statusDiv = document.getElementById('food-rule-status');
        if (statusDiv) setStatus(statusDiv, `已填入「${foodName}」，可补充说明后点击“分析并添加”。`, 'var(--secondary)');
    }

    async function loadFoodRuleCount() {
        const countEl = document.getElementById('food-rule-count');
        if (!countEl) return;
        try {
            const res = await fetch('/api/food-rules');
            const data = await res.json();
            countEl.textContent = `${data.total || 0} foods`;
        } catch (err) {
            countEl.textContent = '';
        }
    }

    async function addFoodRule() {
        const statusDiv = document.getElementById('food-rule-status');
        const name = foodRuleNameInput ? foodRuleNameInput.value.trim() : '';
        const notes = foodRuleNotesInput ? foodRuleNotesInput.value.trim() : '';
        if (!name) {
            if (statusDiv) setStatus(statusDiv, '请输入食物名称。', 'var(--danger)');
            return;
        }
        if (statusDiv) setStatus(statusDiv, '正在让大模型分析这个食物...', 'var(--secondary)');
        if (addFoodRuleBtn) addFoodRuleBtn.disabled = true;
        try {
            const res = await fetch('/api/food-rules', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name, notes})
            });
            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.message || 'Failed to add food rule');
            }
            const tags = (data.rule && data.rule.tags || []).join(', ');
            if (statusDiv) {
                setStatus(
                    statusDiv,
                    `已添加 ${data.name}：${data.rule.category}，评分 ${data.rule.health_score}，标签：${tags}`,
                    'var(--success)'
                );
            }
            if (foodRuleNameInput) foodRuleNameInput.value = '';
            if (foodRuleNotesInput) foodRuleNotesInput.value = '';
            loadFoodRuleCount();
            loadMealsData();
        } catch (err) {
            if (statusDiv) setStatus(statusDiv, err.message || 'Failed to add food rule.', 'var(--danger)');
        } finally {
            if (addFoodRuleBtn) addFoodRuleBtn.disabled = false;
        }
    }

    async function addPaperToReadingQueue(paper) {
        const res = await fetch('/api/reading-queue', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({paper_id: paper.id})
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.message || '加入本周待读失败。');
        }
    }

    async function savePaperComment(paperId, comment, markRead = false) {
        const res = await fetch('/api/papers/comment', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({paper_id: paperId, comment, mark_read: markRead})
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.message || '保存评论失败。');
        }
        return res.json();
    }

    async function savePaperTags(paperId, directionTags, autoGenerate = false) {
        const res = await fetch('/api/papers/tags', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({paper_id: paperId, direction_tags: directionTags, auto_generate: autoGenerate})
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.message || '保存方向标签失败。');
        }
        return res.json();
    }

    async function loadReadingData() {
        const list = document.getElementById('reading-list');
        const count = document.getElementById('weekly-read-count');
        if (!list) return;

        list.innerHTML = '<div class="empty-state">正在加载本周待读...</div>';
        try {
            const res = await fetch('/api/reading-queue');
            const data = await res.json();
            const papers = data.items || [];
            if (count) count.textContent = data.read_count_this_week || 0;
            list.innerHTML = '';

            if (!papers.length) {
                list.innerHTML = '<div class="empty-state">本周待读列表为空。可以从文献记忆库添加论文。</div>';
                return;
            }

            papers.forEach(paper => {
                const card = document.createElement('div');
                card.className = 'reading-card';
                card.innerHTML = `
                    <div class="reading-card-header">
                        <h3>${escapeHtml(paper.title)}</h3>
                        <button class="reading-remove-btn" title="从列表移除">×</button>
                    </div>
                    <p class="reading-meta">来源：${escapeHtml(paper.source || '-')}</p>
                    <textarea class="reading-comment" placeholder="写下读完这篇论文后的想法...">${escapeHtml(paper.comment || '')}</textarea>
                    <div class="reading-actions">
                        <button class="btn outline small reading-save-btn">保存评论</button>
                        <button class="btn success small reading-done-btn">标记已读</button>
                    </div>
                `;

                card.querySelector('.reading-remove-btn').addEventListener('click', async () => {
                    await fetch(`/api/reading-queue/${encodeURIComponent(paper.id)}`, {method: 'DELETE'});
                    loadReadingData();
                });

                card.querySelector('.reading-save-btn').addEventListener('click', async () => {
                    const textarea = card.querySelector('.reading-comment');
                    try {
                        await savePaperComment(paper.id, textarea.value, false);
                        textarea.classList.add('saved');
                        setTimeout(() => textarea.classList.remove('saved'), 900);
                    } catch (err) {
                        alert(err.message || '保存评论失败。');
                    }
                });

                card.querySelector('.reading-done-btn').addEventListener('click', async () => {
                    const textarea = card.querySelector('.reading-comment');
                    if (!textarea.value.trim()) {
                        alert('请先写一段简短评论，再标记为已读。');
                        return;
                    }
                    try {
                        await savePaperComment(paper.id, textarea.value, true);
                        loadReadingData();
                        loadPapersData();
                    } catch (err) {
                        alert(err.message || '标记已读失败。');
                    }
                });
                list.appendChild(card);
            });
        } catch (e) {
            list.innerHTML = '<div class="empty-state error">本周待读加载失败。</div>';
        }
    }

    // File Upload
    const fileInput = document.getElementById('file-input');
    const uploadZone = document.getElementById('upload-zone');
    const statusDiv = document.getElementById('upload-status');

    uploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadZone.style.borderColor = 'var(--primary)';
    });

    uploadZone.addEventListener('dragleave', () => {
        uploadZone.style.borderColor = 'var(--border)';
    });

    uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadZone.style.borderColor = 'var(--border)';
        if (e.dataTransfer.files.length > 0) {
            handleFileUpload(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) {
            handleFileUpload(fileInput.files[0]);
        }
    });

    async function handleFileUpload(file) {
        if (!file.name.endsWith('.pdf')) {
            setStatus(statusDiv, '请选择 PDF 文件。', 'var(--danger)');
            return;
        }

        const customTitle = prompt("为这篇论文起一个名字 (默认为文件名):", file.name.replace('.pdf', ''));
        if (customTitle === null) return; // 用户点击了取消

        const formData = new FormData();
        formData.append('file', file);
        formData.append('title', customTitle.trim() || file.name.replace('.pdf', ''));
        
        setStatus(statusDiv, '正在上传并处理...', 'var(--secondary)');
        
        try {
            const res = await fetch('/api/upload', {
                method: 'POST',
                body: formData
            });
            const data = await res.json();
            if (data.error) {
                setStatus(statusDiv, data.error, 'var(--danger)');
            } else {
                setStatus(statusDiv, data.result, 'var(--success)');
                loadPapersData(); // Instant synchronization!
            }
        } catch (e) {
            setStatus(statusDiv, '上传失败。', 'var(--danger)');
        }
    }

    async function loadPapersData(query = "", scope = null) {
        const grid = document.getElementById('papers-list');
        if (!grid) return;
        const searchScope = scope || (memorySearchScope ? memorySearchScope.value : 'all');
        
        grid.innerHTML = '<div style="color:var(--text-muted); font-size:0.9rem; padding: 1rem;">正在加载已收录文献...</div>';
        
        try {
            const url = query
                ? `/api/search?q=${encodeURIComponent(query)}&scope=${encodeURIComponent(searchScope)}`
                : '/api/papers';
            const res = await fetch(url);
            const papers = await res.json();
            
            grid.innerHTML = '';
            if (papers.length === 0) {
                grid.innerHTML = '<div style="color:var(--text-muted); font-size:0.9rem; padding: 1rem;">未找到相关文献。</div>';
                return;
            }
            
            papers.forEach(paper => {
                const card = document.createElement('div');
                card.className = 'paper-card';
                card.style.position = 'relative';
                
                const title = String(paper.title || '');
                const source = String(paper.source || '');
                const directionTags = paper.direction_tags || [];
                const displayTitle = title.length > 55 ? title.slice(0, 52) + '...' : title;
                const displaySource = source.length > 40 ? source.slice(0, 37) + '...' : source;
                
                // 架构图缩略图
                const thumbHtml = paper.arch_image
                    ? `<div class="paper-card-thumb"><img src="/arch_images/${safeImagePath(paper.arch_image)}" alt="架构图" /></div>`
                    : `<div class="paper-card-thumb paper-card-thumb--empty"><span>📊</span></div>`;
                
                card.innerHTML = `
                    <div class="delete-btn" title="删除文献" style="position: absolute; top: 10px; right: 10px; color: var(--danger); font-size: 1.2rem; cursor: pointer; opacity: 0.7; transition: opacity 0.2s;">🗑️</div>
                    ${thumbHtml}
                    <div class="paper-card-content">
                        <div class="paper-card-title" title="${escapeHtml(title)}">${escapeHtml(displayTitle)}</div>
                        <div class="paper-card-meta">来源：${escapeHtml(displaySource)}</div>
                        ${directionTags.length ? `<div class="paper-direction-tags">${directionTags.map(tag => `<span>${escapeHtml(tag)}</span>`).join('')}</div>` : ''}
                        ${paper.comment ? `<div class="paper-card-comment">评论：${escapeHtml(paper.comment.slice(0, 80))}${paper.comment.length > 80 ? '...' : ''}</div>` : ''}
                    </div>
                    <div class="paper-card-actions">
                        <button class="paper-card-btn">查看摘要</button>
                        <button class="paper-card-btn secondary add-reading-btn ${paper.in_reading_queue ? 'is-added' : ''}">${paper.in_reading_queue ? '已添加' : '加入本周待读'}</button>
                    </div>
                `;
                
                const delBtn = card.querySelector('.delete-btn');
                delBtn.addEventListener('click', async (e) => {
                    e.stopPropagation(); // prevent opening summary
                    if (confirm(`确定要从文献库中彻底删除《${paper.title}》吗？`)) {
                        try {
                            const delRes = await fetch(`/api/papers/${encodeURIComponent(paper.id)}`, { method: 'DELETE' });
                            if (delRes.ok) {
                                loadPapersData(query, searchScope); // reload current view
                            } else {
                                alert("删除文献失败。");
                            }
                        } catch (err) {
                            console.error("Delete failed", err);
                            alert("删除文献失败。");
                        }
                    }
                });
                
                card.addEventListener('click', () => {
                    showPaperDetail(paper);
                });

                const addReadingBtn = card.querySelector('.add-reading-btn');
                addReadingBtn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    if (paper.in_reading_queue) return;
                    try {
                        await addPaperToReadingQueue(paper);
                        paper.in_reading_queue = true;
                        addReadingBtn.classList.add('is-added');
                        addReadingBtn.textContent = '已添加';
                    } catch (err) {
                        alert(err.message || '加入本周待读失败。');
                    }
                });
                
                grid.appendChild(card);
            });
        } catch (e) {
            grid.innerHTML = '<div style="color:var(--danger); font-size:0.9rem; padding: 1rem;">已收录文献加载失败。</div>';
        }
    }

    function showPaperDetail(paper) {
        const overlay = document.createElement('div');
        overlay.className = 'paper-detail-overlay';
        
        let formattedSummary = formatText(paper.summary);
        
        // Remove numbered list prefixes (1. 2. 3. etc.)
        formattedSummary = formattedSummary
            .replace(/(\<br\>)\s*\d+\.\s*/g, '$1')
            .replace(/^\s*\d+\.\s*/, '')
            .replace(/【核心创新点】：/g, '<h4>💡 核心创新点</h4><p>')
            .replace(/【模型架构设计】：/g, '</p><h4>🏗️ 模型架构设计</h4><p>')
            .replace(/【核心创新点】/g, '<h4>💡 核心创新点</h4>')
            .replace(/【模型架构设计】/g, '<h4>🏗️ 模型架构设计</h4>');
            
        if (formattedSummary.includes('<p>') && !formattedSummary.endsWith('</p>')) {
            formattedSummary += '</p>';
        }
        
        // 架构图区域
        const archImageHtml = paper.arch_image ? `
            <div class="arch-image-section">
                <h4>🏗️ 主体网络架构图</h4>
                <div class="arch-image-wrapper">
                    <img 
                        src="/arch_images/${safeImagePath(paper.arch_image)}" 
                        alt="架构图"
                        class="arch-image"
                        onclick="this.classList.toggle('arch-image--zoomed')"
                        title="点击图片放大/缩小"
                    />
                    <p class="arch-image-hint">🔍 点击图片可放大/缩小</p>
                </div>
            </div>
            <hr class="arch-divider" />
        ` : '';
        const directionTagsText = (paper.direction_tags || []).join('，');
        
        overlay.innerHTML = `
            <div class="paper-detail-content">
                <div class="paper-detail-header">
                    <h3 title="${escapeHtml(paper.title)}">${escapeHtml(paper.title)}</h3>
                    <button class="paper-detail-close">&times;</button>
                </div>
                <div class="paper-detail-body">
                    ${archImageHtml}
                    ${formattedSummary}
                    <hr class="arch-divider" />
                    <div class="paper-tags-section">
                        <h4>🏷️ 方向标签</h4>
                        <div class="paper-tags-editor">
                            <input id="paper-direction-tags-input" class="paper-tags-input" type="text" placeholder="例如：多模态，AIGC检测，RAG" value="${escapeHtml(directionTagsText)}">
                            <button id="save-paper-tags" class="btn outline small">保存标签</button>
                            <button id="auto-paper-tags" class="btn outline small">自动生成</button>
                        </div>
                    </div>
                    <div class="paper-comment-section">
                        <h4>📝 阅读评论</h4>
                        <textarea id="paper-comment-input" class="paper-comment-input" placeholder="写下你读完这篇论文后的感想...">${escapeHtml(paper.comment || '')}</textarea>
                        <div class="paper-comment-actions">
                            <button id="save-paper-comment" class="btn outline small">保存评论</button>
                            <button id="add-paper-reading" class="btn primary small">加入本周待读</button>
                        </div>
                        ${paper.read_at ? `<p class="paper-read-at">阅读时间：${escapeHtml(paper.read_at)}</p>` : ''}
                    </div>
                </div>
            </div>
        `;
        
        document.body.appendChild(overlay);
        
        const closeBtn = overlay.querySelector('.paper-detail-close');
        closeBtn.addEventListener('click', () => {
            overlay.remove();
        });

        const commentInput = overlay.querySelector('#paper-comment-input');
        const tagsInput = overlay.querySelector('#paper-direction-tags-input');
        overlay.querySelector('#save-paper-tags').addEventListener('click', async () => {
            try {
                const tags = tagsInput.value.split(/[,，、;；\s]+/).map(tag => tag.trim()).filter(Boolean);
                const data = await savePaperTags(paper.id, tags);
                paper.direction_tags = data.paper.direction_tags || tags;
                loadPapersData(
                    memorySearchInput ? memorySearchInput.value : '',
                    memorySearchScope ? memorySearchScope.value : 'all'
                );
            } catch (err) {
                alert(err.message || '保存方向标签失败。');
            }
        });

        overlay.querySelector('#auto-paper-tags').addEventListener('click', async () => {
            const btn = overlay.querySelector('#auto-paper-tags');
            const originalText = btn.textContent;
            btn.disabled = true;
            btn.textContent = '生成中...';
            try {
                const data = await savePaperTags(paper.id, [], true);
                const tags = data.paper.direction_tags || [];
                paper.direction_tags = tags;
                tagsInput.value = tags.join('，');
                loadPapersData(
                    memorySearchInput ? memorySearchInput.value : '',
                    memorySearchScope ? memorySearchScope.value : 'all'
                );
            } catch (err) {
                alert(err.message || '自动生成方向标签失败。');
            } finally {
                btn.disabled = false;
                btn.textContent = originalText;
            }
        });

        overlay.querySelector('#save-paper-comment').addEventListener('click', async () => {
            try {
                await savePaperComment(paper.id, commentInput.value, false);
                paper.comment = commentInput.value;
                loadPapersData(
                    memorySearchInput ? memorySearchInput.value : '',
                    memorySearchScope ? memorySearchScope.value : 'all'
                );
            } catch (err) {
                alert(err.message || '保存评论失败。');
            }
        });

        overlay.querySelector('#add-paper-reading').addEventListener('click', async () => {
            try {
                await addPaperToReadingQueue(paper);
                overlay.querySelector('#add-paper-reading').textContent = '已添加';
            } catch (err) {
                alert(err.message || '加入本周待读失败。');
            }
        });
        
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                overlay.remove();
            }
        });
    }
});
