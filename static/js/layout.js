/**
 * UI shell + responsive Kanban behavior (Phases 1–2)
 */

const KANBAN_STATUSES = ['Backlog', 'Todo', 'In Progress', 'Review', 'Testing', 'Done'];
let activeMobileKanbanStatus = 'Todo';
let moveMenuTaskId = null;

function isMobileKanban() {
    return window.matchMedia('(max-width: 768px)').matches;
}

function isTouchDevice() {
    return window.matchMedia('(hover: none)').matches || 'ontouchstart' in window;
}

function toggleSidebarCollapse() {
    document.body.classList.toggle('sidebar-collapsed');
    const collapsed = document.body.classList.contains('sidebar-collapsed');
    localStorage.setItem('task_sidebar_collapsed', collapsed ? '1' : '0');
    const btn = document.querySelector('.sidebar-collapse-btn i');
    if (btn) btn.setAttribute('data-lucide', collapsed ? 'panel-left-open' : 'panel-left-close');
    if (typeof lucide !== 'undefined') lucide.createIcons({ props: { width: 16, height: 16 } });
    updateBoardScrollShadows();
}

function toggleMobileNav(forceClose) {
    const aside = document.querySelector('aside');
    const backdrop = document.getElementById('sidebar-backdrop');
    if (!aside) return;
    const open = forceClose === true ? false : !aside.classList.contains('mobile-open');
    aside.classList.toggle('mobile-open', open);
    backdrop?.classList.toggle('active', open);
    document.body.style.overflow = open ? 'hidden' : '';
}

function toggleFilterBar() {
    const wrap = document.getElementById('kanban-filter-wrap');
    if (!wrap) return;
    wrap.classList.toggle('collapsed');
    const collapsed = wrap.classList.contains('collapsed');
    localStorage.setItem('task_kanban_filters_collapsed', collapsed ? '1' : '0');
}

function updateBoardOffset() {
    const header = document.querySelector('header');
    const toolbar = document.querySelector('.kanban-toolbar');
    let offset = 180;
    if (header) offset += header.offsetHeight;
    if (toolbar) offset += toolbar.offsetHeight;
    document.documentElement.style.setProperty('--header-offset', `${offset}px`);
}

function updateBoardScrollShadows() {
    const board = document.getElementById('kanban-board-container');
    const wrapper = document.getElementById('board-scroll-wrapper');
    if (!board || !wrapper || isMobileKanban()) return;

    const canLeft = board.scrollLeft > 4;
    const canRight = board.scrollLeft < board.scrollWidth - board.clientWidth - 4;
    wrapper.classList.toggle('can-scroll-left', canLeft);
    wrapper.classList.toggle('can-scroll-right', canRight);
}

function renderKanbanStatusTabs() {
    const tabsEl = document.getElementById('kanban-status-tabs');
    if (!tabsEl) return;

    tabsEl.innerHTML = '';
    KANBAN_STATUSES.forEach(status => {
        const count = (typeof tasksList !== 'undefined' && tasksList)
            ? tasksList.filter(t => t.status === status).length
            : 0;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `kanban-status-tab ${status === activeMobileKanbanStatus ? 'active' : ''}`;
        btn.innerHTML = `${status} <span class="tab-count">${count}</span>`;
        btn.addEventListener('click', () => {
            activeMobileKanbanStatus = status;
            applyMobileKanbanColumn();
            renderKanbanStatusTabs();
        });
        tabsEl.appendChild(btn);
    });
}

function applyMobileKanbanColumn() {
    document.querySelectorAll('.board-column').forEach(col => {
        const status = col.getAttribute('data-status');
        if (!isMobileKanban()) {
            col.classList.remove('mobile-active');
            col.style.display = '';
        } else {
            col.classList.toggle('mobile-active', status === activeMobileKanbanStatus);
        }
    });
}

function openMoveTaskMenu(taskId) {
    moveMenuTaskId = taskId;
    const menu = document.getElementById('move-task-menu');
    const list = document.getElementById('move-task-options');
    if (!menu || !list) return;

    list.innerHTML = '';
    KANBAN_STATUSES.forEach(status => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'move-task-option';
        btn.textContent = status;
        btn.addEventListener('click', () => moveTaskToStatus(taskId, status));
        list.appendChild(btn);
    });
    menu.classList.add('active');
}

function closeMoveTaskMenu() {
    moveMenuTaskId = null;
    document.getElementById('move-task-menu')?.classList.remove('active');
}

async function moveTaskToStatus(taskId, status) {
    closeMoveTaskMenu();
    try {
        await apiFetch(`/api/tasks/${taskId}/status`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status })
        });
        showToast(`Moved task to ${status}`, 'success');
        if (typeof loadTasks === 'function') loadTasks();
    } catch (err) {
        console.error(err);
    }
}

function patchKanbanRendering() {
    if (typeof renderKanbanBoard !== 'function') return;

    const origRender = renderKanbanBoard;
    renderKanbanBoard = function() {
        origRender();
        applyMobileKanbanColumn();
        renderKanbanStatusTabs();
        updateBoardOffset();
        updateBoardScrollShadows();

        if (isTouchDevice()) {
            document.querySelectorAll('.task-card').forEach(card => {
                const taskId = card.getAttribute('data-id');
                // Double-click does not exist on touch screens: a tap opens the task.
                if (!card.dataset.tapBound) {
                    card.dataset.tapBound = '1';
                    card.addEventListener('click', () => openTaskDetails(parseInt(taskId, 10)));
                }
                if (!card.querySelector('.card-move-btn')) {
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'card-move-btn';
                    btn.title = 'Move task';
                    btn.innerHTML = '<i data-lucide="arrow-right-left"></i>';
                    btn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        openMoveTaskMenu(parseInt(taskId, 10));
                    });
                    card.appendChild(btn);
                }
                if (isMobileKanban()) {
                    card.setAttribute('draggable', 'false');
                }
            });
            if (typeof lucide !== 'undefined') lucide.createIcons({ props: { width: 14, height: 14 } });
        }
    };
}

function initLayoutUI() {
    if (localStorage.getItem('task_sidebar_collapsed') === '1' && !isMobileKanban()) {
        document.body.classList.add('sidebar-collapsed');
        const btn = document.querySelector('.sidebar-collapse-btn i');
        if (btn) btn.setAttribute('data-lucide', 'panel-left-open');
        if (typeof lucide !== 'undefined') lucide.createIcons({ props: { width: 16, height: 16 } });
    }

    const filterWrap = document.getElementById('kanban-filter-wrap');
    if (filterWrap && localStorage.getItem('task_kanban_filters_collapsed') === '1' && isMobileKanban()) {
        filterWrap.classList.add('collapsed');
    }

    document.querySelector('aside nav')?.addEventListener('click', () => {
        if (isMobileKanban()) toggleMobileNav(true);
    });

    const board = document.getElementById('kanban-board-container');
    board?.addEventListener('scroll', updateBoardScrollShadows, { passive: true });

    window.addEventListener('resize', () => {
        updateBoardOffset();
        applyMobileKanbanColumn();
        renderKanbanStatusTabs();
        updateBoardScrollShadows();
        if (!isMobileKanban()) toggleMobileNav(true);
    });

    patchKanbanRendering();
    updateBoardOffset();
    applyMobileKanbanColumn();
    renderKanbanStatusTabs();
    updateBoardScrollShadows();

    const origSwitchView = typeof switchView === 'function' ? switchView : null;
    if (origSwitchView) {
        switchView = function(viewName, updateUrl) {
            origSwitchView(viewName, updateUrl);
            if (viewName === 'kanban') {
                setTimeout(() => {
                    updateBoardOffset();
                    applyMobileKanbanColumn();
                    renderKanbanStatusTabs();
                    updateBoardScrollShadows();
                }, 50);
            }
        };
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initLayoutUI);
} else {
    initLayoutUI();
}
