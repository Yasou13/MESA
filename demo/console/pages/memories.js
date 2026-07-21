window.initMemoriesPage = function(container) {
    container.innerHTML =
    '<div class="page-header">' +
        '<h2 class="page-title">Memory Explorer</h2>' +
        '<div style="margin-top: 16px; display: flex; gap: 12px;">' +
            '<input type="text" class="input" placeholder="Search memories (Semantic &amp; Keyword)..." style="max-width: 400px;">' +
            '<button class="btn btn-secondary">Filter</button>' +
        '</div>' +
    '</div>' +
    '<div id="memoriesTableContainer"><p style="color: #64748b;">Loading memories...</p></div>';

    fetch('../console/data/mock-memories.json')
        .then(function(res) { return res.json(); })
        .then(function(data) {
            var el = document.getElementById('memoriesTableContainer');
            el.innerHTML = '';
            var cols = [
                { key: 'id', label: 'ID' },
                { key: 'type', label: 'Type' },
                { key: 'summary', label: 'Summary' },
                { key: 'confidence', label: 'Confidence' }
            ];
            var table = window.UI.Table.create(cols, data, function(row) {
                window.UI.Inspector.open(
                    "Memory Details",
                    ["Full Content", "Metadata", "Entities"],
                    {
                        "Full Content": "<p>" + row.content + "</p>",
                        "Metadata": "<ul><li><strong>ID:</strong> " + row.id + "</li><li><strong>Type:</strong> " + row.type + "</li><li><strong>Importance:</strong> " + row.importance + "</li><li><strong>Confidence:</strong> " + row.confidence + "</li><li><strong>Created:</strong> " + row.created_at + "</li></ul>",
                        "Entities": "<ul>" + row.entities.map(function(e) { return "<li>" + e.name + " (" + e.type + ")</li>"; }).join('') + "</ul>"
                    }
                );
            });
            el.appendChild(table);
        })
        .catch(function() {
            document.getElementById('memoriesTableContainer').innerHTML = '<p style="color: #ef4444;">Failed to load memories.</p>';
        });
};
