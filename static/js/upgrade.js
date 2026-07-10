/**
 * Phase 1 upgrade: projects, bulk table ops, subtasks, Manager role UI
 */

let projectsList = [];
let allTasksViewMode = 'cards';
let selectedTaskIds = new Set();

function canBulkOperate() {
    return currentUser && ['Admin', 'Manager'].includes(currentUser.role);
}

function canManageProjects() {
    return currentUser && ['Admin', 'Manager'].includes(currentUser.role);
}

async function loadProjects() {
    try {
        projectsList = await apiFetch('/api/projects') || [];
        populateProjectDropdowns();
        if (activeView === 'projects') renderProjectsView();
    } catch (err) {
        console.error('Failed to load projects', err);
    }
}

function populateProjectDropdowns() {
    const dropdownIds = [
        'filter-project',
        'all-tasks-filter-project',
        'editor-project',
        'bulk-project-select'
    ];

    dropdownIds.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        const current = el.value;
        const isBulk = id === 'bulk-project-select';
        const isEditor = id === 'editor-project';
        el.innerHTML = isBulk || isEditor
            ? `<option value="">${isBulk ? 'Move to project...' : 'No project'}</option>`
            : '<option value="">All Projects</option>';
        projectsList.forEach(p => {
            el.innerHTML += `<option value="${p.id}">${p.name}</option>`;
        });
        if (current) el.value = current;
    });

    const leadSelect = document.getElementById('project-lead-select');
    if (leadSelect && typeof teamMembers !== 'undefined') {
        const cur = leadSelect.value;
        leadSelect.innerHTML = '<option value="">No lead</option>';
        teamMembers.forEach(u => {
            const name = u.full_name || u.username;
            leadSelect.innerHTML += `<option value="${u.id}">${name}</option>`;
        });
        if (cur) leadSelect.value = cur;
    }

    const bulkAssignee = document.getElementById('bulk-assignee-select');
    if (bulkAssignee && typeof teamMembers !== 'undefined') {
        const cur = bulkAssignee.value;
        bulkAssignee.innerHTML = '<option value="">Assign to...</option><option value="">Unassign</option>';
        teamMembers.forEach(u => {
            if (u.role === 'Viewer') return;
            const name = u.full_name || u.username;
            bulkAssignee.innerHTML += `<option value="${u.id}">${name}</option>`;
        });
        if (cur) bulkAssignee.value = cur;
    }
}

function projectStatusClass(status) {
    const key = (status || 'Active').toLowerCase().replace(/\s+/g, '-');
    return `project-status-pill project-status-${key}`;
}

async function loadProjectsPanel() {
    await loadProjects();
    renderProjectsView();
}

function renderProjectsView() {
    const grid = document.getElementById('projects-grid');
    const empty = document.getElementById('projects-empty');
    if (!grid) return;

    if (!projectsList.length) {
        grid.innerHTML = '';
        if (empty) empty.style.display = 'block';
        return;
    }
    if (empty) empty.style.display = 'none';

    grid.innerHTML = '';
    projectsList.forEach(project => {
        const card = document.createElement('div');
        card.className = 'project-card';
        const leadName = project.lead ? (project.lead.full_name || project.lead.username) : 'Unassigned';
        card.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px;">
                <h4>${project.name}</h4>
                <span class="${projectStatusClass(project.status)}">${project.status}</span>
            </div>
            <p style="font-size:13px; color:var(--text-secondary); margin-top:6px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;">
                ${project.description || 'No description.'}
            </p>
            <div class="project-card-meta">
                <span><i data-lucide="user" style="width:12px;height:12px;display:inline;vertical-align:middle;"></i> ${leadName}</span>
                <span><i data-lucide="list-todo" style="width:12px;height:12px;display:inline;vertical-align:middle;"></i> ${project.task_count || 0} tasks</span>
            </div>
        `;
        card.addEventListener('click', () => filterBoardByProject(project.id));
        if (canManageProjects()) {
            card.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                openProjectEditor(project.id);
            });
        }
        grid.appendChild(card);
    });
    lucide.createIcons({ props: { width: 12, height: 12 } });
}

function filterBoardByProject(projectId) {
    switchView('kanban');
    setTimeout(() => {
        const filter = document.getElementById('filter-project');
        if (filter) {
            filter.value = projectId;
            loadTasks();
        }
    }, 100);
}

function openProjectEditor(projectId = null) {
    const form = document.getElementById('project-editor-form');
    form.reset();
    document.getElementById('project-editor-id').value = projectId || '';
    document.getElementById('project-editor-title').textContent = projectId ? 'Edit Project' : 'New Project';

    if (projectId) {
        const project = projectsList.find(p => p.id === projectId);
        if (project) {
            document.getElementById('project-name-input').value = project.name;
            document.getElementById('project-desc-input').value = project.description || '';
            document.getElementById('project-status-input').value = project.status;
            document.getElementById('project-lead-select').value = project.lead_id || '';
        }
    }
    populateProjectDropdowns();
    openModal('project-editor-modal');
}

async function handleSaveProject(e) {
    e.preventDefault();
    const id = document.getElementById('project-editor-id').value;
    const payload = {
        name: document.getElementById('project-name-input').value.trim(),
        description: document.getElementById('project-desc-input').value.trim(),
        status: document.getElementById('project-status-input').value,
        lead_id: parseInt(document.getElementById('project-lead-select').value) || null
    };

    const url = id ? `/api/projects/${id}` : '/api/projects';
    const method = id ? 'PUT' : 'POST';

    try {
        const data = await apiFetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (data) {
            closeModal('project-editor-modal');
            showToast(id ? 'Project updated' : 'Project created', 'success');
            await loadProjects();
            if (activeView === 'projects') renderProjectsView();
        }
    } catch (err) {
        console.error(err);
    }
}

function setAllTasksViewMode(mode) {
    allTasksViewMode = mode;
    document.getElementById('all-tasks-cards-btn')?.classList.toggle('active', mode === 'cards');
    document.getElementById('all-tasks-table-btn')?.classList.toggle('active', mode === 'table');
    document.getElementById('all-tasks-grid').style.display = mode === 'cards' ? 'grid' : 'none';
    document.getElementById('all-tasks-table-wrap').style.display = mode === 'table' ? 'block' : 'none';
    if (mode === 'table') renderAllTasksTable();
    else renderAllTasks();
}

function toggleTaskSelection(taskId, checked) {
    if (checked) selectedTaskIds.add(taskId);
    else selectedTaskIds.delete(taskId);
    updateBulkActionBar();
}

function toggleSelectAllTasks(checked) {
    selectedTaskIds.clear();
    if (checked) {
        allTasksList.forEach(t => selectedTaskIds.add(t.id));
    }
    document.querySelectorAll('.task-row-checkbox').forEach(cb => { cb.checked = checked; });
    document.querySelectorAll('#all-tasks-table-body tr').forEach(tr => {
        tr.classList.toggle('selected', checked);
    });
    updateBulkActionBar();
}

function updateBulkActionBar() {
    const bar = document.getElementById('bulk-action-bar');
    const countEl = document.getElementById('bulk-selected-count');
    if (!bar) return;
    const n = selectedTaskIds.size;
    bar.classList.toggle('active', n > 0 && canBulkOperate());
    if (countEl) countEl.textContent = `${n} selected`;
}

function renderAllTasksTable() {
    const tbody = document.getElementById('all-tasks-table-body');
    if (!tbody) return;

    if (!allTasksList || !allTasksList.length) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--text-muted);font-style:italic;">No tasks found.</td></tr>`;
        document.getElementById('all-tasks-empty').style.display = 'block';
        return;
    }
    document.getElementById('all-tasks-empty').style.display = 'none';

    const startIndex = (allTasksCurrentPage - 1) * allTasksPageSize;
    const pageItems = allTasksList.slice(startIndex, startIndex + allTasksPageSize);

    tbody.innerHTML = '';
    pageItems.forEach(task => {
        const tr = document.createElement('tr');
        if (selectedTaskIds.has(task.id)) tr.classList.add('selected');
        const priClass = `pri-${task.priority.toLowerCase()}`;
        const assigneeName = task.assignee ? (task.assignee.full_name || task.assignee.username) : 'Unassigned';
        const projectName = task.project ? task.project.name : '—';
        const deadlineHTML = getDeadlineProgressHTML(task.due_date, task.status);
        const checkCell = canBulkOperate()
            ? `<input type="checkbox" class="task-row-checkbox" ${selectedTaskIds.has(task.id) ? 'checked' : ''} onchange="toggleTaskSelection(${task.id}, this.checked); this.closest('tr').classList.toggle('selected', this.checked)">`
            : '';

        tr.innerHTML = `
            <td>${checkCell}</td>
            <td class="task-title-cell" onclick="openTaskDetails(${task.id})">${task.title}</td>
            <td><span class="project-badge-inline">${projectName}</span></td>
            <td><span class="tag cat-tag">${task.category}</span></td>
            <td><span class="tag pri-tag ${priClass}">${task.priority}</span></td>
            <td>${task.status}</td>
            <td>${assigneeName}</td>
            <td>${deadlineHTML}</td>
            <td>
                <button class="secondary-btn" style="padding:4px 8px;font-size:11px;" onclick="openTaskDetails(${task.id})">Open</button>
            </td>
        `;
        tbody.appendChild(tr);
    });

    const totalPages = Math.ceil(allTasksList.length / allTasksPageSize);
    const paginationContainer = document.getElementById('pagination-container');
    if (totalPages > 1) {
        paginationContainer.style.display = 'flex';
        document.getElementById('pagination-info').textContent = `Page ${allTasksCurrentPage} of ${totalPages} (${allTasksList.length} tasks)`;
        document.getElementById('prev-page-btn').disabled = allTasksCurrentPage === 1;
        document.getElementById('next-page-btn').disabled = allTasksCurrentPage === totalPages;
    } else {
        paginationContainer.style.display = 'none';
    }

    lucide.createIcons({ props: { width: 12, height: 12 } });
}

async function executeBulkAction(action) {
    if (!canBulkOperate() || !selectedTaskIds.size) return;

    const payload = {
        task_ids: Array.from(selectedTaskIds),
        action
    };

    if (action === 'assign') {
        payload.assignee_id = parseInt(document.getElementById('bulk-assignee-select').value) || null;
    } else if (action === 'status') {
        payload.status = document.getElementById('bulk-status-select').value;
        if (!payload.status) return showToast('Select a status', 'warning');
    } else if (action === 'priority') {
        payload.priority = document.getElementById('bulk-priority-select').value;
        if (!payload.priority) return showToast('Select a priority', 'warning');
    } else if (action === 'project') {
        const pid = document.getElementById('bulk-project-select').value;
        payload.project_id = pid ? parseInt(pid) : null;
    } else if (action === 'sprint') {
        const sid = document.getElementById('bulk-sprint-select')?.value;
        payload.sprint_id = sid ? parseInt(sid) : null;
    } else if (action === 'delete') {
        if (!confirm(`Delete ${selectedTaskIds.size} tasks permanently?`)) return;
    }

    try {
        const data = await apiFetch('/api/tasks/bulk', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (data) {
            showToast(`Updated ${data.updated} task(s)`, 'success');
            selectedTaskIds.clear();
            updateBulkActionBar();
            loadAllTasks();
            if (activeView === 'kanban') loadTasks();
        }
    } catch (err) {
        console.error(err);
    }
}

function renderSubtasks(subtasks) {
    const container = document.getElementById('detail-subtasks-list');
    const countBtn = document.getElementById('tab-subtasks-btn');
    const progressEl = document.getElementById('subtask-progress-text');
    if (!container) return;

    const items = subtasks || [];
    const done = items.filter(s => s.is_completed).length;
    if (countBtn) countBtn.textContent = `Checklist (${items.length})`;
    if (progressEl) progressEl.textContent = items.length ? `${done} of ${items.length} completed` : 'No checklist items yet';

    container.innerHTML = '';
    if (!items.length) {
        container.innerHTML = '<p style="color:var(--text-muted);font-style:italic;">No checklist items yet.</p>';
        return;
    }

    items.forEach(st => {
        const row = document.createElement('div');
        row.className = `subtask-item ${st.is_completed ? 'done' : ''}`;
        const canEdit = currentUser.role !== 'Viewer';
        row.innerHTML = `
            <input type="checkbox" ${st.is_completed ? 'checked' : ''} ${canEdit ? '' : 'disabled'}
                onchange="toggleSubtaskComplete(${st.id}, this.checked)">
            <span class="subtask-title">${st.title}</span>
            ${canEdit ? `<button class="subtask-delete" onclick="deleteSubtask(${st.id})" title="Remove"><i data-lucide="x"></i></button>` : ''}
        `;
        container.appendChild(row);
    });
    lucide.createIcons({ props: { width: 12, height: 12 } });
}

async function handleAddSubtask(e) {
    e.preventDefault();
    const input = document.getElementById('new-subtask-input');
    const title = input.value.trim();
    if (!title || !currentDetailTaskId) return;

    try {
        await apiFetch(`/api/tasks/${currentDetailTaskId}/subtasks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title })
        });
        input.value = '';
        const task = await apiFetch(`/api/tasks/${currentDetailTaskId}`);
        renderSubtasks(task.subtasks);
        showToast('Checklist item added', 'success');
    } catch (err) {
        console.error(err);
    }
}

async function toggleSubtaskComplete(subtaskId, isCompleted) {
    try {
        await apiFetch(`/api/subtasks/${subtaskId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_completed: isCompleted })
        });
        const task = await apiFetch(`/api/tasks/${currentDetailTaskId}`);
        renderSubtasks(task.subtasks);
    } catch (err) {
        console.error(err);
    }
}

async function deleteSubtask(subtaskId) {
    try {
        await apiFetch(`/api/subtasks/${subtaskId}`, { method: 'DELETE' });
        const task = await apiFetch(`/api/tasks/${currentDetailTaskId}`);
        renderSubtasks(task.subtasks);
        showToast('Item removed', 'success');
    } catch (err) {
        console.error(err);
    }
}

function patchUpgradePermissions() {
    document.querySelectorAll('.manager-or-admin').forEach(el => {
        el.style.display = canManageProjects() ? 'flex' : 'none';
    });
    document.querySelectorAll('.bulk-only').forEach(el => {
        el.style.display = canBulkOperate() ? '' : 'none';
    });
    const deleteBtn = document.getElementById('delete-task-action-btn');
    if (deleteBtn) {
        deleteBtn.style.display = canBulkOperate() ? 'flex' : 'none';
    }
}

function initUpgradeFeatures() {
    patchUpgradePermissions();

    const origLoadWorkspace = loadWorkspaceData;
    loadWorkspaceData = async function() {
        await origLoadWorkspace();
        await loadProjects();
    };

    const origEnforce = enforcePermissions;
    enforcePermissions = function() {
        origEnforce();
        patchUpgradePermissions();
    };

    const origSwitchView = switchView;
    switchView = function(viewName, updateUrl) {
        origSwitchView(viewName, updateUrl);
        if (viewName === 'projects') loadProjectsPanel();
        if (viewName === 'all-tasks') {
            setAllTasksViewMode(allTasksViewMode);
        }
    };

    const origLoadTasks = loadTasks;
    loadTasks = async function() {
        const projectId = document.getElementById('filter-project')?.value;
        const search = document.getElementById('filter-search').value;
        const priority = document.getElementById('filter-priority').value;
        const category = document.getElementById('filter-category').value;
        const assigneeId = document.getElementById('filter-assignee').value;

        let queryParams = [];
        if (search) queryParams.push(`search=${encodeURIComponent(search)}`);
        if (priority) queryParams.push(`priority=${priority}`);
        if (category) queryParams.push(`category=${category}`);
        if (assigneeId) queryParams.push(`assignee_id=${assigneeId}`);
        if (projectId) queryParams.push(`project_id=${projectId}`);

        const url = '/api/tasks' + (queryParams.length ? `?${queryParams.join('&')}` : '');
        try {
            tasksList = await apiFetch(url);
            if (tasksList) {
                if (currentUser.role === 'Viewer') {
                    document.getElementById('kanban-board-container').style.display = 'none';
                    document.getElementById('viewer-tasks-list-container').style.display = 'block';
                    renderViewerTaskList();
                } else {
                    document.getElementById('kanban-board-container').style.display = 'flex';
                    document.getElementById('viewer-tasks-list-container').style.display = 'none';
                    renderKanbanBoard();
                }
            }
        } catch (err) {
            console.error(err);
        }
    };

    const origLoadAllTasks = loadAllTasks;
    loadAllTasks = async function(page = 1) {
        const search = document.getElementById('all-tasks-filter-search').value;
        const priority = document.getElementById('all-tasks-filter-priority').value;
        const status = document.getElementById('all-tasks-filter-status').value;
        const category = document.getElementById('all-tasks-filter-category').value;
        const projectId = document.getElementById('all-tasks-filter-project')?.value;

        let queryParams = [];
        if (search) queryParams.push(`search=${encodeURIComponent(search)}`);
        if (priority) queryParams.push(`priority=${priority}`);
        if (category) queryParams.push(`category=${category}`);
        if (status) queryParams.push(`status=${status}`);
        if (projectId) queryParams.push(`project_id=${projectId}`);

        const url = '/api/tasks' + (queryParams.length ? `?${queryParams.join('&')}` : '');
        try {
            allTasksList = await apiFetch(url);
            allTasksCurrentPage = 1;
            selectedTaskIds.clear();
            updateBulkActionBar();
            if (allTasksViewMode === 'table') renderAllTasksTable();
            else renderAllTasks();
        } catch (err) {
            console.error(err);
            showToast('Error loading tasks', 'error');
        }
    };

    const origRenderAllTasks = renderAllTasks;
    renderAllTasks = function() {
        origRenderAllTasks();
        if (allTasksViewMode === 'table') renderAllTasksTable();
    };

    const origOpenTaskDetails = openTaskDetails;
    openTaskDetails = async function(taskId) {
        await origOpenTaskDetails(taskId);
        try {
            const task = await apiFetch(`/api/tasks/${taskId}`);
            renderSubtasks(task.subtasks);
            const projectEl = document.getElementById('detail-project-val');
            if (projectEl) {
                projectEl.textContent = task.project ? task.project.name : 'No project';
            }
        } catch (err) {
            console.error(err);
        }
    };

    const origSwitchDetailTab = switchDetailTab;
    switchDetailTab = function(tab) {
        if (tab === 'subtasks') {
            document.querySelectorAll('#task-detail-modal .tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('#task-detail-modal .tab-content').forEach(c => c.classList.remove('active'));
            document.getElementById('tab-subtasks-btn').classList.add('active');
            document.getElementById('tab-subtasks').classList.add('active');
            return;
        }
        origSwitchDetailTab(tab);
    };

    const origOpenTaskEditor = openTaskEditor;
    openTaskEditor = async function(taskId = null) {
        await origOpenTaskEditor(taskId);
        populateProjectDropdowns();
        if (typeof populateSprintDropdowns === 'function') populateSprintDropdowns();
        if (taskId) {
            try {
                const task = await apiFetch(`/api/tasks/${taskId}`);
                const projEl = document.getElementById('editor-project');
                if (projEl) projEl.value = task.project_id || '';
                const sprintEl = document.getElementById('editor-sprint');
                if (sprintEl) sprintEl.value = task.sprint_id || '';
            } catch (err) {
                console.error(err);
            }
        }
    };

    const origHandleSaveTask = handleSaveTask;
    handleSaveTask = async function(e) {
        e.preventDefault();
        const taskId = document.getElementById('editor-task-id').value;

        const checkedLabels = [];
        document.querySelectorAll('#editor-labels-checkboxes input[type="checkbox"]:checked').forEach(cb => {
            checkedLabels.push(parseInt(cb.value));
        });

        const projectVal = document.getElementById('editor-project')?.value;
        const sprintVal = document.getElementById('editor-sprint')?.value;

        const payload = {
            title: document.getElementById('editor-title').value.trim(),
            description: document.getElementById('editor-desc').value.trim(),
            priority: document.getElementById('editor-priority').value,
            category: document.getElementById('editor-category').value,
            status: document.getElementById('editor-status').value,
            due_date: document.getElementById('editor-due').value || null,
            estimated_hours: parseFloat(document.getElementById('editor-est').value) || 0.0,
            actual_hours: parseFloat(document.getElementById('editor-act').value) || 0.0,
            assignee_id: parseInt(document.getElementById('editor-assignee').value) || null,
            project_id: projectVal ? parseInt(projectVal) : null,
            sprint_id: sprintVal ? parseInt(sprintVal) : null,
            label_ids: checkedLabels
        };

        const url = taskId ? `/api/tasks/${taskId}` : '/api/tasks';
        const method = taskId ? 'PUT' : 'POST';

        try {
            const data = await apiFetch(url, {
                method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (data) {
                closeModal('task-editor-modal');
                showToast(taskId ? 'Task details updated' : 'Task created successfully', 'success');
                if (activeView === 'kanban') loadTasks();
                else if (activeView === 'all-tasks') loadAllTasks();
                else if (activeView === 'calendar') renderCalendar();
            }
        } catch (err) {
            console.error(err);
        }
    };
}

document.addEventListener('DOMContentLoaded', () => {
    setTimeout(initUpgradeFeatures, 0);
});
