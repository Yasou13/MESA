window.initOverviewPage = function(container) {
    var cardStyle = 'position: relative; overflow: hidden; background: rgba(16, 18, 28, 0.55); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.07); border-radius: 18px; padding: 28px; box-shadow: 0 16px 36px -8px rgba(0, 0, 0, 0.5);';
    var numStyle = 'font-size: 2.4rem; font-weight: 800; font-family: JetBrains Mono, monospace; margin: 10px 0 4px 0;';
    var labelStyle = 'font-size: 0.78rem; color: #94a3b8; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;';
    var subStyle = 'font-size: 0.78rem; color: #64748b;';
    var statusBadge = '<span style="display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 9999px; font-size: 0.72rem; font-weight: 600; background: rgba(16, 185, 129, 0.12); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.25);">HEALTHY</span>';
    var rowStyle = 'display: flex; justify-content: space-between; align-items: center; padding: 14px 18px; background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 12px;';

    container.innerHTML =
    '<div class="page-header">' +
        '<h2 class="page-title">System Overview</h2>' +
        '<p style="color: #94a3b8; margin-top: 8px; font-size: 0.95rem;">Real-time telemetry across the Triple-Store Memory Engine.</p>' +
    '</div>' +
    '<div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 28px;">' +
        '<div style="' + cardStyle + '">' +
            '<div style="position: absolute; top: 0; left: 0; right: 0; height: 3px; background: #10b981; box-shadow: 0 0 15px #10b981;"></div>' +
            '<span style="' + labelStyle + '">Total Memories</span>' +
            '<div style="' + numStyle + 'color: #10b981;">1,248</div>' +
            '<span style="' + subStyle + '">&#9650; +56 this session</span>' +
            '<div style="height: 4px; border-radius: 2px; margin-top: 14px; background: linear-gradient(90deg, rgba(16,185,129,0.2), #10b981);"></div>' +
        '</div>' +
        '<div style="' + cardStyle + '">' +
            '<div style="position: absolute; top: 0; left: 0; right: 0; height: 3px; background: #06b6d4; box-shadow: 0 0 15px #06b6d4;"></div>' +
            '<span style="' + labelStyle + '">Active Agents</span>' +
            '<div style="' + numStyle + 'color: #06b6d4;">12</div>' +
            '<span style="' + subStyle + '">Zero cross-tenant leakage</span>' +
            '<div style="height: 4px; border-radius: 2px; margin-top: 14px; background: linear-gradient(90deg, rgba(6,182,212,0.2), #06b6d4);"></div>' +
        '</div>' +
        '<div style="' + cardStyle + '">' +
            '<div style="position: absolute; top: 0; left: 0; right: 0; height: 3px; background: #8b5cf6; box-shadow: 0 0 15px #8b5cf6;"></div>' +
            '<span style="' + labelStyle + '">Memory Hit Rate</span>' +
            '<div style="' + numStyle + 'color: #8b5cf6;">93.5%</div>' +
            '<span style="' + subStyle + '">Hit@1 Precision</span>' +
            '<div style="height: 4px; border-radius: 2px; margin-top: 14px; background: linear-gradient(90deg, rgba(139,92,246,0.2), #8b5cf6);"></div>' +
        '</div>' +
        '<div style="' + cardStyle + '">' +
            '<div style="position: absolute; top: 0; left: 0; right: 0; height: 3px; background: #f59e0b; box-shadow: 0 0 15px #f59e0b;"></div>' +
            '<span style="' + labelStyle + '">Avg Latency</span>' +
            '<div style="' + numStyle + 'color: #f59e0b;">145ms</div>' +
            '<span style="' + subStyle + '">Full pipeline cycle</span>' +
            '<div style="height: 4px; border-radius: 2px; margin-top: 14px; background: linear-gradient(90deg, rgba(245,158,11,0.2), #f59e0b);"></div>' +
        '</div>' +
    '</div>' +
    '<div style="background: rgba(11, 13, 20, 0.85); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 18px; overflow: hidden; box-shadow: 0 16px 36px -8px rgba(0, 0, 0, 0.5);">' +
        '<div style="background: rgba(15, 17, 26, 0.95); padding: 14px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid rgba(255, 255, 255, 0.06);">' +
            '<div style="display: flex; gap: 7px; align-items: center;">' +
                '<span style="width: 11px; height: 11px; border-radius: 50%; background: #ff5f56; border: 1px solid #e0443e;"></span>' +
                '<span style="width: 11px; height: 11px; border-radius: 50%; background: #ffbd2e; border: 1px solid #dea123;"></span>' +
                '<span style="width: 11px; height: 11px; border-radius: 50%; background: #27c93f; border: 1px solid #1aab29;"></span>' +
            '</div>' +
            '<span style="font-family: JetBrains Mono, monospace; font-size: 0.8rem; color: #94a3b8; font-weight: 500;">Triple-Store Engine Status</span>' +
            '<span style="display: inline-flex; align-items: center; gap: 6px; padding: 3px 12px; border-radius: 9999px; font-size: 0.72rem; font-weight: 600; background: rgba(16, 185, 129, 0.12); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.25);">ALL HEALTHY</span>' +
        '</div>' +
        '<div style="padding: 20px; display: flex; flex-direction: column; gap: 10px;">' +
            '<div style="' + rowStyle + '">' +
                '<div style="display: flex; align-items: center; gap: 12px;"><span style="font-size: 1.4rem;">&#128451;</span><div><div style="font-weight: 600; font-size: 0.95rem;">SQLite WAL (Relational)</div><div style="font-size: 0.78rem; color: #64748b;">ACID + FTS5 Lexical Index</div></div></div>' +
                statusBadge +
            '</div>' +
            '<div style="' + rowStyle + '">' +
                '<div style="display: flex; align-items: center; gap: 12px;"><span style="font-size: 1.4rem;">&#9889;</span><div><div style="font-weight: 600; font-size: 0.95rem;">LanceDB (Dense Vectors)</div><div style="font-size: 0.78rem; color: #64748b;">Rust-powered cosine similarity</div></div></div>' +
                statusBadge +
            '</div>' +
            '<div style="' + rowStyle + '">' +
                '<div style="display: flex; align-items: center; gap: 12px;"><span style="font-size: 1.4rem;">&#128376;</span><div><div style="font-weight: 600; font-size: 0.95rem;">KuzuDB (Relational Graph)</div><div style="font-size: 0.78rem; color: #64748b;">Native C++ property graph</div></div></div>' +
                statusBadge +
            '</div>' +
        '</div>' +
    '</div>';
};
