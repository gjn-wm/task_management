/**
 * Phase 2: Sprints, notifications, developer skills, enhanced reporting
 */

const SKILL_OPTIONS = ['Backend', 'Frontend', 'AI', 'Embedded', 'DevOps', 'Database', 'Testing', 'API', 'CCTV', 'Dashboard'];

let sprintsList = [];
let selectedSprintId = null;
let notificationPollTimer = null;
let notificationsLoading = false;

function parseUserSkills(user) {
    if (user.skill_list && user.skill_list.length) return user.skill_list;
    if (!user.skills) return [];
    return user.skills.split(',').map(s => s.trim()).filter(Boolean);
}

function skillsToString(skillArr) {
    return (skillArr || []).join(', ');
}

function getSelectedSkills(containerId) {
    const checked = [];
    document.querySelectorAll(`#${containerId} input[type="checkbox"]:checked`).forEach(cb => {
        checked.push(cb.value);
    });
    return checked;
}

function renderSkillCheckboxes(containerId, selected = []) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = '';
    SKILL_OPTIONS.forEach(skill => {
        const div = document.createElement('label');
        div.className = 'skill-checkbox-item';
        div.innerHTML = `
            <input type="checkbox" value="${esc(skill)}" ${selected.includes(skill) ? 'checked' : ''}>
            <span>${esc(skill)}</span>
        `;
        el.appendChild(div);
    });
}

function renderUserSkillTags(skills) {
    if (!skills || !skills.length) return '<span style="color:var(--text-muted);font-size:12px;">No skills</span>';
    return skills.map(s => `<span class="skill-tag">${esc(s)}</span>`).join('');
}

async function refreshNotificationCount() {
    if (!currentUser || currentUser.role === 'Viewer') return;
    try {
        const data = await apiFetch('/api/notifications/unread-count');
        const badge = document.getElementById('notification-badge');
        if (badge && data) {
            badge.textContent = data.count;
            badge.style.display = data.count > 0 ? 'flex' : 'none';
        }
    } catch (err) {
        console.error(err);
    }
}

async function toggleNotificationDropdown() {
    const dropdown = document.getElementById('notification-dropdown');
    if (!dropdown) return;
    if (dropdown.classList.contains('open')) {
        dropdown.classList.remove('open');
        return;
    }
    await loadNotificationsList();
    dropdown.classList.add('open');
}

async function loadNotificationsList() {
    const list = document.getElementById('notification-list');
    if (!list || notificationsLoading) return;
    notificationsLoading = true;
    try {
        const notes = await apiFetch('/api/notifications') || [];
        list.innerHTML = '';
        if (!notes.length) {
            list.innerHTML = '<p style="padding:16px;color:var(--text-muted);font-style:italic;">No alerts right now.</p>';
            return;
        }
        notes.forEach(n => {
            const item = document.createElement('div');
            item.className = `notification-item ${esc(n.type)} ${n.is_read ? '' : 'unread'}`;
            item.innerHTML = `
                <div class="notification-item-title">${esc(n.title)}</div>
                <div class="notification-item-msg">${esc(n.message)}</div>
            `;
            item.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (item.dataset.busy === '1') return;
                item.dataset.busy = '1';
                try {
                    if (!n.is_read) {
                        await apiFetch(`/api/notifications/${n.id}/read`, { method: 'PATCH' });
                        n.is_read = true;
                        item.classList.remove('unread');
                    }
                    if (n.task_id) openTaskDetails(n.task_id);
                    document.getElementById('notification-dropdown')?.classList.remove('open');
                    refreshNotificationCount();
                } finally {
                    item.dataset.busy = '0';
                }
            });
            list.appendChild(item);
        });
    } catch (err) {
        console.error(err);
    } finally {
        notificationsLoading = false;
    }
}

async function markAllNotificationsRead(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    try {
        await apiFetch('/api/notifications/mark-all-read', { method: 'POST' });
        document.querySelectorAll('.notification-item.unread').forEach(el => el.classList.remove('unread'));
        refreshNotificationCount();
    } catch (err) {
        console.error(err);
    }
}

async function syncDueNotifications() {
    if (!currentUser || currentUser.role === 'Viewer') return;
    try {
        await apiFetch('/api/notifications');
        await refreshNotificationCount();
    } catch (err) {
        console.error(err);
    }
}

function startNotificationPolling() {
    if (notificationPollTimer) clearInterval(notificationPollTimer);
    syncDueNotifications();
    notificationPollTimer = setInterval(refreshNotificationCount, 60000);
}

async function loadSprints() {
    try {
        sprintsList = await apiFetch('/api/sprints') || [];
        populateSprintDropdowns();
        if (activeView === 'sprints') renderSprintsView();
    } catch (err) {
        console.error(err);
    }
}

function populateSprintDropdowns() {
    ['editor-sprint', 'bulk-sprint-select'].forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        const cur = el.value;
        const isBulk = id === 'bulk-sprint-select';
        el.innerHTML = isBulk ? '<option value="">Add to sprint...</option>' : '<option value="">No sprint</option>';
        sprintsList.forEach(s => {
            el.innerHTML += `<option value="${s.id}">${esc(s.name)} (${esc(s.status)})</option>`;
        });
        if (cur) el.value = cur;
    });
}

async function loadSprintsPanel() {
    await loadSprints();
    renderSprintsView();
}

function renderSprintsView() {
    const grid = document.getElementById('sprints-grid');
    const empty = document.getElementById('sprints-empty');
    if (!grid) return;

    if (!sprintsList.length) {
        grid.innerHTML = '';
        if (empty) empty.style.display = 'block';
        const panel = document.getElementById('sprint-detail-panel');
        if (panel) panel.style.display = 'none';
        return;
    }
    if (empty) empty.style.display = 'none';

    grid.innerHTML = '';
    sprintsList.forEach(sprint => {
        const pct = sprint.task_count ? Math.round((sprint.completed_count / sprint.task_count) * 100) : 0;
        const card = document.createElement('div');
        card.className = `sprint-card ${selectedSprintId === sprint.id ? 'active' : ''}`;
        card.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                <h4>${esc(sprint.name)}</h4>
                <span class="project-status-pill project-status-${esc(sprint.status.toLowerCase())}">${esc(sprint.status)}</span>
            </div>
            <p style="font-size:12px;color:var(--text-secondary);margin-top:6px;">${esc(sprint.goal || 'No sprint goal set.')}</p>
            <div class="sprint-meta">
                <span>${esc(sprint.start_date)} → ${esc(sprint.end_date)}</span>
                <span>${sprint.completed_count}/${sprint.task_count} done</span>
            </div>
            <div class="sprint-progress-bar"><div class="sprint-progress-fill" style="width:${pct}%"></div></div>
        `;
        card.addEventListener('click', () => selectSprint(sprint.id));
        if (typeof canManageProjects === 'function' && canManageProjects()) {
            card.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                openSprintEditor(sprint.id);
            });
        }
        grid.appendChild(card);
    });

    if (!selectedSprintId && sprintsList.length) {
        const active = sprintsList.find(s => s.status === 'Active') || sprintsList[0];
        selectSprint(active.id);
    } else if (selectedSprintId) {
        loadSprintBurndown(selectedSprintId);
    }
}

async function selectSprint(sprintId) {
    selectedSprintId = sprintId;
    renderSprintsView();
    await loadSprintBurndown(sprintId);
}

async function loadSprintBurndown(sprintId) {
    const panel = document.getElementById('sprint-detail-panel');
    if (!panel) return;
    panel.style.display = 'block';

    const sprint = sprintsList.find(s => s.id === sprintId);
    if (!sprint) return;

    document.getElementById('sprint-detail-title').textContent = sprint.name;
    document.getElementById('sprint-detail-dates').textContent = `${sprint.start_date} → ${sprint.end_date}`;
    document.getElementById('sprint-detail-goal').textContent = sprint.goal || 'No goal set.';
    document.getElementById('sprint-detail-stats').textContent =
        `${sprint.completed_count} of ${sprint.task_count} tasks completed`;

    try {
        const data = await apiFetch(`/api/sprints/${sprintId}/burndown`);
        if (!data) return;

        document.getElementById('sprint-velocity-val').textContent = `${data.velocity_per_week} tasks/week`;

        const theme = document.documentElement.getAttribute('data-theme');
        const textColor = theme === 'dark' ? '#f1f5f9' : '#0f172a';
        const gridColor = theme === 'dark' ? '#20283c' : '#cbd5e1';

        if (window.sprintBurndownChart) window.sprintBurndownChart.destroy();
        const ctx = document.getElementById('chart-sprint-burndown').getContext('2d');
        window.sprintBurndownChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.dates,
                datasets: [
                    { label: 'Actual remaining', data: data.actual_remaining, borderColor: '#14b8a6', backgroundColor: 'rgba(20,184,166,0.1)', fill: true, tension: 0.3 },
                    { label: 'Ideal burndown', data: data.ideal_remaining, borderColor: '#94a3b8', borderDash: [6, 4], fill: false, tension: 0 }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { ticks: { color: textColor, maxRotation: 45 }, grid: { color: gridColor } },
                    y: { ticks: { color: textColor, stepSize: 1 }, grid: { color: gridColor } }
                },
                plugins: { legend: { labels: { color: textColor } } }
            }
        });
    } catch (err) {
        console.error(err);
    }
}

function openSprintEditor(sprintId = null) {
    document.getElementById('sprint-editor-form').reset();
    document.getElementById('sprint-editor-id').value = sprintId || '';
    document.getElementById('sprint-editor-title').textContent = sprintId ? 'Edit Sprint' : 'New Sprint';

    const projectSelect = document.getElementById('sprint-project-select');
    if (projectSelect && typeof projectsList !== 'undefined') {
        projectSelect.innerHTML = '<option value="">All projects</option>';
        projectsList.forEach(p => { projectSelect.innerHTML += `<option value="${p.id}">${esc(p.name)}</option>`; });
    }

    if (sprintId) {
        const sprint = sprintsList.find(s => s.id === sprintId);
        if (sprint) {
            document.getElementById('sprint-name-input').value = sprint.name;
            document.getElementById('sprint-start-input').value = sprint.start_date;
            document.getElementById('sprint-end-input').value = sprint.end_date;
            document.getElementById('sprint-goal-input').value = sprint.goal || '';
            document.getElementById('sprint-status-input').value = sprint.status;
            if (projectSelect) projectSelect.value = sprint.project_id || '';
        }
    }
    openModal('sprint-editor-modal');
}

async function handleSaveSprint(e) {
    e.preventDefault();
    const id = document.getElementById('sprint-editor-id').value;
    const payload = {
        name: document.getElementById('sprint-name-input').value.trim(),
        start_date: document.getElementById('sprint-start-input').value,
        end_date: document.getElementById('sprint-end-input').value,
        goal: document.getElementById('sprint-goal-input').value.trim(),
        status: document.getElementById('sprint-status-input').value,
        project_id: parseInt(document.getElementById('sprint-project-select').value) || null
    };

    const url = id ? `/api/sprints/${id}` : '/api/sprints';
    try {
        const data = await apiFetch(url, { method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        if (data) {
            closeModal('sprint-editor-modal');
            showToast(id ? 'Sprint updated' : 'Sprint created', 'success');
            await loadSprints();
            if (activeView === 'sprints') renderSprintsView();
        }
    } catch (err) {
        console.error(err);
    }
}

function filterBulkAssigneesBySkill() {
    const skillFilter = document.getElementById('bulk-skill-filter')?.value;
    const select = document.getElementById('bulk-assignee-select');
    if (!select || typeof teamMembers === 'undefined') return;

    const cur = select.value;
    select.innerHTML = '<option value="">Assign to...</option><option value="">Unassign</option>';
    teamMembers.forEach(u => {
        if (u.role === 'Viewer') return;
        const skills = parseUserSkills(u);
        if (skillFilter && !skills.map(s => s.toLowerCase()).includes(skillFilter.toLowerCase())) return;
        const name = u.full_name || u.username;
        const skillHint = skills.length ? ` (${skills.slice(0, 2).join(', ')})` : '';
        select.innerHTML += `<option value="${u.id}">${esc(name + skillHint)}</option>`;
    });
    if (cur) select.value = cur;
}

function renderDeveloperCompletionChart(stats) {
    const canvas = document.getElementById('chart-dev-completion');
    if (!canvas || !stats.developer_completion) return;

    const theme = document.documentElement.getAttribute('data-theme');
    const textColor = theme === 'dark' ? '#f1f5f9' : '#0f172a';
    const gridColor = theme === 'dark' ? '#20283c' : '#cbd5e1';
    const labels = Object.keys(stats.developer_completion);
    const rates = labels.map(l => stats.developer_completion[l].rate);
    const assigned = labels.map(l => stats.developer_completion[l].assigned);

    if (window.devCompletionChart) window.devCompletionChart.destroy();
    window.devCompletionChart = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: { labels, datasets: [{ label: 'Completion rate %', data: rates, backgroundColor: '#0ea5e9', borderColor: '#0284c7', borderWidth: 1 }] },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { max: 100, ticks: { color: textColor }, grid: { color: gridColor } },
                y: { ticks: { color: textColor }, grid: { display: false } }
            },
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { afterLabel(ctx) { return `${assigned[ctx.dataIndex]} tasks assigned`; } } }
            }
        }
    });
}

function patchPhase2Dashboard() {
    const origLoad = loadDashboardData;
    loadDashboardData = async function() {
        await origLoad();
        try {
            const stats = await apiFetch('/api/analytics');
            if (!stats) return;
            const weekEl = document.getElementById('kpi-week');
            if (weekEl) weekEl.textContent = stats.summary.completed_this_week ?? 0;
            renderDeveloperCompletionChart(stats);
            const velEl = document.getElementById('sprint-velocity-kpi');
            const velName = document.getElementById('sprint-velocity-name');
            if (velEl && stats.sprint_velocity) {
                velEl.textContent = stats.sprint_velocity.velocity_per_week;
                if (velName) velName.textContent = stats.sprint_velocity.sprint_name;
            } else if (velEl) {
                velEl.textContent = '—';
                if (velName) velName.textContent = 'No active sprint';
            }
        } catch (err) {
            console.error(err);
        }
    };
}

function openEditUserSkills(userId) {
    const user = teamMembers.find(u => u.id === userId);
    if (!user) return;
    document.getElementById('edit-skills-user-id').value = userId;
    document.getElementById('edit-skills-username').textContent = user.full_name || user.username;
    renderSkillCheckboxes('edit-user-skills', parseUserSkills(user));
    openModal('edit-skills-modal');
}

async function handleSaveUserSkills(e) {
    e.preventDefault();
    const userId = document.getElementById('edit-skills-user-id').value;
    const skills = skillsToString(getSelectedSkills('edit-user-skills'));
    try {
        await apiFetch(`/api/users/${userId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ skills }) });
        closeModal('edit-skills-modal');
        showToast('Skills updated', 'success');
        teamMembers = await apiFetch('/api/users');
        loadUsersPanel();
        populateAssigneeDropdowns();
    } catch (err) {
        console.error(err);
    }
}

function initPhase2Features() {
    document.querySelectorAll('.sprint-manage-only').forEach(el => {
        el.style.display = (typeof canManageProjects === 'function' && canManageProjects()) ? '' : 'none';
    });

    renderSkillCheckboxes('new-user-skills', []);
    patchPhase2Dashboard();

    const origLoadWorkspace = loadWorkspaceData;
    loadWorkspaceData = async function() {
        await origLoadWorkspace();
        await loadSprints();
        startNotificationPolling();
    };

    const origSwitchView = switchView;
    switchView = function(viewName, updateUrl) {
        origSwitchView(viewName, updateUrl);
        if (viewName === 'sprints') loadSprintsPanel();
    };

    const origPopulateAssignee = populateAssigneeDropdowns;
    populateAssigneeDropdowns = function() {
        origPopulateAssignee();
        filterBulkAssigneesBySkill();
    };

    const origHandleAddUser = handleAddUser;
    handleAddUser = async function(e) {
        e.preventDefault();
        const payload = {
            username: document.getElementById('new-username').value.trim(),
            full_name: document.getElementById('new-fullname').value.trim(),
            password: document.getElementById('new-password').value,
            role: document.getElementById('new-role').value,
            skills: typeof skillsToString === 'function' ? skillsToString(getSelectedSkills('new-user-skills')) : ''
        };
        try {
            const data = await apiFetch('/api/users', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            if (data) {
                document.getElementById('add-user-form').reset();
                if (typeof renderSkillCheckboxes === 'function') renderSkillCheckboxes('new-user-skills', []);
                showToast('User registered successfully', 'success');
                loadUsersPanel();
                teamMembers = await apiFetch('/api/users');
                populateAssigneeDropdowns();
            }
        } catch (err) {
            console.error(err);
        }
    };

    const origLoadUsersPanel = loadUsersPanel;
    loadUsersPanel = async function() {
        try {
            const users = await apiFetch('/api/users');
            if (!users) return;
            const tbody = document.getElementById('users-table-body');
            tbody.innerHTML = '';
            users.forEach(u => {
                const row = document.createElement('tr');
                const isSelf = u.id === currentUser.id;
                const skills = typeof parseUserSkills === 'function' ? parseUserSkills(u) : [];
                const deleteBtnHTML = isSelf
                    ? '<span style="font-size:12px;color:var(--text-muted);font-style:italic;">Active Account</span>'
                    : `<button class="secondary-btn logout-btn" onclick="deleteUser(${u.id}, ${jsArg(u.username)})" style="padding:6px 12px;font-size:12px;"><i data-lucide="user-x" style="width:12px;height:12px;"></i> Delete</button>`;
                const editSkillsBtn = currentUser.role === 'Admin'
                    ? `<button class="secondary-btn" style="padding:4px 8px;font-size:11px;margin-left:6px;" onclick="openEditUserSkills(${u.id})">Skills</button>`
                    : '';
                row.innerHTML = `
                    <td style="font-weight:600;">${esc(u.full_name || 'N/A')}</td>
                    <td>${esc(u.username)}</td>
                    <td><span class="tag" style="background-color:${u.role === 'Admin' ? 'var(--error)' : u.role === 'Manager' ? 'var(--warning)' : u.role === 'Developer' ? 'var(--accent-color)' : 'var(--text-muted)'}">${esc(u.role)}</span></td>
                    <td><div class="skill-tags">${typeof renderUserSkillTags === 'function' ? renderUserSkillTags(skills) : ''}</div></td>
                    <td>${deleteBtnHTML}${editSkillsBtn}</td>
                `;
                tbody.appendChild(row);
            });
            lucide.createIcons();
        } catch (err) {
            console.error(err);
        }
    };

    document.addEventListener('click', (e) => {
        const wrap = document.getElementById('notification-bell-wrap');
        if (wrap && !wrap.contains(e.target)) {
            document.getElementById('notification-dropdown')?.classList.remove('open');
        }
    });
}

document.addEventListener('DOMContentLoaded', () => {
    setTimeout(initPhase2Features, 50);
});
