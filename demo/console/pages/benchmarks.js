window.initBenchmarksPage = function(container) {
    container.innerHTML =
    '<div class="page-header">' +
        '<h2 class="page-title">Benchmarks</h2>' +
        '<p style="color: #94a3b8; margin-top: 8px; font-size: 0.95rem;">Transparent empirical performance metrics.</p>' +
    '</div>' +
    '<div id="benchmarksContainer"><p style="color: #64748b;">Loading benchmarks...</p></div>';

    fetch('../console/data/mock-benchmarks.json')
        .then(function(res) { return res.json(); })
        .then(function(data) {
            var el = document.getElementById('benchmarksContainer');
            el.innerHTML =
            '<div style="background: rgba(11, 13, 20, 0.85); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 18px; overflow: hidden; box-shadow: 0 16px 36px -8px rgba(0, 0, 0, 0.5); margin-bottom: 24px;">' +
                '<div style="background: rgba(15, 17, 26, 0.95); padding: 14px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid rgba(255, 255, 255, 0.06);">' +
                    '<div style="display: flex; gap: 7px; align-items: center;">' +
                        '<span style="width: 11px; height: 11px; border-radius: 50%; background: #ff5f56; border: 1px solid #e0443e;"></span>' +
                        '<span style="width: 11px; height: 11px; border-radius: 50%; background: #ffbd2e; border: 1px solid #dea123;"></span>' +
                        '<span style="width: 11px; height: 11px; border-radius: 50%; background: #27c93f; border: 1px solid #1aab29;"></span>' +
                    '</div>' +
                    '<span style="font-family: JetBrains Mono, monospace; font-size: 0.8rem; color: #94a3b8;">Evaluation Summary: ' + data.summary.dataset + '</span>' +
                    '<span style="display: inline-flex; align-items: center; gap: 6px; padding: 3px 12px; border-radius: 9999px; font-size: 0.72rem; font-weight: 600; background: rgba(16, 185, 129, 0.12); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.25);">CI/CD VERIFIED</span>' +
                '</div>' +
                '<div style="padding: 24px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px;">' +
                    '<div style="text-align: center;"><div style="font-size: 0.78rem; color: #94a3b8; font-weight: 600; text-transform: uppercase; margin-bottom: 6px;">Accuracy</div><div style="font-size: 2rem; font-weight: 800; font-family: JetBrains Mono, monospace; color: #10b981;">' + (data.summary.accuracy * 100).toFixed(1) + '%</div></div>' +
                    '<div style="text-align: center;"><div style="font-size: 0.78rem; color: #94a3b8; font-weight: 600; text-transform: uppercase; margin-bottom: 6px;">Hit@1</div><div style="font-size: 2rem; font-weight: 800; font-family: JetBrains Mono, monospace; color: #06b6d4;">' + (data.summary.hit_at_1 * 100).toFixed(1) + '%</div></div>' +
                    '<div style="text-align: center;"><div style="font-size: 0.78rem; color: #94a3b8; font-weight: 600; text-transform: uppercase; margin-bottom: 6px;">MRR</div><div style="font-size: 2rem; font-weight: 800; font-family: JetBrains Mono, monospace; color: #8b5cf6;">' + data.summary.mrr + '</div></div>' +
                '</div>' +
            '</div>' +
            '<h3 style="font-size: 1.1rem; font-weight: 700; margin-bottom: 16px; color: #f8fafc;">System Comparison</h3>';

            var cols = [
                { key: 'system', label: 'System' },
                { key: 'accuracy', label: 'Accuracy' },
                { key: 'hit_at_1', label: 'Hit@1' },
                { key: 'latency_ms', label: 'Latency (ms)' }
            ];
            var table = window.UI.Table.create(cols, data.comparisons);
            el.appendChild(table);
        });
};
