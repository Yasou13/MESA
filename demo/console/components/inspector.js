// UI Components Namespace
window.UI = window.UI || {};

window.UI.Inspector = {
    open: function(title, tabs, contentMap) {
        if (window.ConsoleApp && window.ConsoleApp.openInspector) {
            window.ConsoleApp.openInspector(title, tabs, contentMap);
        }
    },
    close: function() {
        if (window.ConsoleApp && window.ConsoleApp.closeInspector) {
            window.ConsoleApp.closeInspector();
        }
    }
};
