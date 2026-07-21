// UI Components Namespace
window.UI = window.UI || {};

window.UI.Sidebar = {
    updateStatus: function(isConnected) {
        const indicator = document.querySelector('.sidebar-footer .status-indicator');
        if (indicator) {
            if (isConnected) {
                indicator.innerHTML = '<span class="dot green"></span> API Connected';
            } else {
                indicator.innerHTML = '<span class="dot red"></span> API Offline';
            }
        }
    }
};
