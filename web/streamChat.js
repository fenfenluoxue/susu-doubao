import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "susu-doubao.LLMChat",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "LLMChat") {
            console.log("[susu-doubao] 注册 LLMChat 节点自定义逻辑");

            const onNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function () {
                console.log("[susu-doubao] LLMChat 节点 onNodeCreated 触发");
                if (onNodeCreated) onNodeCreated.apply(this, arguments);

                try {
                const node = this;

                // 渲染节流：用requestAnimationFrame确保每一帧最多渲染一次
                let _renderPending = false;
                let _renderScheduled = false;

                // 渲染消息
                function renderMessages() {
                    if (_renderScheduled) {
                        _renderPending = true;
                        return;
                    }
                    _renderScheduled = true;
                    requestAnimationFrame(() => {
                        _renderScheduled = false;
                        doRenderMessages();
                        if (_renderPending) {
                            _renderPending = false;
                            requestAnimationFrame(() => doRenderMessages());
                        }
                    });
                }

                // 实际渲染函数（带try/catch保护，防止渲染出错导致节点卡住）
                function doRenderMessages() {
                    try {
                        if (!node.chatMessages || !Array.isArray(node.chatMessages)) node.chatMessages = [];
                        if (!messagesBox) return;
                        if (node.chatMessages.length === 0) {
                            messagesBox.innerHTML = '<div style="color:#555;font-style:italic;text-align:center;font-size:11px;">输入消息后按 Enter 发送，Shift+Enter 换行</div>';
                            return;
                        }
                        messagesBox.innerHTML = "";
                        node.chatMessages.forEach(msg => {
                            if (!msg || typeof msg !== 'object') return;
                            const msgDiv = document.createElement("div");
                            msgDiv.style.cssText = `margin-bottom:14px;display:flex;${msg.role === 'user' ? 'justify-content:flex-end;flex-wrap:wrap;' : 'justify-content:flex-start;flex-direction:column;'}`;

                            // AI消息：先显示思考内容（如果有）
                            if (msg.role === 'assistant' && msg.reasoning && msg.reasoning.trim()) {
                                const thinkBubble = document.createElement("div");
                                thinkBubble.style.cssText = `max-width:85%;padding:4px 8px;border-radius:6px;font-size:10px;line-height:1.4;word-break:break-word;white-space:pre-wrap;background:#0a0a15;color:#666;font-style:italic;border:1px solid #222;margin-bottom:4px;`;
                                thinkBubble.textContent = "💭 " + msg.reasoning;
                                msgDiv.appendChild(thinkBubble);
                            }

                            // 用户消息：显示图片缩略图（如果有）
                            if (msg.role === 'user' && msg.image) {
                                const imgWrap = document.createElement("div");
                                imgWrap.style.cssText = "flex-basis:100%;display:flex;justify-content:flex-end;margin-bottom:4px;";
                                const img = document.createElement("img");
                                img.src = msg.image.indexOf("data:") === 0 ? msg.image : "data:image/png;base64," + msg.image;
                                img.style.cssText = "max-width:120px;max-height:120px;border-radius:6px;cursor:pointer;border:1px solid #444;object-fit:cover;";
                                img.title = "点击查看大图";
                                img.onclick = function() {
                                    const overlay = document.createElement("div");
                                    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.85);z-index:99999;display:flex;align-items:center;justify-content:center;cursor:pointer;";
                                    const bigImg = document.createElement("img");
                                    bigImg.src = img.src;
                                    bigImg.style.cssText = "max-width:90%;max-height:90%;border-radius:8px;";
                                    overlay.appendChild(bigImg);
                                    overlay.onclick = function() { overlay.remove(); };
                                    document.body.appendChild(overlay);
                                };
                                imgWrap.appendChild(img);
                                msgDiv.appendChild(imgWrap);
                            }

                            const bubble = document.createElement("div");
                            bubble.style.cssText = `max-width:85%;padding:6px 10px;border-radius:8px;font-size:11px;line-height:1.5;word-break:break-word;white-space:pre-wrap;${
                                msg.role === 'user'
                                    ? 'background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;'
                                    : 'background:#1a1a2e;color:#a5d6a7;border:1px solid #333;'
                            }`;
                            bubble.textContent = msg.content || (msg.role === 'assistant' ? "..." : "");
                            msgDiv.appendChild(bubble);

                            // 显示时间戳
                            if (msg.timestamp) {
                                const timeDiv = document.createElement("div");
                                timeDiv.style.cssText = `font-size:10px;color:#666;margin-top:2px;${msg.role === 'user' ? 'flex-basis:100%;text-align:right;' : 'text-align:left;'}`;
                                timeDiv.textContent = msg.timestamp;
                                msgDiv.appendChild(timeDiv);
                            }

                            messagesBox.appendChild(msgDiv);
                        });
                        messagesBox.scrollTop = messagesBox.scrollHeight;
                    } catch(e) {
                        console.error("[susu-doubao] 渲染消息失败:", e);
                        try { if (messagesBox) messagesBox.innerHTML = '<div style="color:#ff6666;text-align:center;font-size:11px;">渲染出错，已重置</div>'; } catch(e2) {}
                    }
                }

                node.chatMessages = []; // {role: 'user'|'assistant', content: string}
                node._imageB64 = ""; // 当前加载的图像base64（用于视觉模型分析）

                // 找到自动创建的widget
                const apiUrlW = node.widgets.find(w => w.name === "API地址");
                const modelW = node.widgets.find(w => w.name === "模型名称");
                const apiKeyW = node.widgets.find(w => w.name === "API密钥");
                const sysW = node.widgets.find(w => w.name === "系统提示词");
                const userInputW = node.widgets.find(w => w.name === "用户输入");
                const tempW = node.widgets.find(w => w.name === "温度");
                const topPW = node.widgets.find(w => w.name === "top_p");
                const topKW = node.widgets.find(w => w.name === "top_k");
                const maxLenW = node.widgets.find(w => w.name === "最大长度");
                const cloudDefaultW = node.widgets.find(w => w.name === "使用云端默认参数");

                // 隐藏旧的聊天结果、状态信息、按钮widget，以及流式输出开关、会话ID、系统提示词、用户输入（完全移除，不渲染，避免残留间隙导致间距不一致）
                const oldWidgets = ["聊天结果", "状态信息", "🟢 生成回答", "🔴 清空对话", "流式输出", "会话ID", "系统提示词", "用户输入"];
                const removedWidgets = [];
                oldWidgets.forEach(name => {
                    const w = node.widgets.find(w => w.name === name);
                    if (w) {
                        removedWidgets.push(w);
                        const idx = node.widgets.indexOf(w);
                        if (idx > -1) node.widgets.splice(idx, 1);
                    }
                });
                // 彻底删除旧widget：只删除widget的直接外层容器（element.parentNode），不向上遍历，不删除其他元素
                // 延迟150ms确保ComfyUI已创建完整的widget DOM（包括外层容器）
                setTimeout(() => {
                    removedWidgets.forEach(w => {
                        try {
                            // 找到widget的主元素
                            const el = w.element || w.inputEl || w.labelEl;
                            if (!el) {
                                console.log("[susu-doubao] widget无DOM元素:", w.name);
                                return;
                            }

                            // 只处理直接父元素（widget的外层容器），绝对不向上遍历parentNode.parentNode
                            const container = el.parentNode;
                            if (container && container.parentNode) {
                                // 删除前清除title（防止删除过程中tooltip残留）
                                container.removeAttribute('title');
                                try {
                                    container.querySelectorAll('[title]').forEach(child => {
                                        child.removeAttribute('title');
                                    });
                                } catch(e) {}
                                // 彻底删除这个widget的外层容器（只包含这一个widget，安全）
                                container.parentNode.removeChild(container);
                                console.log("[susu-doubao] 已彻底删除widget外层容器:", w.name);
                            }

                            // 兜底：如果容器删除失败，隐藏内部元素
                            if (w.inputEl && w.inputEl.parentNode) w.inputEl.style.display = "none";
                            if (w.labelEl && w.labelEl.parentNode) w.labelEl.style.display = "none";
                            if (w.element && w.element.parentNode) w.element.style.display = "none";
                        } catch(e) {
                            console.log("[susu-doubao] 删除widget失败:", w.name, e);
                        }
                    });
                    console.log("[susu-doubao] 旧widget删除完成，共", removedWidgets.length, "个");
                }, 150);

                // API密钥改成密码框
                setTimeout(() => {
                    if (apiKeyW && apiKeyW.inputEl) {
                        apiKeyW.inputEl.type = "password";
                        apiKeyW.inputEl.style.fontSize = "11px";
                    }
                }, 100);

                // ===== 自定义DOM聊天界面 =====
                const chatEl = document.createElement("div");
                chatEl.className = "susu-chat-root";
                chatEl.style.cssText = "width:100%;padding:4px 0;position:relative;";

                chatEl.innerHTML = `
                    <style>
                        /* 根容器不拦截点击事件，让点击穿透到下方的原生widget（API地址等） */
                        .susu-chat-root { pointer-events: none; }
                        /* 只让真正需要交互的元素响应点击，容器div保持穿透 */
                        .susu-chat-root button,
                        .susu-chat-root textarea,
                        .susu-chat-root input,
                        .susu-chat-root .susu-session-list,
                        .susu-chat-root .susu-chat-messages,
                        .susu-chat-root .susu-resize-handle,
                        .susu-chat-root .susu-sys-resize-handle,
                        .susu-chat-root .susu-input-resize-handle,
                        .susu-chat-root .susu-image-preview,
                        .susu-chat-root .susu-image-preview-img {
                            pointer-events: auto;
                        }
                        .susu-sys-resize-handle > div,
                        .susu-resize-handle > div,
                        .susu-input-resize-handle > div {
                            transition: background 0.2s;
                        }
                        .susu-sys-resize-handle:hover > div,
                        .susu-resize-handle:hover > div,
                        .susu-input-resize-handle:hover > div {
                            background: #888 !important;
                        }
                    </style>
                    <!-- 会话管理区域（豆包风格） -->
                    <div class="susu-session-manager" style="margin-bottom:8px;position:relative;z-index:200;">
                        <div style="display:flex;gap:4px;align-items:center;">
                            <button class="susu-session-title-btn" style="flex:1;background:#2a2a3e;color:#ddd;border:1px solid #444;border-radius:3px;padding:5px 8px;font-size:11px;cursor:pointer;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">💬 新对话</button>
                            <button class="susu-new-session-btn" style="background:#667eea;color:#fff;border:none;border-radius:3px;padding:5px 10px;font-size:11px;cursor:pointer;white-space:nowrap;">+ 新对话</button>
                        </div>
                        <div class="susu-session-list" style="display:none;position:absolute;top:100%;left:0;right:0;z-index:9999;background:#1a1a2e;border:1px solid #444;border-radius:4px;margin-top:2px;max-height:200px;overflow-y:auto;z-index:1000;"></div>
                    </div>
                    <!-- 系统提示词（可折叠） -->
                    <div class="susu-sys-section" style="margin-bottom:14px;position:relative;z-index:100;">
                        <button class="susu-sys-toggle" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none';this.textContent=this.nextElementSibling.style.display==='none'?'⚙️ 系统提示词（点击展开）':'⚙️ 系统提示词（点击收起）';window.dispatchEvent(new CustomEvent('susuSysToggle',{detail:this}));" style="width:100%;background:#3d3d5c;color:#ddd;border:1px solid #667eea;border-radius:3px;padding:6px;font-size:11px;cursor:pointer;margin-bottom:4px;text-align:left;position:relative;z-index:99999;pointer-events:auto;display:block;">⚙️ 系统提示词（点击展开）</button>
                        <div class="susu-sys-content" style="display:none;position:relative;z-index:100;">
                            <textarea class="susu-sys-input" placeholder="设定模型的角色、行为规则和回答风格..." style="width:100%;background:#0d0d1a;border:1px solid #333;border-radius:4px;padding:6px;color:#ccc;font-size:11px;min-height:50px;height:50px;resize:none;box-sizing:border-box;position:relative;z-index:100;"></textarea>
                            <div class="susu-sys-resize-handle" style="position:absolute;left:0;right:0;bottom:-8px;height:14px;cursor:row-resize;z-index:9999;display:flex;align-items:center;justify-content:center;background:transparent;">
                                <div style="width:40px;height:3px;background:#555;border-radius:2px;pointer-events:none;"></div>
                            </div>
                        </div>
                    </div>

                    <!-- 图像预览区域（视觉模型分析图片时显示） -->
                    <div class="susu-image-preview" style="display:none;align-items:flex-start;gap:6px;margin-bottom:6px;padding:6px;background:#1a1a2e;border:1px solid #667eea;border-radius:4px;">
                        <img class="susu-image-preview-img" style="max-width:80px;max-height:80px;border-radius:3px;object-fit:cover;cursor:pointer;" title="点击查看大图" />
                        <div style="flex:1;min-width:0;">
                            <div class="susu-image-status" style="font-size:10px;color:#667eea;margin-bottom:2px;">🖼 已加载图像</div>
                            <button class="susu-clear-image-btn" style="background:#555;color:#fff;border:none;border-radius:2px;padding:1px 6px;font-size:9px;cursor:pointer;">✕ 移除图像</button>
                        </div>
                    </div>

                    <!-- 聊天消息显示区（相对定位，用于绝对定位拖拽手柄） -->
                    <div class="susu-chat-messages-wrapper" style="position:relative;margin-bottom:4px;">
                        <div class="susu-chat-messages" style="background:#0d0d1a;border:1px solid #333;border-radius:4px;padding:8px;min-height:80px;height:180px;overflow-y:auto;box-sizing:border-box;">
                            <div style="color:#555;font-style:italic;text-align:center;font-size:11px;">输入消息后按 Enter 发送，Shift+Enter 换行</div>
                        </div>
                        <!-- 绝对定位的拖拽手柄 -->
                        <div class="susu-resize-handle" style="position:absolute;left:0;right:0;bottom:-11px;height:14px;cursor:row-resize;z-index:9999;display:flex;align-items:center;justify-content:center;background:transparent;">
                            <div style="width:40px;height:3px;background:#555;border-radius:2px;pointer-events:none;"></div>
                        </div>
                    </div>

                    <!-- 状态显示 -->
                    <div class="susu-status-bar" style="font-size:10px;color:#88ff88;margin-bottom:4px;text-align:right;">就绪</div>

                    <!-- 输入框 -->
                    <div class="susu-input-area" style="position:relative;margin-bottom:0;">
                        <textarea class="susu-user-input" placeholder="输入消息，按 Enter 发送..." style="width:100%;background:#0d0d1a;border:1px solid #333;border-radius:4px;padding:6px;color:#e0e0e0;font-size:12px;min-height:30px;height:40px;resize:none;box-sizing:border-box;"></textarea>
                    </div>

                    <!-- 底部一行：清空按钮 + 拖拽条（紧挨输入框） + 发送按钮，上侧对齐 -->
                    <div style="display:flex;align-items:flex-start;gap:4px;margin-top:-6px;position:relative;z-index:10;">
                        <button class="susu-clear-btn" style="flex:0 0 60px;background:#555;color:#fff;border:none;border-radius:3px;padding:2px 6px;font-size:9px;cursor:pointer;white-space:nowrap;text-align:center;transition:background 0.2s;margin-top:8px;">🗑 清空</button>
                        <div class="susu-input-resize-handle" style="flex:1;height:14px;cursor:row-resize;display:flex;align-items:center;justify-content:center;background:transparent;">
                            <div style="width:40px;height:3px;background:#555;border-radius:2px;pointer-events:none;transition:background 0.2s;"></div>
                        </div>
                        <button class="susu-send-btn" style="flex:0 0 60px;background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;border:none;border-radius:3px;padding:2px 10px;font-size:9px;font-weight:bold;cursor:pointer;white-space:nowrap;text-align:center;transition:opacity 0.2s;margin-top:8px;">✉ 发送</button>
                    </div>
                `;

                const chatWidget = node.addDOMWidget("susu_chat", "div", chatEl, () => {}, {});

                // 动态高度：computeSize只返回基础最小高度（不含用户拉伸的额外高度）
                // 用户拉伸的额外高度由onResize实时计算并让聊天区填满，参考doubao_clipboard.js
                chatWidget.computeSize = function(width) {
                    const sysH = _sysExpanded ? (_sysHeight + 50) : 45;
                    // 常数95包含：会话管理区(~36px) + 状态栏(~18px) + 底部按钮行(~22px) + 内边距/间距(~19px)
                    // _imagePreviewHeight: 图片预览区域高度（显示图片时约100px，不显示时0）
                    const total = _chatHeight + _inputHeight + sysH + _imagePreviewHeight + 95;
                    return [width, Math.max(220, total)];
                };
                // 移除内部滚动限制，让内容自然撑开
                chatEl.style.overflowY = "";
                chatEl.style.maxHeight = "";

                // 程序式调整节点高度（拖拽手柄、展开收起时调用）
                // 参考doubao_clipboard.js的setNodeHeight：保留用户拉伸的额外高度
                function updateComputeSize() {
                    try {
                        const newMin = node.computeSize()[1];
                        const extra = Math.max(0, node.size[1] - newMin);
                        const finalH = newMin + extra;
                        _programmaticResize = true;
                        try {
                            node.setSize([node.size[0], finalH]);
                        } finally {
                            _programmaticResize = false;
                        }
                    } catch(e) {}
                    if (node.graph) node.graph.setDirtyCanvas(true, false);
                    _saveState();
                }

                // 获取DOM元素引用
                const sysToggle = chatEl.querySelector(".susu-sys-toggle");
                const sysContent = chatEl.querySelector(".susu-sys-content");
                const sysInput = chatEl.querySelector(".susu-sys-input");
                const sysResizeHandle = chatEl.querySelector(".susu-sys-resize-handle");
                const messagesBox = chatEl.querySelector(".susu-chat-messages");
                const resizeHandle = chatEl.querySelector(".susu-resize-handle");
                const statusBar = chatEl.querySelector(".susu-status-bar");
                const userInput = chatEl.querySelector(".susu-user-input");
                const inputResizeHandle = chatEl.querySelector(".susu-input-resize-handle");
                const sendBtn = chatEl.querySelector(".susu-send-btn");
                const clearBtn = chatEl.querySelector(".susu-clear-btn");
                // 图像预览相关元素（视觉模型分析图片）
                const imagePreviewContainer = chatEl.querySelector(".susu-image-preview");
                const imagePreviewImg = chatEl.querySelector(".susu-image-preview-img");
                const imageStatus = chatEl.querySelector(".susu-image-status");
                const clearImageBtn = chatEl.querySelector(".susu-clear-image-btn");
                // 会话管理相关元素
                const sessionTitleBtn = chatEl.querySelector(".susu-session-title-btn");
                const newSessionBtn = chatEl.querySelector(".susu-new-session-btn");
                const sessionList = chatEl.querySelector(".susu-session-list");

                // 遍历所有祖先设overflow:visible（参考doubao-susu的做法）
                let _ancestor = chatEl;
                while (_ancestor && _ancestor !== document.body) {
                    _ancestor.style.overflow = "visible";
                    _ancestor = _ancestor.parentElement;
                }

                // 通用拖拽处理函数（参考doubao-susu的做法）
                let _isDragging = false;
                let _programmaticResize = false; // 程序式setSize时不保存到localStorage
                let _nodeReady = false; // 节点初始化完成标志
                // 高度跟踪变量（不依赖offsetHeight，确保准确）
                let _sysExpanded = false;
                let _sysHeight = 80;  // 系统提示词框展开时的高度
                let _inputHeight = 40; // 输入框高度
                let _chatHeight = 180; // 聊天区高度
                let _imagePreviewHeight = 0; // 图片预览区域高度（显示图片时约100px）

                // localStorage key
                const _storageKey = "susu_chat_state_" + node.id;
                const _sessionsKey = "susu_chat_sessions_" + node.id;
                const _currentSessionKey = "susu_current_session_" + node.id;

                // ===== 会话管理（豆包风格）=====
                let _sessions = {}; // {sessionId: {id, title, createdAt, messages}}
                let _currentSessionId = null;
                let _sessionCounter = 0; // 用于自动命名"新对话 N"

                // 生成唯一会话ID
                function _genSessionId() {
                    return "sess_" + Date.now() + "_" + Math.random().toString(36).substr(2, 6);
                }

                // 获取会话标题（第一条用户消息摘要，或自动命名）
                function _getSessionTitle(session) {
                    if (!session) return "新对话";
                    if (session.title && session.title !== "") return session.title;
                    // 找第一条用户消息（加判空保护，防止元信息对象没有messages属性）
                    if (session.messages && Array.isArray(session.messages)) {
                        const firstUserMsg = session.messages.find(m => m.role === "user");
                        if (firstUserMsg && firstUserMsg.content) {
                            const text = firstUserMsg.content.trim().replace(/\n/g, " ");
                            return text.length > 15 ? text.substr(0, 15) + "..." : text;
                        }
                    }
                    return "新对话 " + (session._index || 1);
                }

                // ===== 后端会话API调用函数 =====
                async function _apiListSessions() {
                    try {
                        const resp = await fetch("/susu-doubao/sessions/list");
                        const data = await resp.json();
                        if (data.success) return data.sessions || [];
                    } catch(e) { console.warn("[susu] API列表失败:", e); }
                    return [];
                }
                async function _apiGetSession(id) {
                    try {
                        const resp = await fetch("/susu-doubao/sessions/get?id=" + encodeURIComponent(id));
                        const data = await resp.json();
                        if (data.success) return data.session;
                    } catch(e) { console.warn("[susu] API获取失败:", e); }
                    return null;
                }
                async function _apiSaveSession(id, title, messages) {
                    try {
                        const resp = await fetch("/susu-doubao/sessions/save", {
                            method: "POST", headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ id: id, title: title, messages: messages })
                        });
                        const data = await resp.json();
                        return data.success;
                    } catch(e) { console.warn("[susu] API保存失败:", e); }
                    return false;
                }
                async function _apiDeleteSession(id) {
                    try {
                        const resp = await fetch("/susu-doubao/sessions/delete", {
                            method: "POST", headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ id: id })
                        });
                        const data = await resp.json();
                        return data.success;
                    } catch(e) { console.warn("[susu] API删除失败:", e); }
                    return false;
                }
                async function _apiRenameSession(id, title) {
                    try {
                        const resp = await fetch("/susu-doubao/sessions/rename", {
                            method: "POST", headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ id: id, title: title })
                        });
                        const data = await resp.json();
                        return data.success;
                    } catch(e) { console.warn("[susu] API重命名失败:", e); }
                    return false;
                }

                // 保存当前会话（防抖）
                let _saveTimer = null;
                function _saveCurrentSession() {
                    if (!_currentSessionId) return;
                    const sess = _sessions[_currentSessionId] || {};
                    const title = _getSessionTitle(sess);
                    const messages = Array.isArray(node.chatMessages) ? [...node.chatMessages] : [];
                    if (_sessions[_currentSessionId]) {
                        _sessions[_currentSessionId].title = title;
                        _sessions[_currentSessionId].message_count = messages.length;
                        _sessions[_currentSessionId].updated_at = Date.now() / 1000;
                    }
                    if (_saveTimer) clearTimeout(_saveTimer);
                    _saveTimer = setTimeout(function() {
                        _apiSaveSession(_currentSessionId, title, messages);
                    }, 500);
                }

                // 加载会话数据（从后端API，异步）
                async function _loadSessions() {
                    try {
                        // 从localStorage读取上次的会话ID（只存ID）
                        let lastSessionId = null;
                        try { lastSessionId = localStorage.getItem(_currentSessionKey); } catch(e) {}

                        // 从后端API获取会话列表（元信息，不含messages）
                        const sessionList = await _apiListSessions();
                        _sessions = {};
                        for (let i = 0; i < sessionList.length; i++) {
                            _sessions[sessionList[i].id] = sessionList[i];
                        }
                        _sessionCounter = sessionList.length;

                        // 确定当前会话
                        if (lastSessionId && _sessions[lastSessionId]) {
                            _currentSessionId = lastSessionId;
                            const full = await _apiGetSession(lastSessionId);
                            if (full && Array.isArray(full.messages)) {
                                node.chatMessages = full.messages;
                            } else {
                                // 后端消息为空时保留本地恢复的消息，防止刷新后聊天内容消失
                                if (!node.chatMessages || node.chatMessages.length === 0) node.chatMessages = [];
                            }
                        } else if (sessionList.length > 0) {
                            _currentSessionId = sessionList[0].id;
                            const full = await _apiGetSession(_currentSessionId);
                            if (full && Array.isArray(full.messages)) {
                                node.chatMessages = full.messages;
                            } else {
                                // 后端消息为空时保留本地恢复的消息，防止刷新后聊天内容消失
                                if (!node.chatMessages || node.chatMessages.length === 0) node.chatMessages = [];
                            }
                        } else {
                            // 没有任何会话，创建新的
                            await _createNewSession(true);
                        }

                        // 保存当前会话ID到localStorage
                        try { if (_currentSessionId) localStorage.setItem(_currentSessionKey, _currentSessionId); } catch(e) {}

                        // 渲染
                        _updateSessionTitle();
                        _renderSessionList();
                        renderMessages();
                    } catch(e) {
                        console.error("[susu] 加载会话失败:", e);
                        if (!_currentSessionId) {
                            try { _createNewSession(true); } catch(e2) {}
                        }
                    }
                }

                // 保存会话数据
                function _saveSessions() {
                    try {
                        localStorage.setItem(_sessionsKey, JSON.stringify({
                            sessions: _sessions,
                            currentSessionId: _currentSessionId,
                            sessionCounter: _sessionCounter
                        }));
                    } catch(e) { console.warn("保存会话失败", e); }
                }

                // 创建新会话（默认名称"新对话"，重名自动加序号）
                async function _createNewSession(silent) {
                    // 先保存当前会话
                    if (_currentSessionId) _saveCurrentSession();
                    _sessionCounter++;
                    const id = _genSessionId();
                    // 生成不重复的默认标题
                    let defaultTitle = "新对话";
                    const existingTitles = new Set(Object.values(_sessions).map(function(s) { return s.title || "新对话"; }));
                    let n = 2;
                    while (existingTitles.has(defaultTitle)) {
                        defaultTitle = "新对话 " + n;
                        n++;
                    }
                    const now = Date.now() / 1000;
                    _sessions[id] = { id: id, title: defaultTitle, created_at: now, updated_at: now, message_count: 0 };
                    _currentSessionId = id;
                    try { localStorage.setItem(_currentSessionKey, id); } catch(e) {}
                    await _apiSaveSession(id, defaultTitle, []);
                    if (!silent) {
                        node.chatMessages = [];
                        renderMessages();
                        _updateSessionTitle();
                        _renderSessionList();
                        if (statusBar) statusBar.textContent = "已开启新对话";
                        setTimeout(function() { if (statusBar) statusBar.textContent = "就绪"; }, 1500);
                    }
                    return id;
                }

                // 切换会话
                async function _switchSession(sessionId) {
                    if (!_sessions[sessionId] || sessionId === _currentSessionId) return;
                    if (_currentSessionId) _saveCurrentSession();
                    _currentSessionId = sessionId;
                    try { localStorage.setItem(_currentSessionKey, sessionId); } catch(e) {}
                    const full = await _apiGetSession(sessionId);
                    if (full && Array.isArray(full.messages)) {
                        node.chatMessages = full.messages;
                    } else {
                        node.chatMessages = [];
                    }
                    renderMessages();
                    _updateSessionTitle();
                    _renderSessionList();
                    if (sessionList) sessionList.style.display = "none";
                    if (statusBar) statusBar.textContent = "已切换到: " + _getSessionTitle(_sessions[sessionId]);
                    setTimeout(function() { if (statusBar) statusBar.textContent = "就绪"; }, 1500);
                }

                // 更新当前会话标题显示
                function _updateSessionTitle() {
                    if (_currentSessionId && _sessions[_currentSessionId] && sessionTitleBtn) {
                        const title = _getSessionTitle(_sessions[_currentSessionId]);
                        sessionTitleBtn.textContent = "💬 " + title;
                    }
                }

                // 重命名会话
                async function _renameSession(sessionId) {
                    if (!_sessions[sessionId]) return;
                    const oldTitle = _getSessionTitle(_sessions[sessionId]);
                    const newTitle = prompt("请输入新的对话标题：", oldTitle);
                    if (newTitle === null) return;
                    const trimmed = newTitle.trim();
                    if (!trimmed) return;
                    _sessions[sessionId].title = trimmed;
                    await _apiRenameSession(sessionId, trimmed);
                    _renderSessionList();
                    if (sessionId === _currentSessionId) _updateSessionTitle();
                    if (statusBar) statusBar.textContent = "已重命名为: " + trimmed;
                    setTimeout(function() { if (statusBar) statusBar.textContent = "就绪"; }, 1500);
                }

                // 删除会话
                async function _deleteSession(sessionId) {
                    if (!_sessions[sessionId]) return;
                    const title = _getSessionTitle(_sessions[sessionId]);
                    if (!confirm("确定删除对话「" + title + "」吗？此操作不可恢复。")) return;

                    await _apiDeleteSession(sessionId);
                    delete _sessions[sessionId];

                    // 如果删除的是当前会话，切换到另一个或新建
                    if (sessionId === _currentSessionId) {
                        const remaining = Object.values(_sessions).sort(function(a, b) { return (b.updated_at || 0) - (a.updated_at || 0); });
                        if (remaining.length > 0) {
                            _currentSessionId = remaining[0].id;
                            try { localStorage.setItem(_currentSessionKey, _currentSessionId); } catch(e) {}
                            const full = await _apiGetSession(_currentSessionId);
                            node.chatMessages = (full && Array.isArray(full.messages)) ? full.messages : [];
                        } else {
                            await _createNewSession(true);
                        }
                        renderMessages();
                        _updateSessionTitle();
                    }

                    _renderSessionList();
                    if (statusBar) statusBar.textContent = "已删除对话";
                    setTimeout(function() { if (statusBar) statusBar.textContent = "就绪"; }, 1500);
                }

                // 渲染会话列表
                function _renderSessionList() {
                    if (!sessionList) return;
                    sessionList.innerHTML = "";
                    // 按最后更新时间倒序（最新的在上面）
                    const sorted = Object.values(_sessions).sort(function(a, b) { return (b.updated_at || 0) - (a.updated_at || 0); });
                    sorted.forEach(session => {
                        const item = document.createElement("div");
                        const isCurrent = session.id === _currentSessionId;
                        item.style.cssText = `display:flex;align-items:center;gap:4px;padding:6px 8px;font-size:11px;cursor:pointer;color:${isCurrent ? "#667eea" : "#ccc"};background:${isCurrent ? "#2a2a3e" : "transparent"};border-bottom:1px solid #333;`;
                        item.onmouseenter = () => {
                            item.style.background = isCurrent ? "#2a2a3e" : "#252540";
                            actions.style.opacity = "1";
                        };
                        item.onmouseleave = () => {
                            item.style.background = isCurrent ? "#2a2a3e" : "transparent";
                            actions.style.opacity = "0";
                        };

                        const titleSpan = document.createElement("span");
                        titleSpan.style.cssText = "flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;";
                        titleSpan.textContent = (isCurrent ? "● " : "○ ") + _getSessionTitle(session);
                        titleSpan.onclick = () => _switchSession(session.id);

                        // 操作按钮组（hover时显示）
                        const actions = document.createElement("span");
                        actions.style.cssText = "flex:0 0 auto;display:flex;gap:2px;opacity:0;transition:opacity 0.15s;";

                        const renameBtn = document.createElement("span");
                        renameBtn.textContent = "✎";
                        renameBtn.title = "重命名";
                        renameBtn.style.cssText = "font-size:10px;color:#888;padding:1px 3px;border-radius:2px;cursor:pointer;";
                        renameBtn.onmouseenter = () => { renameBtn.style.color = "#667eea"; renameBtn.style.background = "#2a2a4a"; };
                        renameBtn.onmouseleave = () => { renameBtn.style.color = "#888"; renameBtn.style.background = "transparent"; };
                        renameBtn.onclick = (e) => {
                            e.stopPropagation();
                            _renameSession(session.id);
                        };

                        const delBtn = document.createElement("span");
                        delBtn.textContent = "✕";
                        delBtn.title = "删除";
                        delBtn.style.cssText = "font-size:10px;color:#888;padding:1px 3px;border-radius:2px;cursor:pointer;";
                        delBtn.onmouseenter = () => { delBtn.style.color = "#ff6b6b"; delBtn.style.background = "#3a1a1a"; };
                        delBtn.onmouseleave = () => { delBtn.style.color = "#888"; delBtn.style.background = "transparent"; };
                        delBtn.onclick = (e) => {
                            e.stopPropagation();
                            _deleteSession(session.id);
                        };

                        actions.appendChild(renameBtn);
                        actions.appendChild(delBtn);

                        item.appendChild(titleSpan);
                        item.appendChild(actions);
                        sessionList.appendChild(item);
                    });
                }

                // 保存当前会话消息（发送消息、接收回复后调用）
                function _saveCurrentSessionMessages() {
                    _saveCurrentSession();
                    _updateSessionTitle();
                }

                // 保存状态到localStorage
                function _saveState() {
                    if (!_nodeReady) return;
                    try {
                        const state = {
                            sysExpanded: _sysExpanded,
                            sysHeight: _sysHeight,
                            inputHeight: _inputHeight,
                            chatHeight: _chatHeight,
                            nodeHeight: node.size[1],
                            sysPrompt: sysInput ? sysInput.value : "",
                            messages: Array.isArray(node.chatMessages) ? node.chatMessages : []
                        };
                        localStorage.setItem(_storageKey, JSON.stringify(state));
                    } catch(e) {
                        console.warn("[susu-doubao] 保存状态失败:", e);
                    }
                }

                // 从localStorage恢复状态
                function _loadState() {
                    try {
                        const data = localStorage.getItem(_storageKey);
                        if (data) {
                            const state = JSON.parse(data);
                            if (typeof state.sysExpanded === 'boolean') _sysExpanded = state.sysExpanded;
                            if (typeof state.sysHeight === 'number') _sysHeight = state.sysHeight;
                            if (typeof state.inputHeight === 'number') _inputHeight = state.inputHeight;
                            if (typeof state.chatHeight === 'number') _chatHeight = state.chatHeight;
                            // 恢复系统提示词内容
                            if (typeof state.sysPrompt === 'string' && sysInput) {
                                sysInput.value = state.sysPrompt;
                                if (sysW) sysW.value = state.sysPrompt;
                            }
                            // 验证消息格式，确保每条消息都是有效的对象，防止格式错误导致渲染卡住
                            if (Array.isArray(state.messages)) {
                                const validMessages = state.messages.filter(function(m) {
                                    return m && typeof m === 'object' &&
                                           (m.role === 'user' || m.role === 'assistant') &&
                                           typeof m.content === 'string';
                                });
                                node.chatMessages = validMessages;
                            }
                            return state;
                        }
                    } catch(e) {
                        console.warn("[susu-doubao] 恢复状态失败:", e);
                    }
                    return null;
                }
                function setupResizeHandle(handle, targetEl, minH, maxH, onResize) {
                    if (!handle || !targetEl) {
                        console.warn("[susu-doubao] 拖拽手柄初始化失败: handle=", !!handle, "targetEl=", !!targetEl);
                        return;
                    }
                    console.log("[susu-doubao] 拖拽手柄初始化成功:", handle.className);
                    let _lastH = 0;
                    handle.addEventListener("mouseenter", () => {
                        handle.querySelector("div").style.background = "#667eea";
                    });
                    handle.addEventListener("mouseleave", () => {
                        handle.querySelector("div").style.background = "#555";
                    });
                    let _dragY = 0, _dragH = 0;
                    handle.addEventListener("mousedown", (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        _isDragging = true;
                        _dragY = e.clientY;
                        // 记录拖拽开始时的高度，使用offsetHeight确保准确
                        _dragH = targetEl.offsetHeight;
                        _lastH = _dragH;
                        // 清除可能的内联高度干扰
                        targetEl.style.height = _dragH + "px";
                        const onMove = (ev) => {
                            ev.preventDefault();
                            let newH = _dragH + (ev.clientY - _dragY);
                            newH = Math.max(minH, Math.min(maxH, newH));
                            // 只有高度实际变化时才更新
                            if (Math.abs(newH - _lastH) < 0.5) return;
                            _lastH = newH;
                            targetEl.style.height = newH + "px";
                            if (onResize) onResize(newH);
                        };
                        const onUp = () => {
                            window.removeEventListener("mousemove", onMove);
                            window.removeEventListener("mouseup", onUp);
                            _isDragging = false;
                            // 拖拽结束，onMove中已经实时更新了节点高度，这里不需要再设置
                            if (node.graph) node.graph.setDirtyCanvas(true, false);
                        };
                        window.addEventListener("mousemove", onMove);
                        window.addEventListener("mouseup", onUp);
                    });
                }

                // 安全措施：全局mouseup确保_isDragging不会卡住（防止拖拽中断后onResize一直return）
                window.addEventListener("mouseup", () => {
                    if (_isDragging) {
                        _isDragging = false;
                        console.log("[susu-doubao] 全局mouseup重置_isDragging");
                    }
                });

                // 系统提示词框拖拽
                setupResizeHandle(sysResizeHandle, sysInput, 40, 500, (newH) => {
                    _sysHeight = newH;
                    sysInput.style.height = newH + "px";
                    // 重置聊天区高度为基础高度，消除之前用户拉伸节点产生的extra
                    if (messagesBox) messagesBox.style.height = _chatHeight + "px";
                    // 拖拽手柄时直接设置节点高度为新的基础高度，不保留extra
                    _programmaticResize = true;
                    try {
                        node.setSize([node.size[0], node.computeSize()[1]]);
                    } finally {
                        _programmaticResize = false;
                    }
                    if (node.graph) node.graph.setDirtyCanvas(true, false);
                    _saveState();
                });

                // 从localStorage恢复状态（高度、展开状态、聊天历史）
                const _savedState = _loadState();

                // 异步加载会话管理数据（从后端API）
                _loadSessions();

                // 初始化各元素高度
                messagesBox.style.height = _chatHeight + "px";
                userInput.style.height = _inputHeight + "px";
                sysInput.style.height = _sysHeight + "px";

                // 恢复系统提示词展开状态
                if (_sysExpanded) {
                    sysContent.style.display = "block";
                    sysToggle.textContent = "⚙️ 系统提示词（点击收起）";
                } else {
                    sysContent.style.display = "none";
                    sysToggle.textContent = "⚙️ 系统提示词（点击展开）";
                }

                // 恢复聊天消息
                if (node.chatMessages && node.chatMessages.length > 0) {
                    renderMessages();
                }

                // 标记节点已就绪（之后才允许保存状态，防止初始化时误存）
                _nodeReady = true;

                // 初始化节点高度
                updateComputeSize();

                // 恢复保存的节点高度（如果有），onResize会自动让聊天区填满额外空间
                if (_savedState && typeof _savedState.nodeHeight === 'number' && _savedState.nodeHeight > 0) {
                    _programmaticResize = true;
                    try {
                        node.setSize([node.size[0], _savedState.nodeHeight]);
                    } finally {
                        _programmaticResize = false;
                    }
                }

                // 恢复节点高度后，同步聊天区显示高度（不烘焙extra到_chatHeight，保持基础高度变量准确）
                // extra由onResize动态处理，拖拽手柄时直接用computeSize设置节点高度
                try {
                    const _initMinH = node.computeSize()[1];
                    const _initExtra = Math.max(0, node.size[1] - _initMinH);
                    if (messagesBox) {
                        messagesBox.style.height = (_chatHeight + _initExtra) + "px";
                    }
                    console.log("[susu-doubao] 状态恢复完成: _chatHeight=", _chatHeight, "extra=", _initExtra, "nodeHeight=", node.size[1]);
                } catch(e) {
                    console.error("[susu-doubao] 恢复聊天区高度失败:", e);
                }

                // 聊天区拖拽
                setupResizeHandle(resizeHandle, messagesBox, 80, 1000, (newH) => {
                    _chatHeight = newH;
                    messagesBox.style.height = newH + "px";
                    // 拖拽手柄时直接设置节点高度为新的基础高度，不保留extra
                    // （updateComputeSize会保留extra导致finalH=node.size[1]不变，节点外框不跟随）
                    _programmaticResize = true;
                    try {
                        node.setSize([node.size[0], node.computeSize()[1]]);
                    } finally {
                        _programmaticResize = false;
                    }
                    if (node.graph) node.graph.setDirtyCanvas(true, false);
                    _saveState();
                });

                // 输入框拖拽
                setupResizeHandle(inputResizeHandle, userInput, 30, 400, (newH) => {
                    _inputHeight = newH;
                    userInput.style.height = newH + "px";
                    // 重置聊天区高度为基础高度，消除之前用户拉伸节点产生的extra
                    if (messagesBox) messagesBox.style.height = _chatHeight + "px";
                    // 拖拽手柄时直接设置节点高度为新的基础高度，不保留extra
                    _programmaticResize = true;
                    try {
                        node.setSize([node.size[0], node.computeSize()[1]]);
                    } finally {
                        _programmaticResize = false;
                    }
                    if (node.graph) node.graph.setDirtyCanvas(true, false);
                    _saveState();
                });

                // 重写onResize：用户拉伸节点时，让聊天区填满额外空间（参考doubao_clipboard.js）
                const origOnResize = node.onResize;
                node.onResize = function() {
                    try {
                        if (origOnResize) origOnResize.apply(this, arguments);
                    } catch(e) {}
                    // 拖拽手柄时不修改DOM元素高度，由拖拽手柄完全控制，避免干扰导致只能放大不能缩小
                    if (_isDragging) return;
                    try {
                        // 计算基础最小高度（computeSize只返回基础高度，不含用户拉伸的额外高度）
                        const minH = node.computeSize()[1];
                        // 用户拉伸的额外高度 = 当前节点高度 - 基础高度
                        const extra = Math.max(0, node.size[1] - minH);
                        // 聊天区高度 = 基础聊天区高度 + 用户拉伸的额外高度（让聊天区填满）
                        if (messagesBox) {
                            messagesBox.style.height = (_chatHeight + extra) + "px";
                        }
                    } catch(e) {}
                    // 程序式调整时不保存状态，用户手动拉伸时保存
                    if (!_programmaticResize && _nodeReady) {
                        _saveState();
                    }
                };

                // 同步系统提示词
                if (sysW && sysW.value) {
                    sysInput.value = sysW.value;
                }
                sysInput.addEventListener("input", () => {
                    if (sysW) sysW.value = sysInput.value;
                    // 输入时保存到localStorage，确保刷新后系统提示词不丢失
                    if (_nodeReady) _saveState();
                });

                // 同步用户输入（清空默认值，不显示"你好"）
                if (userInputW) {
                    userInputW.value = "";
                }
                userInput.value = "";

                // 系统提示词折叠（监听内联onclick触发的自定义事件，更可靠）
                window.addEventListener("susuSysToggle", (e) => {
                    try {
                        const btn = e.detail;
                        const content = btn.nextElementSibling;
                        const willExpand = content.style.display === "block";
                        // 展开/收起前保存当前extra（用户拉伸节点产生的额外高度）
                        const currentMin = node.computeSize()[1];
                        const originalExtra = Math.max(0, node.size[1] - currentMin);
                        _sysExpanded = willExpand;
                        if (_sysExpanded) {
                            sysInput.style.height = _sysHeight + "px";
                        }
                        // 设置新高度 = 新的最小高度 + 原始extra（保留用户拉伸的额外空间）
                        const newMin = node.computeSize()[1];
                        _programmaticResize = true;
                        try {
                            node.setSize([node.size[0], newMin + originalExtra]);
                        } finally {
                            _programmaticResize = false;
                        }
                        // 同步聊天区高度：setSize后计算新的extra，确保聊天区填满不出现底部空白
                        // （onResize可能已设置正确高度，这里再确认一次，双保险）
                        try {
                            const afterMin = node.computeSize()[1];
                            const afterExtra = Math.max(0, node.size[1] - afterMin);
                            if (messagesBox) {
                                messagesBox.style.height = (_chatHeight + afterExtra) + "px";
                            }
                        } catch(e) {}
                        if (node.graph) node.graph.setDirtyCanvas(true, false);
                        _saveState();
                    } catch(err) {
                        console.error("[susu-doubao] 系统提示词切换错误:", err);
                    }
                });



                // 发送消息
                async function sendMessage() {
                    const text = userInput.value.trim();
                    if (!text) return;

                    // 同步到原始widget
                    if (userInputW) userInputW.value = text;

                    // 添加用户消息（包含图片数据，如果有）
                    const userMsg = { role: "user", content: text, timestamp: new Date().toLocaleTimeString('zh-CN', {hour12: false}) };
                    if (node._imageB64) userMsg.image = node._imageB64;
                    node.chatMessages.push(userMsg);
                    renderMessages();
                    _saveState();
                    _saveCurrentSessionMessages(); // 保存到当前会话
                    userInput.value = "";
                    statusBar.textContent = "正在生成...";
                    statusBar.style.color = "#ffcc00";

                    // 读取参数
                    const getVal = (w, def = "") => w ? w.value : def;
                    const apiBase = getVal(apiUrlW, "http://127.0.0.1:8080/v1").trim();
                    let modelName = getVal(modelW, "").trim();
                    if (!modelName) modelName = "default"; // 模型名称为空时用default
                    const apiKey = getVal(apiKeyW, "").trim();
                    const systemPrompt = sysInput.value.trim();
                    const temperature = parseFloat(getVal(tempW, 0.7));
                    const topP = parseFloat(getVal(topPW, 0.9));
                    const topK = parseInt(getVal(topKW, 40));
                    const maxTokens = parseInt(getVal(maxLenW, 1024));
                    const useCloudDefault = cloudDefaultW ? !!cloudDefaultW.value : false;

                    // 构建消息（系统提示词 + 全部历史消息）
                    const messages = [];
                    if (systemPrompt) {
                        messages.push({ role: "system", content: systemPrompt });
                    }
                    // 发送所有历史消息（包括用户刚输入的）
                    for (let i = 0; i < node.chatMessages.length; i++) {
                        messages.push(node.chatMessages[i]);
                    }

                    const payload = {
                        base_url: apiBase,
                        api_key: apiKey,
                        model: modelName,
                        messages: messages,
                        temperature: temperature,
                        top_p: topP,
                        top_k: topK,
                        max_tokens: maxTokens,
                        use_cloud_default: useCloudDefault,
                        image_base64: node._imageB64 || "",
                    };
                    console.log("[susu-doubao] 发送请求参数:", {useCloudDefault, temperature: useCloudDefault ? "(不传,用云端默认)" : temperature, top_p: useCloudDefault ? "(不传)" : topP, top_k: useCloudDefault ? "(不传)" : topK, max_tokens: useCloudDefault ? "(不传)" : maxTokens, image: node._imageB64 ? `已附带图像(base64长度:${node._imageB64.length})` : "无图像"});

                    try {
                        let fullContent = "";
                        let fullReasoning = "";
                        let displayedContent = "";
                        let displayedReasoning = "";
                        let streamDone = false;

                        // 使用真正的流式输出（SSE）
                        const response = await fetch("/susu-doubao/ai/generate_stream", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify(payload),
                        });

                        if (!response.ok) {
                            const errText = await response.text();
                            throw new Error(`HTTP ${response.status}: ${errText}`);
                        }

                        // 添加空的AI消息占位
                        node.chatMessages.push({ role: "assistant", content: "", reasoning: "", timestamp: new Date().toLocaleTimeString('zh-CN', {hour12: false}) });
                        renderMessages();

                        // 打字机效果：用Promise等待打字机完成
                        await new Promise((resolve) => {
                            const reader = response.body.getReader();
                            const decoder = new TextDecoder();
                            let buffer = "";

                            // 打字机定时器：每25ms显示1-2个字，更平滑
                            const typeTimer = setInterval(() => {
                                let needUpdate = false;
                                // 显示content
                                if (displayedContent.length < fullContent.length) {
                                    const step = Math.max(1, Math.ceil((fullContent.length - displayedContent.length) / 20));
                                    displayedContent = fullContent.substring(0, displayedContent.length + step);
                                    node.chatMessages[node.chatMessages.length - 1].content = displayedContent;
                                    needUpdate = true;
                                }
                                // 显示reasoning
                                if (displayedReasoning.length < fullReasoning.length) {
                                    const step = Math.max(1, Math.ceil((fullReasoning.length - displayedReasoning.length) / 20));
                                    displayedReasoning = fullReasoning.substring(0, displayedReasoning.length + step);
                                    node.chatMessages[node.chatMessages.length - 1].reasoning = displayedReasoning;
                                    needUpdate = true;
                                }
                                if (needUpdate) {
                                    renderMessages();
                                    const total = displayedContent.length + displayedReasoning.length;
                                    statusBar.textContent = `生成中... ${total} 字`;
                                }
                                // 流结束且全部显示完，停止定时器
                                if (streamDone && displayedContent.length >= fullContent.length && displayedReasoning.length >= fullReasoning.length) {
                                    clearInterval(typeTimer);
                                    // 确保显示完整内容
                                    node.chatMessages[node.chatMessages.length - 1].content = fullContent;
                                    node.chatMessages[node.chatMessages.length - 1].reasoning = fullReasoning;
                                    renderMessages();
                                    resolve();
                                }
                            }, 25);

                            // 异步读取流
                            (async () => {
                                while (true) {
                                    const { done, value } = await reader.read();
                                    if (done) {
                                        streamDone = true;
                                        break;
                                    }

                                    buffer += decoder.decode(value, { stream: true });
                                    const lines = buffer.split("\n");
                                    buffer = lines.pop() || "";

                                    for (const line of lines) {
                                        if (!line.trim() || !line.startsWith("data:")) continue;
                                        const data = line.slice(5).trim();
                                        if (data === "[DONE]") continue;

                                        try {
                                            const json = JSON.parse(data);
                                            if (json.error) throw new Error(json.error);
                                            const contentDelta = json.content || "";
                                            const reasoningDelta = json.reasoning || "";
                                            if (contentDelta) fullContent += contentDelta;
                                            if (reasoningDelta) fullReasoning += reasoningDelta;
                                        } catch (e) {
                                            if (e.message && !e.message.includes("JSON")) throw e;
                                        }
                                    }
                                }
                            })();
                        });

                        statusBar.textContent = `✅ 完成 | ${fullContent.length} 字`;
                        statusBar.style.color = "#88ff88";
                        _saveState();
                        _saveCurrentSessionMessages(); // 保存到当前会话

                    } catch (e) {
                        console.error("[susu-doubao] 生成失败:", e);
                        statusBar.textContent = "❌ 错误";
                        statusBar.style.color = "#ff8888";
                        node.chatMessages.push({ role: "assistant", content: `❌ ${e.message}\n\n请检查：\n1. API地址是否正确\n2. 模型是否已启动\n3. 网络是否通畅`, timestamp: new Date().toLocaleTimeString('zh-CN', {hour12: false}) });
                        renderMessages();
                    }
                }

                // 发送按钮点击
                sendBtn.onclick = sendMessage;

                // 输入框Enter发送
                userInput.addEventListener("keydown", (e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        sendMessage();
                    }
                });

                // 清空对话
                clearBtn.onclick = () => {
                    node.chatMessages = [];
                    renderMessages();
                    statusBar.textContent = "已清空";
                    statusBar.style.color = "#88ff88";
                    _saveState();
                    _saveCurrentSessionMessages(); // 清空当前会话
                    _updateSessionTitle();
                };

                // 移除图像（视觉模型分析图片时使用）
                if (clearImageBtn) {
                    clearImageBtn.onclick = () => {
                        node._imageB64 = "";
                        if (imagePreviewContainer) imagePreviewContainer.style.display = "none";
                        if (imagePreviewImg) imagePreviewImg.src = "";
                        if (imageStatus) imageStatus.textContent = "";
                        statusBar.textContent = "已移除图像";
                        statusBar.style.color = "#88ff88";
                        // 恢复节点高度（图片预览高度归零）
                        if (node._updateImagePreviewHeight) {
                            node._updateImagePreviewHeight(false);
                        }
                        console.log("[susu-doubao] 图像已移除");
                    };
                }

                // ===== 会话管理事件绑定 =====
                // 新对话按钮
                newSessionBtn.onclick = () => {
                    _createNewSession(false);
                };

                // 会话标题按钮（展开/收起会话列表）
                sessionTitleBtn.onclick = (e) => {
                    e.stopPropagation();
                    if (sessionList.style.display === "none" || !sessionList.style.display) {
                        _renderSessionList();
                        sessionList.style.display = "block";
                    } else {
                        sessionList.style.display = "none";
                    }
                };

                // 点击外部关闭会话列表
                document.addEventListener("click", (e) => {
                    if (!sessionList.contains(e.target) && e.target !== sessionTitleBtn) {
                        sessionList.style.display = "none";
                    }
                });


                // 保存引用
                node._messagesBox = messagesBox;
                node._statusBar = statusBar;
                node._renderMessages = renderMessages;
                node._chatEl = chatEl;
                node._imagePreview = imagePreviewImg;
                node._imageStatus = imageStatus;
                node._imagePreviewContainer = imagePreviewContainer;

                // 图片预览显示/隐藏时更新节点高度（保留用户拉伸的extra空间）
                node._updateImagePreviewHeight = function(show) {
                    try {
                        const oldMin = node.computeSize()[1];
                        const originalExtra = Math.max(0, node.size[1] - oldMin);
                        _imagePreviewHeight = show ? 100 : 0;
                        const newMin = node.computeSize()[1];
                        _programmaticResize = true;
                        try {
                            node.setSize([node.size[0], newMin + originalExtra]);
                        } finally {
                            _programmaticResize = false;
                        }
                        // 同步聊天区高度，填满extra空间
                        const afterExtra = Math.max(0, node.size[1] - newMin);
                        if (messagesBox) {
                            messagesBox.style.height = (_chatHeight + afterExtra) + "px";
                        }
                        if (node.graph) node.graph.setDirtyCanvas(true, false);
                        console.log("[susu-doubao] 图片预览高度更新: show=" + show + ", newMin=" + newMin + ", extra=" + originalExtra + "->" + afterExtra);
                    } catch(e) {
                        console.error("[susu-doubao] 更新图片预览高度失败:", e);
                    }
                };

                console.log("[susu-doubao] LLMChat 节点初始化完成（聊天界面模式）");
                } catch(e) {
                    console.error("[susu-doubao] onNodeCreated错误:", e);
                }
            };

            // 节点执行完成回调：捕获输出中的图像base64，用于视觉模型分析
            const onExecuted = nodeType.prototype.onExecuted;
            nodeType.prototype.onExecuted = function (output) {
                if (onExecuted) onExecuted.apply(this, arguments);
                console.log("[susu-doubao] ===== onExecuted触发 =====");
                console.log("[susu-doubao] output完整对象:", JSON.stringify(output).substring(0, 500));
                console.log("[susu-doubao] output类型:", typeof output, "是否数组:", Array.isArray(output));
                console.log("[susu-doubao] output的keys:", output ? Object.keys(output) : "无");
                // 健壮提取base64：ComfyUI的output格式是 {"0": ["base64字符串"]} 或 {"图像Base64": ["base64"]}
                let b64 = "";
                if (output) {
                    // 尝试 output["0"][0]
                    if (output["0"] && Array.isArray(output["0"]) && output["0"].length > 0 && typeof output["0"][0] === "string") {
                        b64 = output["0"][0];
                        console.log("[susu-doubao] 从output['0'][0]提取到base64, 长度:", b64.length);
                    }
                    // 尝试 output["图像Base64"][0]
                    else if (output["图像Base64"] && Array.isArray(output["图像Base64"]) && output["图像Base64"].length > 0 && typeof output["图像Base64"][0] === "string") {
                        b64 = output["图像Base64"][0];
                        console.log("[susu-doubao] 从output['图像Base64'][0]提取到base64, 长度:", b64.length);
                    }
                    // 尝试 output[0] 直接是字符串
                    else if (output[0] && typeof output[0] === "string") {
                        b64 = output[0];
                        console.log("[susu-doubao] 从output[0]直接提取到base64, 长度:", b64.length);
                    }
                    // 尝试 output[0][0]
                    else if (output[0] && Array.isArray(output[0]) && output[0].length > 0 && typeof output[0][0] === "string") {
                        b64 = output[0][0];
                        console.log("[susu-doubao] 从output[0][0]提取到base64, 长度:", b64.length);
                    }
                    else {
                        console.log("[susu-doubao] 未能从output中提取到base64字符串");
                    }
                }
                if (b64) {
                    this._imageB64 = b64;
                    if (this._imagePreviewContainer) {
                        this._imagePreviewContainer.style.display = "flex";
                        console.log("[susu-doubao] 图像预览容器已显示(display:flex)");
                    } else {
                        console.log("[susu-doubao] 警告: _imagePreviewContainer不存在!");
                    }
                    if (this._imagePreview) {
                        this._imagePreview.src = "data:image/png;base64," + b64;
                        console.log("[susu-doubao] 图像src已设置, dataURL长度:", this._imagePreview.src.length);
                    } else {
                        console.log("[susu-doubao] 警告: _imagePreview不存在!");
                    }
                    if (this._imageStatus) {
                        this._imageStatus.textContent = "🖼 已加载图像，可发送给视觉模型分析";
                        this._imageStatus.style.color = "#667eea";
                    }
                    // 更新节点高度（图片预览占约100px）
                    if (this._updateImagePreviewHeight) {
                        this._updateImagePreviewHeight(true);
                    }
                    console.log("[susu-doubao] 图像加载完成, base64长度:", b64.length);
                }
                console.log("[susu-doubao] ===== onExecuted结束 =====");
            };
        }
    },
});


// ============================================================
// LLM 模型控制台节点 - 简单结果显示
// ============================================================
app.registerExtension({
    name: "susu-doubao.LLMModelConsole",
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name !== "LLMModelConsole") return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            if (onNodeCreated) onNodeCreated.apply(this, arguments);
            const node = this;

            try {
                // 创建容器
                const container = document.createElement("div");
                container.style.cssText = "width: 100%; display: flex; flex-direction: column; gap: 3px; padding: 2px 4px;";

                // 1. API地址显示行
                const apiRow = document.createElement("div");
                apiRow.style.cssText = "width: 100%; display: none; align-items: center; gap: 4px; padding: 4px 6px; background: #1a2a1a; border: 1px solid #2a4a2a; border-radius: 3px; font-family: monospace; font-size: 11px; color: #6f6;";
                const apiText = document.createElement("span");
                apiText.style.cssText = "flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;";
                apiText.textContent = "";
                const copyBtn = document.createElement("span");
                copyBtn.style.cssText = "cursor: pointer; color: #888; padding: 0 4px; user-select: none;";
                copyBtn.textContent = "复制";
                copyBtn.title = "复制API地址";
                copyBtn.onclick = (e) => {
                    e.stopPropagation();
                    const url = apiText.textContent.replace("API: ", "");
                    if (url) {
                        navigator.clipboard.writeText(url).then(() => {
                            copyBtn.textContent = "已复制";
                            setTimeout(() => copyBtn.textContent = "复制", 1500);
                        });
                    }
                };
                apiRow.appendChild(apiText);
                apiRow.appendChild(copyBtn);
                container.appendChild(apiRow);

                // 2. 显存占用显示行
                const vramRow = document.createElement("div");
                vramRow.style.cssText = "width: 100%; display: none; align-items: center; gap: 4px; padding: 4px 6px; background: #1a1a2a; border: 1px solid #3a3a5a; border-radius: 3px; font-family: monospace; font-size: 11px; color: #88f;";
                const vramText = document.createElement("span");
                vramText.style.cssText = "flex: 1;";
                vramText.textContent = "";
                vramRow.appendChild(vramText);
                container.appendChild(vramRow);

                // 3. 操作结果显示区域
                const resultWrap = document.createElement("div");
                resultWrap.style.cssText = "width: 100%; border: 1px solid #444; border-radius: 4px; overflow: hidden; background: #1a1a2a;";
                const resultTitle = document.createElement("div");
                resultTitle.style.cssText = "padding: 2px 6px; background: #222233; border-bottom: 1px solid #333; font-size: 10px; color: #888; font-family: sans-serif;";
                resultTitle.textContent = "操作结果";
                const resultContent = document.createElement("div");
                resultContent.style.cssText = "padding: 4px 6px; background: #1a1a2a; font-family: monospace; font-size: 11px; color: #ccc; white-space: pre-wrap; word-break: break-word; max-height: 70px; overflow-y: auto; line-height: 1.4;";
                resultContent.textContent = "运行操作后，结果将显示在这里...";
                resultWrap.appendChild(resultTitle);
                resultWrap.appendChild(resultContent);
                container.appendChild(resultWrap);

                // 添加DOM widget
                const domWidget = node.addDOMWidget("console_result", "控制台结果", container, () => {}, {});
                domWidget.computeSize = function(width) {
                    return [width, 130];
                };

                // 保存引用
                node._apiRow = apiRow;
                node._apiText = apiText;
                node._vramRow = vramRow;
                node._vramText = vramText;
                node._resultContent = resultContent;

            } catch (e) {
                console.error("[susu-doubao] 控制台节点onNodeCreated错误:", e);
            }
        };

        // onExecuted回调 - 显示操作结果
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (output) {
            if (onExecuted) onExecuted.apply(this, arguments);
            const node = this;

            try {
                let result = "";
                let apiUrl = "";

                // 从多种可能的位置获取结果
                if (output.ui && output.ui.text && output.ui.text.length > 0) {
                    result = output.ui.text[0];
                } else if (output.text && output.text.length > 0) {
                    result = output.text[0];
                } else if (output["1"] && output["1"].length > 0) {
                    result = output["1"][0];
                }

                // 获取API地址
                if (output.ui && output.ui["0"] && output.ui["0"].length > 0) {
                    apiUrl = output.ui["0"][0];
                } else if (output["0"] && output["0"].length > 0) {
                    apiUrl = output["0"][0];
                }

                // 显示API地址
                if (node._apiRow) {
                    if (apiUrl) {
                        node._apiRow.style.display = "flex";
                        node._apiText.textContent = "API: " + apiUrl;
                    } else {
                        node._apiRow.style.display = "none";
                    }
                }

                // 解析并显示显存占用
                if (node._vramRow) {
                    if (result && (result.includes("显存") || result.includes("VRAM") || result.includes("vram"))) {
                        // 从结果中提取显存信息
                        const vramMatch = result.match(/显存[：:]\s*([^|\n]+)/);
                        const vramEstMatch = result.match(/预估显存[：:]\s*([^|\n]+)/);
                        if (vramMatch) {
                            node._vramRow.style.display = "flex";
                            node._vramText.textContent = "显存: " + vramMatch[1].trim();
                        } else if (vramEstMatch) {
                            node._vramRow.style.display = "flex";
                            node._vramText.textContent = "预估显存: " + vramEstMatch[1].trim();
                        } else {
                            node._vramRow.style.display = "none";
                        }
                    } else {
                        node._vramRow.style.display = "none";
                    }
                }

                // 显示操作结果
                if (node._resultContent && result) {
                    node._resultContent.textContent = result;
                    // 根据结果类型设置颜色
                    if (result.startsWith("❌") || result.startsWith("错误") || result.includes("失败")) {
                        node._resultContent.style.color = "#f88";
                    } else if (result.startsWith("✅") || result.startsWith("成功") || result.includes("已启动")) {
                        node._resultContent.style.color = "#8f8";
                    } else {
                        node._resultContent.style.color = "#ccc";
                    }
                    // 滚动到底部
                    requestAnimationFrame(() => {
                        node._resultContent.scrollTop = node._resultContent.scrollHeight;
                    });
                }

            } catch (e) {
                console.error("[susu-doubao] 控制台节点onExecuted错误:", e);
            }
        };
    }
});
