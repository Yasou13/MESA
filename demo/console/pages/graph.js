window.initGraphPage = function(container) {
    container.innerHTML =
    '<div class="page-header">' +
        '<h2 class="page-title">Knowledge Graph</h2>' +
        '<p style="color: #94a3b8; margin-top: 8px; font-size: 0.95rem;">Explore KuzuDB entities and relationships.</p>' +
    '</div>' +
    '<div style="display: flex; gap: 24px; flex: 1; min-height: 0;">' +
        '<div style="flex: 1; background: rgba(11, 13, 20, 0.85); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 18px; overflow: hidden; box-shadow: 0 16px 36px -8px rgba(0, 0, 0, 0.5); display: flex; flex-direction: column;">' +
            '<div style="background: rgba(15, 17, 26, 0.95); padding: 14px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid rgba(255, 255, 255, 0.06);">' +
                '<div style="display: flex; gap: 7px; align-items: center;">' +
                    '<span style="width: 11px; height: 11px; border-radius: 50%; background: #ff5f56; border: 1px solid #e0443e;"></span>' +
                    '<span style="width: 11px; height: 11px; border-radius: 50%; background: #ffbd2e; border: 1px solid #dea123;"></span>' +
                    '<span style="width: 11px; height: 11px; border-radius: 50%; background: #27c93f; border: 1px solid #1aab29;"></span>' +
                '</div>' +
                '<span style="font-family: JetBrains Mono, monospace; font-size: 0.8rem; color: #94a3b8; font-weight: 500;">KuzuDB Graph Explorer</span>' +
                '<span style="display: inline-flex; align-items: center; gap: 6px; padding: 3px 12px; border-radius: 9999px; font-size: 0.72rem; font-weight: 600; background: rgba(139, 92, 246, 0.12); color: #c4b5fd; border: 1px solid rgba(139, 92, 246, 0.25);">LIVE</span>' +
            '</div>' +
            '<div id="graphCanvas" style="flex: 1; display: flex; align-items: center; justify-content: center; position: relative; padding: 20px;"><p style="color: #64748b;">Graph visualization loading...</p></div>' +
        '</div>' +
        '<div style="width: 240px; display: flex; flex-direction: column; gap: 16px;">' +
            '<div class="card"><h3 style="font-size: 0.85rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 14px;">Filters</h3>' +
                '<div style="display: flex; flex-direction: column; gap: 14px;">' +
                    '<div><label style="font-size: 0.78rem; color: #64748b; font-weight: 600; text-transform: uppercase; display: block; margin-bottom: 6px;">Node Type</label><select class="input"><option>All</option><option>Person</option><option>Technology</option></select></div>' +
                    '<div><label style="font-size: 0.78rem; color: #64748b; font-weight: 600; text-transform: uppercase; display: block; margin-bottom: 6px;">Depth</label><select class="input"><option>2 hops</option><option>3 hops</option><option>5 hops</option></select></div>' +
                '</div>' +
            '</div>' +
            '<div class="card"><h3 style="font-size: 0.85rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 14px;">Legend</h3>' +
                '<div style="display: flex; flex-direction: column; gap: 10px; font-size: 0.85rem;">' +
                    '<div style="display: flex; align-items: center; gap: 8px;"><span style="width: 12px; height: 12px; border-radius: 50%; background: #8b5cf6;"></span> Person</div>' +
                    '<div style="display: flex; align-items: center; gap: 8px;"><span style="width: 12px; height: 12px; border-radius: 50%; background: #3b82f6;"></span> Technology</div>' +
                    '<div style="display: flex; align-items: center; gap: 8px;"><span style="width: 12px; height: 12px; border-radius: 50%; background: #10b981;"></span> Location</div>' +
                '</div>' +
            '</div>' +
        '</div>' +
    '</div>';

    fetch('../console/data/mock-graph.json')
        .then(function(res) { return res.json(); })
        .then(function(data) {
            var canvas = document.getElementById('graphCanvas');
            canvas.innerHTML =
            '<div style="position: absolute; top: 16px; left: 20px; font-family: JetBrains Mono, monospace; font-size: 0.78rem; color: #64748b;">Nodes: ' + data.nodes.length + ' | Edges: ' + data.links.length + '</div>' +
            '<div style="text-align: center;">' +
                '<svg width="450" height="320">' +
                    '<line x1="225" y1="160" x2="110" y2="80" stroke="rgba(139,92,246,0.4)" stroke-width="2" stroke-dasharray="6 4"/>' +
                    '<line x1="225" y1="160" x2="340" y2="80" stroke="rgba(6,182,212,0.4)" stroke-width="2" stroke-dasharray="6 4"/>' +
                    '<line x1="225" y1="160" x2="225" y2="270" stroke="rgba(16,185,129,0.4)" stroke-width="2" stroke-dasharray="6 4"/>' +
                    '<circle cx="225" cy="160" r="42" fill="none" stroke="rgba(139,92,246,0.15)" stroke-width="1.5"/>' +
                    '<circle cx="225" cy="160" r="56" fill="none" stroke="rgba(139,92,246,0.06)" stroke-width="1"/>' +
                    '<circle cx="225" cy="160" r="32" fill="#8b5cf6"/>' +
                    '<text x="225" y="165" text-anchor="middle" fill="white" font-size="13" font-weight="600">Ahmet</text>' +
                    '<circle cx="110" cy="80" r="26" fill="#3b82f6"/>' +
                    '<text x="110" y="85" text-anchor="middle" fill="white" font-size="11">PostgreSQL</text>' +
                    '<circle cx="340" cy="80" r="26" fill="#10b981"/>' +
                    '<text x="340" y="85" text-anchor="middle" fill="white" font-size="11">Istanbul</text>' +
                    '<circle cx="225" cy="270" r="26" fill="#3b82f6"/>' +
                    '<text x="225" y="275" text-anchor="middle" fill="white" font-size="11">Enterprise</text>' +
                    '<text x="155" y="112" fill="#94a3b8" font-size="9">PREFERS</text>' +
                    '<text x="290" y="112" fill="#94a3b8" font-size="9">LIVES_IN</text>' +
                    '<text x="240" y="220" fill="#94a3b8" font-size="9">INTERESTED</text>' +
                '</svg>' +
                '<p style="margin-top: 12px; color: #64748b; font-size: 0.85rem;">Click on a node to inspect</p>' +
                '<button class="btn btn-secondary" onclick="inspectNode()" style="margin-top: 12px; font-size: 0.85rem; padding: 8px 18px;">Inspect Node</button>' +
            '</div>';
        });

    window.inspectNode = function() {
        window.UI.Inspector.open("Node Details", ["Properties", "Connections"], {
            "Properties": "<ul><li><strong>ID:</strong> user_ahmet</li><li><strong>Type:</strong> person</li></ul>",
            "Connections": "<ul><li>LIVES_IN &rarr; Istanbul</li><li>PREFERS &rarr; PostgreSQL</li></ul>"
        });
    };
};
