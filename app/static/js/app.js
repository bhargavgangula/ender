/**
 * LeadScraper Pro — Frontend JavaScript
 */

let currentJobId = null;
let pollInterval = null;
let allResults = [];
let dbData = [];

// ═══════════════════════════════════════════════════════════════
// TAB SWITCHING
// ═══════════════════════════════════════════════════════════════

function switchTab(tab) {
    // Hide all tabs
    document.getElementById('tabDashboard').style.display = 'none';
    document.getElementById('tabScraper').style.display = 'none';
    document.getElementById('tabHistory').style.display = 'none';
    document.getElementById('tabDatabase').style.display = 'none';

    // Remove active from nav items
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));

    // Show selected tab and set active nav
    const titles = {
        dashboard: 'Dashboard',
        scraper: 'Scraper',
        history: 'Task History',
        database: 'Database Explorer'
    };

    document.getElementById('pageTitle').textContent = titles[tab] || 'Dashboard';

    const navItem = document.querySelector(`.nav-item[data-tab="${tab}"]`);
    if (navItem) navItem.classList.add('active');

    switch (tab) {
        case 'dashboard':
            document.getElementById('tabDashboard').style.display = 'block';
            loadDbStats();
            loadRecentTasks();
            break;
        case 'scraper':
            document.getElementById('tabScraper').style.display = 'block';
            break;
        case 'history':
            document.getElementById('tabHistory').style.display = 'block';
            loadTaskHistory();
            break;
        case 'database':
            document.getElementById('tabDatabase').style.display = 'block';
            loadIndustries();
            loadDatabaseData();
            break;
    }

    // Close sidebar on mobile
    document.getElementById('sidebar').classList.remove('open');
    const overlay = document.querySelector('.sidebar-overlay');
    if (overlay) overlay.classList.remove('active');
}

function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    sidebar.classList.toggle('open');
    let overlay = document.querySelector('.sidebar-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'sidebar-overlay';
        overlay.onclick = () => {
            sidebar.classList.remove('open');
            overlay.classList.remove('active');
        };
        document.body.appendChild(overlay);
    }
    overlay.classList.toggle('active');
}

// ═══════════════════════════════════════════════════════════════
// TOAST NOTIFICATIONS
// ═══════════════════════════════════════════════════════════════

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    const icons = { success: 'fa-check-circle', error: 'fa-exclamation-circle', info: 'fa-info-circle' };
    toast.innerHTML = `<i class="fas ${icons[type] || icons.info}"></i> ${escapeHtml(message)}`;

    container.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('show'));

    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 350);
    }, 3500);
}

// ═══════════════════════════════════════════════════════════════
// DASHBOARD — STATS & RECENT ACTIVITY
// ═══════════════════════════════════════════════════════════════

async function loadDbStats() {
    try {
        const response = await fetch('/api/stats');
        const data = await response.json();

        const totalBiz = data.total_businesses || 0;
        const totalEmails = data.total_emails || 0;
        const totalTasks = data.total_tasks || 0;

        animateNumber('dbTotalBiz', totalBiz);
        animateNumber('dbTotalEmails', totalEmails);
        animateNumber('dbTotalTasks', totalTasks);

        // Email rate
        const emailRate = totalBiz > 0 ? Math.round((totalEmails / totalBiz) * 100) : 0;
        document.getElementById('emailRate').textContent = emailRate + '%';
        document.getElementById('emailRateBar').style.width = emailRate + '%';

        // Load POS count from data endpoint
        try {
            const dataResp = await fetch('/api/data?limit=5000');
            const dataResult = await dataResp.json();
            const allData = dataResult.data || [];
            const posCount = allData.filter(r => r.has_pos === 'Yes').length;
            animateNumber('dbTotalPos', posCount);

            const posRate = totalBiz > 0 ? Math.round((posCount / totalBiz) * 100) : 0;
            document.getElementById('posRate').textContent = posRate + '%';
            document.getElementById('posRateBar').style.width = posRate + '%';
        } catch (e) {
            console.error('POS stat error:', e);
        }

        // Industries
        try {
            const indResp = await fetch('/api/industries');
            const indData = await indResp.json();
            const indCount = (indData.industries || []).length;
            document.getElementById('industryCount').textContent = indCount;
            document.getElementById('industryBar').style.width = Math.min(indCount * 10, 100) + '%';
        } catch (e) {
            console.error('Industry stat error:', e);
        }

    } catch (error) {
        console.error('Failed to load DB stats:', error);
    }
}

function animateNumber(elementId, target) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const current = parseInt(el.textContent) || 0;
    if (current === target) return;

    const duration = 600;
    const start = performance.now();

    function update(timestamp) {
        const elapsed = timestamp - start;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
        el.textContent = Math.round(current + (target - current) * eased);
        if (progress < 1) requestAnimationFrame(update);
    }

    requestAnimationFrame(update);
}

async function loadRecentTasks() {
    const container = document.getElementById('recentTasks');
    try {
        const response = await fetch('/api/tasks');
        const data = await response.json();
        const tasks = (data.tasks || []).slice(0, 8);

        if (tasks.length === 0) {
            container.innerHTML = `
                <div class="empty-placeholder">
                    <i class="fas fa-inbox"></i>
                    <p>No tasks yet. Start scraping to see activity here.</p>
                </div>`;
            return;
        }

        container.innerHTML = tasks.map(t => {
            const statusClass = t.status === 'Completed' ? 'completed' :
                               t.status === 'Running' ? 'running' : 'failed';
            const statusIcon = t.status === 'Completed' ? 'fa-check' :
                              t.status === 'Running' ? 'fa-spinner fa-spin' : 'fa-xmark';
            const badgeClass = t.status === 'Completed' ? 'badge-completed' :
                              t.status === 'Running' ? 'badge-running' : 'badge-failed';
            const created = t.created_at ? new Date(t.created_at).toLocaleDateString() : '';

            return `
                <div class="task-activity-item">
                    <div class="task-activity-icon ${statusClass}">
                        <i class="fas ${statusIcon}"></i>
                    </div>
                    <div class="task-activity-info">
                        <div class="task-name">${escapeHtml(t.search_term)}</div>
                        <div class="task-meta">${t.total_results || 0} results &middot; ${created}</div>
                    </div>
                    <span class="task-activity-badge ${badgeClass}">${t.status}</span>
                </div>`;
        }).join('');
    } catch (error) {
        console.error('Failed to load recent tasks:', error);
    }
}

// ═══════════════════════════════════════════════════════════════
// SCRAPER
// ═══════════════════════════════════════════════════════════════

async function startScraping() {
    const searchTerms = document.getElementById('searchTerms').value
        .split('\n').map(s => s.trim()).filter(s => s.length > 0);
    const zipCodes = document.getElementById('zipCodes').value
        .split('\n').map(s => s.trim()).filter(s => s.length > 0);

    if (searchTerms.length === 0) { showToast('Enter at least one search term', 'error'); return; }
    if (zipCodes.length === 0) { showToast('Enter at least one zip code', 'error'); return; }

    const maxResults = parseInt(document.getElementById('maxResults').value) || 20;
    const startBtn = document.getElementById('startBtn');

    startBtn.disabled = true;
    startBtn.innerHTML = '<span class="spinner"></span> Scraping...';

    try {
        const response = await fetch('/api/scrape', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ search_terms: searchTerms, zip_codes: zipCodes, max_results_per_search: maxResults }),
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Failed to start scraping');

        currentJobId = data.job_id;

        // If results returned inline (HTTP/sync mode), display immediately
        if (data.status === 'completed' && data.results) {
            allResults = data.results || [];
            updateLiveStats();
            renderResults(allResults);
            showToast(`Done! Found ${data.results_count} leads.`, 'success');
            startBtn.disabled = false;
            startBtn.innerHTML = '<i class="fas fa-rocket"></i> Start Scraping';
            document.getElementById('statRunning').textContent = 'Done';
        } else {
            // Async mode (Playwright) — poll for progress
            showToast('Scraping started!', 'success');
            document.getElementById('progressContainer').style.display = 'block';
            document.getElementById('progressBar').style.width = '0%';
            document.getElementById('liveIndicator').style.display = 'flex';
            startPolling();
        }
    } catch (error) {
        showToast(error.message, 'error');
        startBtn.disabled = false;
        startBtn.innerHTML = '<i class="fas fa-rocket"></i> Start Scraping';
    }
}

function startPolling() {
    if (pollInterval) clearInterval(pollInterval);

    pollInterval = setInterval(async () => {
        if (!currentJobId) return;

        try {
            const response = await fetch(`/api/job/${currentJobId}`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.error);

            const percent = data.total > 0 ? Math.round((data.completed / data.total) * 100) : 0;
            document.getElementById('progressBar').style.width = `${percent}%`;
            document.getElementById('progressText').textContent = `${data.completed} / ${data.total} searches`;
            document.getElementById('progressPercent').textContent = `${percent}%`;

            allResults = data.results || [];
            updateLiveStats();
            renderResults(allResults);

            document.getElementById('statRunning').textContent =
                data.status === 'running' ? 'Running' : data.status;

            if (data.status === 'completed' || data.status === 'failed') {
                clearInterval(pollInterval);
                pollInterval = null;

                const startBtn = document.getElementById('startBtn');
                startBtn.disabled = false;
                startBtn.innerHTML = '<i class="fas fa-rocket"></i> Start Scraping';
                document.getElementById('progressContainer').style.display = 'none';
                document.getElementById('liveIndicator').style.display = 'none';

                if (data.status === 'completed') {
                    showToast(`Done! Found ${data.results_count} leads.`, 'success');
                    document.getElementById('statRunning').textContent = 'Done';
                } else {
                    showToast('Scraping failed. Check logs.', 'error');
                    document.getElementById('statRunning').textContent = 'Failed';
                }

                loadDbStats();
            }
        } catch (error) {
            console.error('Poll error:', error);
        }
    }, 2000);
}

function updateLiveStats() {
    document.getElementById('statTotal').textContent = allResults.length;
    document.getElementById('statEmails').textContent = allResults.filter(r => r.final_email).length;
    document.getElementById('statPos').textContent = allResults.filter(r => r.has_pos === 'Yes').length;
}

function renderResults(results) {
    const emptyState = document.getElementById('emptyState');
    const tableContainer = document.getElementById('tableContainer');

    if (results.length === 0) {
        emptyState.style.display = 'block';
        tableContainer.style.display = 'none';
        return;
    }

    emptyState.style.display = 'none';
    tableContainer.style.display = 'block';

    const filter = document.getElementById('filterInput').value.toLowerCase();
    let filtered = results;
    if (filter) {
        filtered = results.filter(r =>
            (r.name || '').toLowerCase().includes(filter) ||
            (r.address || '').toLowerCase().includes(filter) ||
            (r.final_email || '').toLowerCase().includes(filter) ||
            (r.phone || '').toLowerCase().includes(filter) ||
            (r.city || '').toLowerCase().includes(filter)
        );
    }

    document.getElementById('resultsBody').innerHTML = filtered.map((r, i) => {
        const socialHtml = buildSocialIcons(r);
        const websiteUrl = r.website ? (r.website.startsWith('http') ? r.website : 'https://' + r.website) : '';

        return `<tr>
            <td>${i + 1}</td>
            <td title="${escapeHtml(r.name)}">${escapeHtml(r.name)}</td>
            <td title="${escapeHtml(r.address)}">${escapeHtml(r.address)}</td>
            <td>${escapeHtml(r.phone)}</td>
            <td class="cell-email" title="${escapeHtml(r.final_email)}">${escapeHtml(r.final_email) || '-'}</td>
            <td title="${escapeHtml(r.all_website_emails)}">${escapeHtml(r.all_website_emails) || '-'}</td>
            <td>${escapeHtml(r.email_source) || '-'}</td>
            <td>${websiteUrl ? `<a href="${escapeHtml(websiteUrl)}" target="_blank" class="cell-link">Visit</a>` : '-'}</td>
            <td>${socialHtml || '-'}</td>
            <td>${escapeHtml(r.rating) || '-'}</td>
            <td>${escapeHtml(r.reviews_count) || '-'}</td>
            <td class="${r.has_pos === 'Yes' ? 'cell-pos-yes' : 'cell-pos-no'}">${r.has_pos || '-'}</td>
            <td title="${escapeHtml(r.pos_system)}">${escapeHtml(r.pos_system) || '-'}</td>
            <td>${escapeHtml(r.delivery_services) || '-'}</td>
            <td>${escapeHtml(r.website_type) || '-'}</td>
            <td class="${(r.status || '') === 'Open' ? 'cell-open' : 'cell-closed'}">${escapeHtml(r.status) || '-'}</td>
            <td>${r.maps_url ? `<a href="${escapeHtml(r.maps_url)}" target="_blank" class="cell-link"><i class="fas fa-map-marker-alt"></i></a>` : '-'}</td>
        </tr>`;
    }).join('');
}

function buildSocialIcons(r) {
    let html = '<div class="social-icons">';
    let hasAny = false;
    if (r.facebook_link) { html += `<a href="${escapeHtml(r.facebook_link)}" target="_blank" class="fb" title="Facebook"><i class="fab fa-facebook-f"></i></a>`; hasAny = true; }
    if (r.instagram_link) { html += `<a href="${escapeHtml(r.instagram_link)}" target="_blank" class="ig" title="Instagram"><i class="fab fa-instagram"></i></a>`; hasAny = true; }
    if (r.twitter_link) { html += `<a href="${escapeHtml(r.twitter_link)}" target="_blank" class="tw" title="Twitter"><i class="fab fa-twitter"></i></a>`; hasAny = true; }
    if (r.linkedin_link) { html += `<a href="${escapeHtml(r.linkedin_link)}" target="_blank" class="li" title="LinkedIn"><i class="fab fa-linkedin-in"></i></a>`; hasAny = true; }
    html += '</div>';
    return hasAny ? html : '';
}

// ═══════════════════════════════════════════════════════════════
// TASK HISTORY
// ═══════════════════════════════════════════════════════════════

async function loadTaskHistory() {
    const tasksBody = document.getElementById('tasksBody');
    const tasksEmpty = document.getElementById('tasksEmpty');
    const wrapper = document.getElementById('tasksTableWrapper');

    try {
        const response = await fetch('/api/tasks');
        const data = await response.json();
        const tasks = data.tasks || [];

        if (tasks.length === 0) {
            tasksEmpty.style.display = 'block';
            wrapper.style.display = 'none';
            return;
        }

        tasksEmpty.style.display = 'none';
        wrapper.style.display = 'block';

        tasksBody.innerHTML = tasks.map(t => {
            const created = t.created_at ? new Date(t.created_at).toLocaleString() : '-';
            const statusClass = t.status === 'Completed' ? 'cell-open' :
                               t.status === 'Failed' ? 'cell-closed' : '';
            return `<tr>
                <td><code style="color:var(--primary-light);font-size:0.75rem;">${escapeHtml(t.job_id)}</code></td>
                <td title="${escapeHtml(t.search_term)}">${escapeHtml(t.search_term)}</td>
                <td title="${escapeHtml(t.zip_codes)}">${escapeHtml(t.zip_codes)}</td>
                <td class="${statusClass}">${escapeHtml(t.status)}</td>
                <td>${t.total_results || 0}</td>
                <td style="font-size:0.75rem;">${created}</td>
                <td>
                    <div class="task-actions">
                        <button class="btn btn-outline btn-xs" onclick="viewTaskResults('${t.job_id}')">
                            <i class="fas fa-eye"></i> View
                        </button>
                        <button class="btn btn-danger btn-xs" onclick="deleteTask('${t.job_id}')">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </td>
            </tr>`;
        }).join('');
    } catch (error) {
        console.error('Failed to load tasks:', error);
        showToast('Failed to load task history', 'error');
    }
}

async function viewTaskResults(jobId) {
    try {
        const response = await fetch(`/api/tasks/${jobId}/results`);
        const data = await response.json();
        const results = data.results || [];

        if (results.length === 0) {
            showToast('No results for this task.', 'info');
            return;
        }

        // Switch to database tab and show these results
        switchTab('database');
        dbData = results;
        renderDbTable(results);
        document.getElementById('dbResultCount').textContent = `${results.length} records (Task: ${jobId})`;
    } catch (error) {
        showToast('Failed to load results', 'error');
    }
}

async function deleteTask(jobId) {
    if (!confirm(`Delete task ${jobId} and ALL its data?`)) return;

    try {
        const response = await fetch(`/api/tasks/${jobId}`, { method: 'DELETE' });
        const data = await response.json();
        if (response.ok) {
            showToast('Task deleted.', 'success');
            loadTaskHistory();
            loadDbStats();
        } else {
            throw new Error(data.error || 'Delete failed');
        }
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function deleteAllData() {
    if (!confirm('DELETE ALL DATA? This removes ALL tasks and business data permanently.')) return;
    if (!confirm('Are you SURE? This CANNOT be undone.')) return;

    try {
        const response = await fetch('/api/data', { method: 'DELETE' });
        if (response.ok) {
            showToast('All data deleted.', 'success');
            loadTaskHistory();
            loadDbStats();
        } else {
            throw new Error('Delete failed');
        }
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// ═══════════════════════════════════════════════════════════════
// DATABASE EXPLORER
// ═══════════════════════════════════════════════════════════════

async function loadIndustries() {
    try {
        const response = await fetch('/api/industries');
        const data = await response.json();
        const industries = data.industries || [];

        const select = document.getElementById('industryFilter');
        while (select.options.length > 1) select.remove(1);

        industries.forEach(ind => {
            const option = document.createElement('option');
            option.value = ind;
            option.textContent = ind;
            select.appendChild(option);
        });
    } catch (error) {
        console.error('Failed to load industries:', error);
    }
}

async function loadDatabaseData() {
    const industry = document.getElementById('industryFilter').value;
    const dbEmpty = document.getElementById('dbEmpty');
    const dbTableContainer = document.getElementById('dbTableContainer');

    try {
        const response = await fetch(`/api/data?industry=${encodeURIComponent(industry)}`);
        const data = await response.json();
        dbData = data.data || [];

        document.getElementById('dbResultCount').textContent = `${dbData.length} records`;

        if (dbData.length === 0) {
            dbEmpty.style.display = 'block';
            dbTableContainer.style.display = 'none';
            return;
        }

        dbEmpty.style.display = 'none';
        dbTableContainer.style.display = 'block';
        renderDbTable(dbData);
    } catch (error) {
        console.error('Failed to load data:', error);
        showToast('Failed to load database data', 'error');
    }
}

function renderDbTable(data) {
    const dbBody = document.getElementById('dbBody');
    const dbEmpty = document.getElementById('dbEmpty');
    const dbTableContainer = document.getElementById('dbTableContainer');

    if (data.length === 0) {
        dbEmpty.style.display = 'block';
        dbTableContainer.style.display = 'none';
        return;
    }

    dbEmpty.style.display = 'none';
    dbTableContainer.style.display = 'block';

    dbBody.innerHTML = data.map((r, i) => {
        const websiteUrl = r.website ? (r.website.startsWith('http') ? r.website : 'https://' + r.website) : '';
        return `<tr>
            <td>${i + 1}</td>
            <td title="${escapeHtml(r.name)}">${escapeHtml(r.name)}</td>
            <td title="${escapeHtml(r.address)}">${escapeHtml(r.address)}</td>
            <td>${escapeHtml(r.phone)}</td>
            <td class="cell-email" title="${escapeHtml(r.final_email)}">${escapeHtml(r.final_email) || '-'}</td>
            <td>${websiteUrl ? `<a href="${escapeHtml(websiteUrl)}" target="_blank" class="cell-link">Visit</a>` : '-'}</td>
            <td>${r.facebook_link ? `<a href="${escapeHtml(r.facebook_link)}" target="_blank" class="cell-link"><i class="fab fa-facebook-f"></i></a>` : '-'}</td>
            <td>${r.instagram_link ? `<a href="${escapeHtml(r.instagram_link)}" target="_blank" class="cell-link"><i class="fab fa-instagram"></i></a>` : '-'}</td>
            <td>${escapeHtml(r.rating) || '-'}</td>
            <td class="${r.has_pos === 'Yes' ? 'cell-pos-yes' : 'cell-pos-no'}">${escapeHtml(r.pos_system) || (r.has_pos === 'Yes' ? 'Yes' : '-')}</td>
            <td>${escapeHtml(r.delivery_services) || '-'}</td>
            <td>${escapeHtml(r.website_type) || '-'}</td>
            <td>${escapeHtml(r.search_query) || '-'}</td>
        </tr>`;
    }).join('');
}

function filterDbTable() {
    const filter = document.getElementById('dbFilterInput').value.toLowerCase();
    if (!filter) {
        renderDbTable(dbData);
        document.getElementById('dbResultCount').textContent = `${dbData.length} records`;
        return;
    }

    const filtered = dbData.filter(r =>
        (r.name || '').toLowerCase().includes(filter) ||
        (r.address || '').toLowerCase().includes(filter) ||
        (r.final_email || '').toLowerCase().includes(filter) ||
        (r.phone || '').toLowerCase().includes(filter) ||
        (r.website || '').toLowerCase().includes(filter)
    );

    renderDbTable(filtered);
    document.getElementById('dbResultCount').textContent = `${filtered.length} of ${dbData.length} records`;
}

// ═══════════════════════════════════════════════════════════════
// EXPORT
// ═══════════════════════════════════════════════════════════════

async function exportResults(format) {
    if (!currentJobId || allResults.length === 0) {
        showToast('No results to export.', 'error');
        return;
    }

    try {
        const response = await fetch(`/api/export/${currentJobId}/${format}`);
        if (!response.ok) throw new Error('Export failed');
        downloadBlob(response, `leads_${currentJobId}.${format}`);
        showToast(`Exported ${allResults.length} leads as ${format.toUpperCase()}`, 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function exportDbCsv() {
    const industry = document.getElementById('industryFilter').value;
    await downloadDbExport('csv', industry);
}

async function exportDbJson() {
    const industry = document.getElementById('industryFilter').value;
    await downloadDbExport('json', industry);
}

async function downloadDbExport(format, industry) {
    try {
        const url = `/api/export-db/${format}?industry=${encodeURIComponent(industry)}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error('Export failed');
        downloadBlob(response, `leads_${industry || 'all'}.${format}`);
        showToast(`Exported as ${format.toUpperCase()}`, 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function downloadBlob(response, filename) {
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
}

// ═══════════════════════════════════════════════════════════════
// UTILITIES
// ═══════════════════════════════════════════════════════════════

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ═══════════════════════════════════════════════════════════════
// INITIALIZE
// ═══════════════════════════════════════════════════════════════

// Check DB connection status
async function checkDbConnection() {
    try {
        const response = await fetch('/api/stats');
        if (response.ok) {
            const dot = document.querySelector('.status-dot');
            if (dot) dot.classList.add('connected');
            const status = document.getElementById('dbStatus');
            if (status) status.querySelector('span').textContent = 'Supabase Connected';
        }
    } catch (e) {
        const dot = document.querySelector('.status-dot');
        if (dot) dot.classList.remove('connected');
        const status = document.getElementById('dbStatus');
        if (status) status.querySelector('span').textContent = 'DB Disconnected';
    }
}

// Boot
switchTab('dashboard');
checkDbConnection();
