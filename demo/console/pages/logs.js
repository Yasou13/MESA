window.initLogsPage = function(container) {
    container.innerHTML =
    '<div class="page-header">' +
        '<h2 class="page-title">System Logs</h2>' +
        '<p style="color: #94a3b8; margin-top: 8px; font-size: 0.95rem;">Real-time pipeline execution logs.</p>' +
    '</div>' +
    '<div id="logsContainer"><p style="color: #64748b;">Loading logs...</p></div>';

    fetch('../console/data/mock-logs.json')
        .then(function(res) { return res.json(); })
        .then(function(data) {
            var el = document.getElementById('logsContainer');
            el.innerHTML = '';
            var cols = [
                { key: 'timestamp', label: 'Time' },
                { key: 'level_badge', label: 'Level' },
                { key: 'operation_type', label: 'Operation' },
                { key: 'message', label: 'Message' },
                { key: 'duration_ms', label: 'Duration' }
            ];
            var tableData = data.map(function(log) {
                return Object.assign({}, log, {
                    level_badge: window.UI.StatusBadge.create(log.level === 'ERROR' ? 'error' : (log.level === 'WARNING' ? 'warning' : 'info'))
                });
            });
            var table = window.UI.Table.create(cols, tableData, function(row) {
                window.UI.Inspector.open("Log Details", ["Payload", "Metadata"], {
                    "Payload": '<pre style="white-space: pre-wrap; font-family: JetBrains Mono, monospace; font-size: 0.82rem; line-height: 1.65; background: rgba(11,13,20,0.85); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 16px; color: #f8fafc;">' + JSON.stringify(row, null, 2) + '</pre>',
                    "Metadata": "<p><strong>Trace ID:</strong> TRACE-10293848</p>"
                });
            });
            el.appendChild(table);
        });
};
