// Core Console Application Logic
// This script MUST be loaded AFTER all component and page scripts

(function() {
    const navItems = document.querySelectorAll('.nav-item');
    const mainContent = document.getElementById('mainContent');
    const inspector = document.getElementById('inspector');
    const closeInspectorBtn = document.getElementById('closeInspectorBtn');

    // Navigation and Routing
    function navigateTo(pageId) {
        // Update nav UI
        navItems.forEach(item => {
            if (item.dataset.page === pageId) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        });

        // Hide all pages
        const pages = document.querySelectorAll('.page');
        pages.forEach(page => page.classList.remove('active'));

        // Show target page
        let targetPage = document.getElementById(`page-${pageId}`);
        if (!targetPage) {
            // Render basic template if page doesn't exist yet
            targetPage = document.createElement('div');
            targetPage.id = `page-${pageId}`;
            targetPage.className = 'page active';
            targetPage.innerHTML = `
                <div class="page-header">
                    <h2 class="page-title">${pageId.charAt(0).toUpperCase() + pageId.slice(1)}</h2>
                </div>
                <div class="page-body">
                    <p>Content for ${pageId} will be loaded here.</p>
                </div>
            `;
            mainContent.appendChild(targetPage);

            // Trigger specific page initialization if exists
            const initFnName = `init${pageId.charAt(0).toUpperCase() + pageId.slice(1)}Page`;
            if (typeof window[initFnName] === 'function') {
                window[initFnName](targetPage);
            }
        } else {
            targetPage.classList.add('active');
        }

        // Close inspector on navigation
        closeInspector();
    }

    // Event Listeners for Nav
    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const pageId = item.dataset.page;
            navigateTo(pageId);
        });
    });

    // Inspector Logic
    function openInspector(title, tabs, contentMap) {
        inspector.classList.remove('hidden');
        document.getElementById('inspectorTitle').textContent = title;

        const tabsContainer = document.getElementById('inspectorTabs');
        const bodyContainer = document.getElementById('inspectorBody');

        tabsContainer.innerHTML = '';

        tabs.forEach((tab, index) => {
            const tabEl = document.createElement('div');
            tabEl.className = `inspector-tab ${index === 0 ? 'active' : ''}`;
            tabEl.textContent = tab;
            tabEl.onclick = () => {
                document.querySelectorAll('.inspector-tab').forEach(t => t.classList.remove('active'));
                tabEl.classList.add('active');
                bodyContainer.innerHTML = contentMap[tab] || '';
            };
            tabsContainer.appendChild(tabEl);
        });

        // Load first tab content
        if (tabs.length > 0) {
            bodyContainer.innerHTML = contentMap[tabs[0]] || '';
        }
    }

    function closeInspector() {
        inspector.classList.add('hidden');
    }

    if (closeInspectorBtn) {
        closeInspectorBtn.addEventListener('click', closeInspector);
    }

    // Expose globals for pages
    window.ConsoleApp = {
        navigateTo,
        openInspector,
        closeInspector
    };

    // Initial Navigation — all page init functions are guaranteed to be loaded
    navigateTo('playground');
})();
