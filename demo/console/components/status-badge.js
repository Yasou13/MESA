// UI Components Namespace
window.UI = window.UI || {};

window.UI.StatusBadge = {
    create: function(status) {
        status = (status || 'unknown').toLowerCase();
        let colorClass = 'badge-info';
        
        if (status === 'success' || status === 'healthy' || status === 'selected') {
            colorClass = 'badge-success';
        } else if (status === 'warning' || status === 'degraded') {
            colorClass = 'badge-warning';
        } else if (status === 'error' || status === 'offline' || status === 'rejected') {
            colorClass = 'badge-danger';
        }
        
        return `<span class="badge ${colorClass}">${status}</span>`;
    }
};
