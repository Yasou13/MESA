// UI Components Namespace
window.UI = window.UI || {};

window.UI.Table = {
    create: function(columns, data, onRowClick) {
        let html = '<div class="table-wrapper"><table class="data-table"><thead><tr>';
        columns.forEach(col => {
            html += `<th>${col.label}</th>`;
        });
        html += '</tr></thead><tbody>';
        
        data.forEach((row, rowIndex) => {
            html += `<tr data-index="${rowIndex}" style="cursor: ${onRowClick ? 'pointer' : 'default'}">`;
            columns.forEach(col => {
                html += `<td>${row[col.key] || '-'}</td>`;
            });
            html += '</tr>';
        });
        
        html += '</tbody></table></div>';
        
        const container = document.createElement('div');
        container.innerHTML = html;
        
        if (onRowClick) {
            const rows = container.querySelectorAll('tbody tr');
            rows.forEach(tr => {
                tr.addEventListener('click', () => {
                    const index = parseInt(tr.getAttribute('data-index'));
                    onRowClick(data[index]);
                });
            });
        }
        
        return container.firstElementChild;
    }
};
