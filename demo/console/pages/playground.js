window.initPlaygroundPage = function(container) {
    container.innerHTML = '<div class="page-header">' +
        '<h2 class="page-title">Playground</h2>' +
        '<p style="color: #94a3b8; margin-top: 8px; font-size: 0.95rem;">Direct-write sandbox and interactive memory testing.</p>' +
    '</div>' +
    '<div style="display: flex; gap: 24px; flex: 1; min-height: 0;">' +
        '<div style="flex: 1; display: flex; flex-direction: column; background: rgba(11, 13, 20, 0.85); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 18px; overflow: hidden; box-shadow: 0 16px 36px -8px rgba(0, 0, 0, 0.5);">' +
            '<div style="background: rgba(15, 17, 26, 0.95); padding: 14px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid rgba(255, 255, 255, 0.06);">' +
                '<div style="display: flex; gap: 7px; align-items: center;">' +
                    '<span style="width: 11px; height: 11px; border-radius: 50%; background: #ff5f56; border: 1px solid #e0443e;"></span>' +
                    '<span style="width: 11px; height: 11px; border-radius: 50%; background: #ffbd2e; border: 1px solid #dea123;"></span>' +
                    '<span style="width: 11px; height: 11px; border-radius: 50%; background: #27c93f; border: 1px solid #1aab29;"></span>' +
                '</div>' +
                '<span style="font-family: JetBrains Mono, monospace; font-size: 0.8rem; color: #94a3b8; font-weight: 500;">MESA Playground — Direct-Write Interface</span>' +
                '<div style="display: flex; align-items: center; gap: 8px;">' +
                    '<span style="width: 8px; height: 8px; border-radius: 50%; background: #10b981; box-shadow: 0 0 8px #10b981;"></span>' +
                    '<span style="font-size: 0.75rem; color: #64748b;">Connected</span>' +
                '</div>' +
            '</div>' +
            '<div id="chatHistory" style="flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px;">' +
                '<div style="align-self: center; padding: 4px 12px; font-size: 0.82rem; color: #64748b; font-style: italic;">System initialized. Direct-write pipeline listening...</div>' +
            '</div>' +
            '<div style="padding: 16px 20px; border-top: 1px solid rgba(255, 255, 255, 0.06); background: rgba(0, 0, 0, 0.2);">' +
                '<form id="playgroundForm" style="display: flex; gap: 12px;">' +
                    '<input type="text" class="input" id="playgroundInput" placeholder="Ask a question or store a memory block..." required style="flex: 1; border-radius: 9999px; padding: 14px 20px;">' +
                    '<button type="submit" class="btn btn-primary" style="border-radius: 50%; width: 48px; height: 48px; padding: 0; flex-shrink: 0;">' +
                        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M2.01 21L23 12L2.01 3L2 10l15 2-15 2z" fill="currentColor"/></svg>' +
                    '</button>' +
                '</form>' +
            '</div>' +
        '</div>' +
    '</div>';

    var form = document.getElementById('playgroundForm');
    var input = document.getElementById('playgroundInput');
    var history = document.getElementById('chatHistory');

    // Add animation keyframe
    if (!document.getElementById('msgPopStyle')) {
        var style = document.createElement('style');
        style.id = 'msgPopStyle';
        style.textContent = '@keyframes msgPop { 0% { opacity: 0; transform: scale(0.95) translateY(10px); } 100% { opacity: 1; transform: scale(1) translateY(0); } }';
        document.head.appendChild(style);
    }

    form.addEventListener('submit', function(e) {
        e.preventDefault();
        var text = input.value.trim();
        if (!text) return;

        var userDiv = document.createElement('div');
        userDiv.style.cssText = 'align-self: flex-end; max-width: 80%; animation: msgPop 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);';
        userDiv.innerHTML = '<div style="background: #8b5cf6; color: white; padding: 14px 18px; border-radius: 16px; border-bottom-right-radius: 4px; font-size: 0.95rem; line-height: 1.5;">' + text + '</div>';
        history.appendChild(userDiv);
        input.value = '';

        setTimeout(function() {
            var aiDiv = document.createElement('div');
            aiDiv.style.cssText = 'align-self: flex-start; max-width: 80%; animation: msgPop 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);';
            aiDiv.innerHTML = '<div style="background: rgba(255, 255, 255, 0.06); border: 1px solid rgba(255, 255, 255, 0.07); padding: 14px 18px; border-radius: 16px; border-bottom-left-radius: 4px; font-size: 0.95rem; line-height: 1.5; cursor: pointer;" onclick="inspectResponse()">' +
                'Processing your input...' +
                '<div style="margin-top: 10px; padding-top: 10px; border-top: 1px dashed rgba(255, 255, 255, 0.08); font-family: JetBrains Mono, monospace; font-size: 0.78rem; color: #94a3b8;">' +
                    '<span style="color: #34d399;">&#10003;</span> 2 memories used &bull; ' +
                    '<span style="color: #06b6d4;">145ms</span> &bull; ' +
                    '<span style="color: #64748b;">Click to inspect</span>' +
                '</div>' +
            '</div>';
            history.appendChild(aiDiv);
            history.scrollTop = history.scrollHeight;
        }, 800);
    });

    window.inspectResponse = function() {
        window.UI.Inspector.open(
            "Telemetry Inspector",
            ["Context", "Retrieved Memories", "Pipeline Trace"],
            {
                "Context": "<p><strong>System Prompt:</strong> You are MESA Agent.</p><p><strong>Context Limit:</strong> 8192</p>",
                "Retrieved Memories": "<p>Memory 1: User prefers PostgreSQL (Score: 0.92)</p><p>Memory 2: Interested in Enterprise Plan (Score: 0.85)</p>",
                "Pipeline Trace": "<ul><li>Query Normalization (2ms)</li><li>Vector Search (15ms)</li><li>Reranking (45ms)</li></ul>"
            }
        );
    };
};
