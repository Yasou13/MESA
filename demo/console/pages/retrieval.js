window.initRetrievalPage = function(container) {
    container.innerHTML =
    '<div class="page-header">' +
        '<h2 class="page-title">Retrieval Inspector</h2>' +
        '<p style="color: #94a3b8; margin-top: 8px; font-size: 0.95rem;">Analyze why specific memories were selected or rejected.</p>' +
    '</div>' +
    '<div id="retrievalContainer"><p style="color: #64748b;">Loading retrieval data...</p></div>';

    fetch('../console/data/mock-retrieval.json')
        .then(function(res) { return res.json(); })
        .then(function(data) {
            var el = document.getElementById('retrievalContainer');
            el.innerHTML =
            '<div style="background: rgba(11, 13, 20, 0.85); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 18px; overflow: hidden; box-shadow: 0 16px 36px -8px rgba(0, 0, 0, 0.5); margin-bottom: 24px;">' +
                '<div style="background: rgba(15, 17, 26, 0.95); padding: 14px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid rgba(255, 255, 255, 0.06);">' +
                    '<div style="display: flex; gap: 7px; align-items: center;">' +
                        '<span style="width: 11px; height: 11px; border-radius: 50%; background: #ff5f56; border: 1px solid #e0443e;"></span>' +
                        '<span style="width: 11px; height: 11px; border-radius: 50%; background: #ffbd2e; border: 1px solid #dea123;"></span>' +
                        '<span style="width: 11px; height: 11px; border-radius: 50%; background: #27c93f; border: 1px solid #1aab29;"></span>' +
                    '</div>' +
                    '<span style="font-family: JetBrains Mono, monospace; font-size: 0.8rem; color: #94a3b8;">Alpha RRF Retrieval Pipeline</span>' +
                    '<span style="display: inline-flex; align-items: center; gap: 6px; padding: 3px 12px; border-radius: 9999px; font-size: 0.72rem; font-weight: 600; background: rgba(6, 182, 212, 0.12); color: #22d3ee; border: 1px solid rgba(6, 182, 212, 0.25);">STAGE-2 RERANKED</span>' +
                '</div>' +
                '<div style="padding: 20px;">' +
                    '<div style="font-size: 0.78rem; color: #64748b; font-weight: 600; text-transform: uppercase; margin-bottom: 8px;">Query</div>' +
                    '<div style="font-family: JetBrains Mono, monospace; font-size: 0.88rem; background: rgba(0,0,0,0.3); padding: 14px 18px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.05); color: #34d399;">' + data.query + '</div>' +
                '</div>' +
            '</div>' +
            '<h3 style="font-size: 1.1rem; font-weight: 700; margin-bottom: 16px; color: #f8fafc;">Retrieval Results</h3>';

            var cols = [
                { key: 'rank', label: 'Rank' },
                { key: 'memory_id', label: 'Memory ID' },
                { key: 'final_score', label: 'Final Score' },
                { key: 'decision_badge', label: 'Decision' }
            ];
            var tableData = data.results.map(function(r) {
                return Object.assign({}, r, { decision_badge: window.UI.StatusBadge.create(r.decision) });
            });
            var table = window.UI.Table.create(cols, tableData, function(row) {
                window.UI.Inspector.open("Result Details", ["Scores", "Content"], {
                    "Scores": "<ul><li><strong>Vector Score:</strong> " + row.vector_score + "</li><li><strong>Keyword Score:</strong> " + row.keyword_score + "</li><li><strong>Final Score:</strong> " + row.final_score + "</li></ul>",
                    "Content": "<p>" + row.content + "</p>"
                });
            });
            el.appendChild(table);
        });
};
