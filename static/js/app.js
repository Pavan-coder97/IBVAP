/* ═══════════════════════════════════════════════════════════════════════
   IBVAP — app.js  (Main frontend logic)
   Handles: SPA routing, multi-camera grid, SSE events, alerts log,
            analytics charts, FRS registration, upload progress
   ═══════════════════════════════════════════════════════════════════════ */

'use strict';

// ── State ────────────────────────────────────────────────────────────────
const state = {
    cameras:    [],    // [{cam_id, name, running, fps, ...}]
    activeGrid: '1x1',
    drawingZone: false,
    zoneCamId:  'CAM-01',
    frsRole:    'authorized',
    frsMethod:  'upload',
    capturedBlob: null,
    frsStream:  null,
    analyticsCharts: {},
    uploadJobId: null,
};

// ── SPA Routing ──────────────────────────────────────────────────────────
document.querySelectorAll('.tab-link').forEach(link => {
    link.addEventListener('click', e => {
        e.preventDefault();
        document.querySelectorAll('.tab-pane').forEach(p => p.classList.add('d-none'));
        document.querySelectorAll('.tab-link').forEach(l => l.classList.remove('active'));
        const id = link.getAttribute('href').substring(1);
        document.getElementById(id)?.classList.remove('d-none');
        link.classList.add('active');

        // Trigger tab-specific refresh
        if (id === 'dashboard') refreshDashboard();
        if (id === 'alerts')    loadAlerts();
        if (id === 'analytics') loadAnalytics();
        if (id === 'frs')       loadProfiles();
    });
});

// ══════════════════════════════════════════════════════════════════════════
// Dashboard
// ══════════════════════════════════════════════════════════════════════════

async function refreshDashboard() {
    const stats = await fetchJSON('/api/stats');
    if (!stats) return;

    setText('stat-vehicles',  stats.total_vehicles       || 0);
    setText('stat-persons',   stats.persons_detected     || 0);
    setText('stat-intrusions',stats.intrusion_alerts     || 0);
    setText('stat-suspects',  stats.suspect_alerts       || 0);
    setText('stat-plates',    stats.plates_read          || 0);
    setText('stat-night',     stats.night_alerts         || 0);

    renderCamStatusList();
    loadTimelineChart();
}

function renderCamStatusList() {
    const el = document.getElementById('cam-status-list');
    if (!state.cameras.length) {
        el.innerHTML = `
            <div class="empty-state mt-2">
                <i class="fa-solid fa-video-slash"></i>
                <div>No cameras configured</div>
            </div>`;
        return;
    }
    el.innerHTML = state.cameras.map(c => `
        <div class="cam-status-item">
            <span class="${c.running ? 'cam-online' : 'cam-offline'}">
                <i class="fa-solid fa-circle blink me-1" style="font-size:0.5rem;"></i>${c.cam_id} — ${c.name}
            </span>
            <span class="${c.running ? 'cam-online' : 'cam-offline'}">
                ${c.running ? c.fps + ' fps' : 'OFFLINE'}
            </span>
        </div>
    `).join('');
}

async function loadTimelineChart() {
    const data = await fetchJSON('/api/analytics/timeline?hours=24');
    if (!data) return;

    const hours  = Array.from({length: 24}, (_, i) => String(i).padStart(2, '0'));
    const types  = [...new Set(data.map(d => d.alert_type))];
    const colours = ['#ff2244','#8800ff','#00aaff','#ff8c00','#ffcc00','#00ff88'];

    const datasets = types.map((t, i) => ({
        label: t,
        data: hours.map(h => {
            const row = data.find(d => d.hour === h && d.alert_type === t);
            return row ? row.count : 0;
        }),
        borderColor: colours[i % colours.length],
        backgroundColor: colours[i % colours.length] + '33',
        fill: true,
        tension: 0.4,
        pointRadius: 2,
    }));

    const ctx = document.getElementById('chart-timeline');
    if (!ctx) return;

    if (state.analyticsCharts.timeline) state.analyticsCharts.timeline.destroy();
    state.analyticsCharts.timeline = new Chart(ctx, {
        type: 'line',
        data: { labels: hours, datasets },
        options: chartDefaults({ x: { title: { display: true, text: 'Hour (UTC)' } } }),
    });
}

// ══════════════════════════════════════════════════════════════════════════
// Camera Management
// ══════════════════════════════════════════════════════════════════════════

document.getElementById('btn-add-cam').addEventListener('click', async () => {
    const cam_id   = document.getElementById('new-cam-id').value.trim()     || `CAM-${Date.now()}`;
    const source   = document.getElementById('new-cam-source').value.trim() || '0';
    const name     = document.getElementById('new-cam-name').value.trim()   || cam_id;
    const location = document.getElementById('new-cam-location').value.trim();

    const res = await fetchJSON('/api/cameras', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cam_id, source, name, location }),
    });

    if (res?.success) {
        await refreshCameras();
    } else {
        alert(res?.error || 'Failed to add camera');
    }
});

document.getElementById('btn-stop-all').addEventListener('click', async () => {
    await fetch('/api/camera/stop', { method: 'POST' });
    await refreshCameras();
});

async function refreshCameras() {
    const cams = await fetchJSON('/api/cameras');
    if (!cams) return;
    state.cameras = cams;

    // Update badge
    const live = cams.filter(c => c.running).length;
    setText('cam-count-badge', `${live} CAMs LIVE`);

    // Update zone cam selector
    const zoneSel = document.getElementById('zone-cam-select');
    zoneSel.innerHTML = cams.map(c => `<option value="${c.cam_id}">${c.cam_id}</option>`).join('');

    // Update alert filter cam dropdown
    const filterCam = document.getElementById('filter-cam');
    filterCam.innerHTML = '<option value="">All Cameras</option>' +
        cams.map(c => `<option value="${c.cam_id}">${c.cam_id}</option>`).join('');

    renderCamGrid();
}

function renderCamGrid() {
    const grid = document.getElementById('cam-grid');
    if (!state.cameras.length) {
        grid.innerHTML = `
            <div class="cam-cell placeholder-cell">
                <i class="fa-solid fa-video-slash fa-2x mb-2"></i><br>
                Add a camera using the panel above
            </div>`;
        return;
    }

    grid.innerHTML = state.cameras.map(cam => `
        <div class="cam-cell" id="cell-${cam.cam_id}">
            <img id="feed-${cam.cam_id}"
                 src="/video_feed/${cam.cam_id}"
                 alt="${cam.cam_id}"
                 onerror="this.src='/video_feed/${cam.cam_id}'">
            <canvas id="canvas-${cam.cam_id}"></canvas>
            <div class="cam-hud">
                <span class="cam-label">${cam.cam_id} — ${cam.name || ''}</span>
                <span class="cam-fps" id="fps-${cam.cam_id}">${cam.fps || 0} fps</span>
            </div>
            <button class="cam-remove" onclick="removeCamera('${cam.cam_id}')">
                <i class="fa-solid fa-xmark"></i>
            </button>
        </div>
    `).join('');

    applyGridLayout(state.activeGrid);
    setupZoneCanvases();
}

async function removeCamera(cam_id) {
    if (!confirm(`Remove camera ${cam_id}?`)) return;
    await fetch(`/api/cameras/${cam_id}`, { method: 'DELETE' });
    await refreshCameras();
}

// ── Grid layout toggle ────────────────────────────────────────────────────
document.querySelectorAll('.grid-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.grid-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        applyGridLayout(btn.dataset.grid);
    });
});

function applyGridLayout(layout) {
    state.activeGrid = layout;
    const grid = document.getElementById('cam-grid');
    grid.className = `cam-grid cam-grid-${layout.replace('x', 'x')}`;
}

// ══════════════════════════════════════════════════════════════════════════
// Virtual Zone Drawing
// ══════════════════════════════════════════════════════════════════════════

function setupZoneCanvases() {
    state.cameras.forEach(cam => {
        const canvas = document.getElementById(`canvas-${cam.cam_id}`);
        const img    = document.getElementById(`feed-${cam.cam_id}`);
        if (!canvas || !img) return;

        // Keep canvas sized to its cell
        const syncSize = () => {
            canvas.width  = img.clientWidth  || canvas.clientWidth;
            canvas.height = img.clientHeight || canvas.clientHeight;
        };
        img.addEventListener('load', syncSize);
        setInterval(syncSize, 1000);

        let drawing = false, sx = 0, sy = 0;

        canvas.addEventListener('mousedown', e => {
            if (!state.drawingZone || state.zoneCamId !== cam.cam_id) return;
            drawing = true; sx = e.offsetX; sy = e.offsetY;
        });

        canvas.addEventListener('mousemove', e => {
            if (!drawing) return;
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.strokeStyle = '#ff2244';
            ctx.setLineDash([5, 5]);
            ctx.lineWidth = 2;
            ctx.strokeRect(sx, sy, e.offsetX - sx, e.offsetY - sy);
        });

        canvas.addEventListener('mouseup', e => {
            if (!drawing) return;
            drawing = false;
            state.drawingZone = false;

            const scaleX = 640 / canvas.width;
            const scaleY = 480 / canvas.height;
            const x = Math.round(Math.min(sx, e.offsetX) * scaleX);
            const y = Math.round(Math.min(sy, e.offsetY) * scaleY);
            const w = Math.round(Math.abs(e.offsetX - sx) * scaleX);
            const h = Math.round(Math.abs(e.offsetY - sy) * scaleY);

            fetch(`/api/zone/${cam.cam_id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ x, y, w, h }),
            });

            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        });
    });
}

document.getElementById('btn-draw-zone').addEventListener('click', () => {
    state.zoneCamId   = document.getElementById('zone-cam-select').value;
    state.drawingZone = true;
});

document.getElementById('btn-clear-zone').addEventListener('click', () => {
    const camId = document.getElementById('zone-cam-select').value;
    fetch(`/api/zone/${camId}`, { method: 'DELETE' });
    const canvas = document.getElementById(`canvas-${camId}`);
    if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
});

// ══════════════════════════════════════════════════════════════════════════
// SSE Events
// ══════════════════════════════════════════════════════════════════════════

const evtSource = new EventSource('/events');
evtSource.onmessage = e => {
    const data    = JSON.parse(e.data);
    const timeNow = new Date().toLocaleTimeString('en-GB', { hour12: false });
    addLiveEvent(data, timeNow);
};

function addLiveEvent(data, timeNow) {
    const list = document.getElementById('live-events-list');
    let html = '', cardClass = '';

    if (data.type === 'alarm') {
        cardClass = 'alarm';
        html = `
            <div class="event-card alarm">
                <div class="d-flex justify-content-between">
                    <span class="event-title">⚠ INTRUSION</span>
                    <span class="event-time">${timeNow}</span>
                </div>
                <div class="event-desc">${data.message || ''}</div>
                <div class="event-meta">${data.cam_id || 'CAM-01'} | Track ${data.track_id || '—'}</div>
            </div>`;
        showBanner('alarm-banner', 12000);
        playIntrusionAlarm();
        if (data.img_path) {
            document.getElementById('intruder-modal-img').src = data.img_path;
            document.getElementById('intruder-modal-text').innerText = data.message || "UNKNOWN PERSON IN RESTRICTED ZONE";
            document.getElementById('intruder-modal').classList.remove('d-none');
        }
    } else if (data.type === 'suspect') {
        html = `
            <div class="event-card suspect">
                <div class="d-flex justify-content-between">
                    <span class="event-title" style="color:#cc44ff;">⚠ SUSPECT</span>
                    <span class="event-time">${timeNow}</span>
                </div>
                <div class="event-desc">${data.data}</div>
                <div class="event-meta">Conf: ${(data.confidence * 100).toFixed(1)}% | ${data.cam_id || ''}</div>
            </div>`;
        showBanner('suspect-banner', 15000);
        playSuspectAlarm();
        if (data.img_path) {
            document.getElementById('intruder-modal-img').src = data.img_path;
            document.getElementById('intruder-modal-text').innerText = "WANTED SUSPECT IDENTIFIED: " + data.data;
            document.getElementById('intruder-modal').classList.remove('d-none');
        }
    } else if (data.type === 'plate') {
        html = `
            <div class="event-card">
                <div class="d-flex justify-content-between">
                    <span class="event-title">PLATE READ</span>
                    <span class="event-time">${timeNow}</span>
                </div>
                <div class="event-desc" style="color:var(--accent-amber);font-size:1rem;font-weight:bold;">${data.data}</div>
                <div class="event-meta">Conf: ${((data.confidence||0)*100).toFixed(1)}% | ${data.cam_id || 'CAM-01'}</div>
            </div>`;
    } else if (data.type === 'night') {
        html = `
            <div class="event-card night">
                <div class="d-flex justify-content-between">
                    <span class="event-title" style="color:var(--accent-blue);">🌙 NIGHT ACTIVITY</span>
                    <span class="event-time">${timeNow}</span>
                </div>
                <div class="event-desc">${data.message || ''}</div>
            </div>`;
    } else if (data.type === 'suspicious') {
        html = `
            <div class="event-card" style="border-left:3px solid #ff8c00;">
                <div class="d-flex justify-content-between">
                    <span class="event-title" style="color:#ff8c00;">LOITERING</span>
                    <span class="event-time">${timeNow}</span>
                </div>
                <div class="event-desc">${data.message || ''}</div>
            </div>`;
    } else if (data.type === 'crowd') {
        html = `
            <div class="event-card" style="border-left:3px solid #ffcc00;">
                <div class="d-flex justify-content-between">
                    <span class="event-title" style="color:#ffcc00;">CROWD ALERT</span>
                    <span class="event-time">${timeNow}</span>
                </div>
                <div class="event-desc">${data.message || ''}</div>
            </div>`;
    }

    if (html) list.insertAdjacentHTML('afterbegin', html);
}

document.getElementById('btn-clear-events').addEventListener('click', () => {
    document.getElementById('live-events-list').innerHTML = '';
});

function showBanner(id, ms) {
    const el = document.getElementById(id);
    el.classList.remove('d-none');
    setTimeout(() => el.classList.add('d-none'), ms);
}

// ══════════════════════════════════════════════════════════════════════════
// Alerts Log
// ══════════════════════════════════════════════════════════════════════════

async function loadAlerts() {
    const type     = document.getElementById('filter-type').value;
    const cam_id   = document.getElementById('filter-cam').value;
    const dateFrom = document.getElementById('filter-from').value;
    const dateTo   = document.getElementById('filter-to').value;

    let url = `/api/alerts?limit=500`;
    if (type)     url += `&alert_type=${type}`;
    if (cam_id)   url += `&cam_id=${cam_id}`;
    if (dateFrom) url += `&date_from=${dateFrom}`;
    if (dateTo)   url += `&date_to=${dateTo}`;

    const rows = await fetchJSON(url);
    if (!rows) return;

    setText('alert-count', `${rows.length} records`);
    const tbody = document.getElementById('alerts-tbody');
    if (!rows.length) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="py-4">
                    <div class="empty-state">
                        <i class="fa-solid fa-file-excel mb-2"></i>
                        <div>No alert records found matching the criteria</div>
                    </div>
                </td>
            </tr>`;
        return;
    }

    tbody.innerHTML = rows.map((r, i) => `
        <tr>
            <td class="text-muted">${r.id}</td>
            <td>${r.timestamp?.replace('T', ' ').substring(0, 19) || ''}</td>
            <td><span class="badge-alert badge-${r.alert_type}">${r.alert_type}</span></td>
            <td>${r.cam_id || '—'}</td>
            <td class="text-muted">${r.location || '—'}</td>
            <td style="max-width:280px;">${r.message || ''}</td>
            <td>
                ${r.image_path
                    ? `<img src="/captures/${r.image_path.replace('captures/', '')}"
                           class="alert-thumb" onclick="window.open(this.src)" title="View snapshot">`
                    : '<span class="text-muted small">—</span>'}
            </td>
        </tr>
    `).join('');
}

document.getElementById('btn-filter-alerts').addEventListener('click', loadAlerts);

document.getElementById('btn-export-csv').addEventListener('click', () => {
    const type     = document.getElementById('filter-type').value;
    const cam_id   = document.getElementById('filter-cam').value;
    let url = '/api/alerts/export?limit=5000';
    if (type)   url += `&alert_type=${type}`;
    if (cam_id) url += `&cam_id=${cam_id}`;
    window.open(url);
});

// ══════════════════════════════════════════════════════════════════════════
// Analytics
// ══════════════════════════════════════════════════════════════════════════

async function loadAnalytics() {
    const summary = await fetchJSON('/api/analytics/summary');
    if (!summary) return;

    // Alert type donut
    const ab = summary.alert_breakdown || {};
    renderDonut('chart-alert-types', Object.keys(ab), Object.values(ab),
        ['#ff2244','#8800ff','#00aaff','#ff8c00','#ffcc00','#00ff88']);

    // FRS split pie
    renderDonut('chart-frs-split',
        ['Authorized', 'Suspects'],
        [summary.authorized_profiles || 0, summary.suspect_profiles || 0],
        ['#00ff88', '#ff2244']);

    // Plates line chart from timeline
    const timeline = await fetchJSON('/api/analytics/timeline?hours=24');
    if (timeline) {
        const hours = Array.from({length:24}, (_,i) => String(i).padStart(2,'0'));
        const plateData = hours.map(h => {
            const row = timeline.find(d => d.hour === h && d.alert_type === 'plate');
            return row ? row.count : 0;
        });
        const ctx = document.getElementById('chart-plates');
        if (state.analyticsCharts.plates) state.analyticsCharts.plates.destroy();
        state.analyticsCharts.plates = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: hours,
                datasets: [{ label: 'Plates/hr', data: plateData,
                    backgroundColor: '#ffcc0055', borderColor: '#ffcc00', borderWidth: 1 }]
            },
            options: chartDefaults(),
        });
    }

    // Quick stats
    const s = summary.stats || {};
    document.getElementById('analytics-quick').innerHTML = `
        <div class="cam-status-item"><span>Total Vehicles</span><span class="cam-online">${s.total_vehicles||0}</span></div>
        <div class="cam-status-item"><span>Persons Detected</span><span class="cam-online">${s.persons_detected||0}</span></div>
        <div class="cam-status-item"><span>Plates Read</span><span class="cam-online">${s.plates_read||0}</span></div>
        <div class="cam-status-item"><span>Intrusion Alerts</span><span class="cam-offline">${s.intrusion_alerts||0}</span></div>
        <div class="cam-status-item"><span>Suspect Alerts</span><span class="cam-offline">${s.suspect_alerts||0}</span></div>
        <div class="cam-status-item"><span>Night Alerts</span><span style="color:var(--accent-blue)">${s.night_alerts||0}</span></div>
        <div class="cam-status-item"><span>Total Profiles</span><span>${(summary.authorized_profiles||0)+(summary.suspect_profiles||0)}</span></div>
        <div class="cam-status-item"><span>Total Plates (DB)</span><span>${summary.total_plates||0}</span></div>
    `;

    // Load plate log
    loadPlatelog('/api/plates?limit=50');
}

async function loadPlatelog(url) {
    const rows = await fetchJSON(url);
    if (!rows) return;
    const tbody = document.getElementById('plate-tbody');
    if (!rows.length) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" class="py-4">
                    <div class="empty-state">
                        <i class="fa-solid fa-car mb-2"></i>
                        <div>No plate records found</div>
                    </div>
                </td>
            </tr>`;
        return;
    }
    tbody.innerHTML = rows.map((r, i) => `
        <tr>
            <td class="text-muted">${r.id}</td>
            <td>${r.timestamp?.replace('T',' ').substring(0,19)||''}</td>
            <td style="color:var(--accent-amber);font-weight:bold;font-size:1rem;">${r.plate_text}</td>
            <td>${((r.confidence||0)*100).toFixed(1)}%</td>
            <td>${r.cam_id||'—'}</td>
        </tr>
    `).join('');
}

document.getElementById('btn-plate-search').addEventListener('click', () => {
    const q = document.getElementById('plate-search-input').value.trim();
    if (q) loadPlatelog(`/api/plates/search?q=${encodeURIComponent(q)}`);
});
document.getElementById('btn-plate-all').addEventListener('click', () => {
    loadPlatelog('/api/plates?limit=100');
});

function renderDonut(canvasId, labels, data, colours) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;
    if (state.analyticsCharts[canvasId]) state.analyticsCharts[canvasId].destroy();
    state.analyticsCharts[canvasId] = new Chart(ctx, {
        type: 'doughnut',
        data: { labels, datasets: [{ data, backgroundColor: colours, borderColor: '#0d1526', borderWidth: 2 }] },
        options: {
            plugins: {
                legend: { labels: { color: '#c8d8e8', font: { size: 10 }, boxWidth: 12 } }
            },
            cutout: '60%',
        },
    });
}

function chartDefaults(extraScales = {}) {
    return {
        responsive: true,
        plugins: {
            legend: { labels: { color: '#c8d8e8', font: { size: 10 }, boxWidth: 12 } }
        },
        scales: {
            x: { ticks: { color: '#4a6070', font: { size: 9 } }, grid: { color: '#1a2a42' }, ...extraScales.x },
            y: { ticks: { color: '#4a6070', font: { size: 9 } }, grid: { color: '#1a2a42' }, ...extraScales.y },
        },
    };
}

// ══════════════════════════════════════════════════════════════════════════
// Upload / Batch Processing
// ══════════════════════════════════════════════════════════════════════════

const uploadFile = document.getElementById('upload-file');
const dropZone   = document.getElementById('drop-zone');

dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    if (e.dataTransfer.files.length) {
        uploadFile.files = e.dataTransfer.files;
        onFileSelected();
    }
});
uploadFile.addEventListener('change', onFileSelected);

function onFileSelected() {
    const f = uploadFile.files[0];
    if (!f) return;
    document.getElementById('upload-filename').textContent = `Selected: ${f.name} (${(f.size/1024/1024).toFixed(1)} MB)`;
    document.getElementById('btn-upload-start').disabled = false;
}

document.getElementById('btn-upload-start').addEventListener('click', async () => {
    const f = uploadFile.files[0];
    if (!f) return;

    const formData = new FormData();
    formData.append('file', f);

    document.getElementById('upload-idle').classList.add('d-none');
    document.getElementById('upload-progress-panel').classList.remove('d-none');
    document.getElementById('btn-upload-start').disabled = true;

    const res = await fetchJSON('/api/upload/video', { method: 'POST', body: formData });
    if (!res?.success) {
        alert(res?.error || 'Upload failed');
        return;
    }
    state.uploadJobId = res.job_id;

    const sse = new EventSource(`/api/upload/stream/${res.job_id}`);
    sse.onmessage = e => {
        const job = JSON.parse(e.data);
        const pct = job.progress || 0;
        document.getElementById('upload-pct').textContent = pct.toFixed(1) + '%';
        document.getElementById('upload-progress-bar').style.width = pct + '%';

        const resultList = document.getElementById('upload-results-list');
        if (job.results?.length) {
            resultList.innerHTML = job.results.slice(-20).reverse().map(r => `
                <div class="event-card" style="border-top:1px solid #1a2a42;">
                    <span class="event-title">${r.type?.toUpperCase() || 'EVENT'}</span>
                    <span class="event-meta ms-2">${r.data || r.message || ''}</span>
                </div>
            `).join('');
        }

        if (job.status === 'done' || job.status === 'error') {
            sse.close();
            document.getElementById('btn-upload-start').disabled = false;
        }
    };
});

// ══════════════════════════════════════════════════════════════════════════
// FRS — Facial Recognition
// ══════════════════════════════════════════════════════════════════════════

// Role toggle
document.querySelectorAll('.role-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.role-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.frsRole = btn.dataset.role;
        document.getElementById('frs-role').value = btn.dataset.role;
    });
});

// Method tabs
document.querySelectorAll('[data-method]').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('[data-method]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.frsMethod = btn.dataset.method;
        document.getElementById('frs-pane-upload').classList.toggle('d-none', state.frsMethod !== 'upload');
        document.getElementById('frs-pane-webcam').classList.toggle('d-none', state.frsMethod !== 'webcam');
    });
});

// Photo upload
document.getElementById('frs-file').addEventListener('change', () => {
    const f = document.getElementById('frs-file').files[0];
    if (!f) return;
    const url = URL.createObjectURL(f);
    document.getElementById('frs-upload-preview').innerHTML =
        `<img src="${url}" style="max-height:100px;border-radius:4px;border:1px solid #1a2a42;">`;
    document.getElementById('btn-frs-register-upload').disabled = false;
});

document.getElementById('btn-frs-register-upload').addEventListener('click', () => {
    const name = document.getElementById('frs-name').value.trim();
    const file = document.getElementById('frs-file').files[0];
    if (!name) { alert('Enter a name'); return; }
    if (!file) { alert('Select an image'); return; }

    const fd = new FormData();
    fd.append('name', name);
    fd.append('role', state.frsRole);
    fd.append('file', file);
    submitFRS(fd);
});

// Webcam capture
document.getElementById('btn-frs-startcam').addEventListener('click', async () => {
    try {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            throw new Error("Browser API not available. Ensure you are using localhost or HTTPS.");
        }
        state.frsStream = await navigator.mediaDevices.getUserMedia({ video: true });
        const vid = document.getElementById('frs-video');
        vid.srcObject = state.frsStream;
        vid.classList.remove('d-none');
        document.getElementById('frs-cam-placeholder').classList.add('d-none');
        document.getElementById('frs-canvas').classList.add('d-none');
        document.getElementById('btn-frs-capture').disabled = false;
    } catch (err) {
        alert('Camera access failed: ' + err.message);
    }
});

document.getElementById('btn-frs-capture').addEventListener('click', () => {
    const vid    = document.getElementById('frs-video');
    const canvas = document.getElementById('frs-canvas');
    canvas.width  = vid.videoWidth;
    canvas.height = vid.videoHeight;
    canvas.getContext('2d').drawImage(vid, 0, 0);
    vid.classList.add('d-none');
    canvas.classList.remove('d-none');
    document.getElementById('btn-frs-retake').classList.remove('d-none');
    document.getElementById('btn-frs-capture').disabled = true;
    canvas.toBlob(blob => {
        state.capturedBlob = blob;
        document.getElementById('btn-frs-register-webcam').disabled = false;
    }, 'image/jpeg');
    if (state.frsStream) state.frsStream.getTracks().forEach(t => t.stop());
});

document.getElementById('btn-frs-retake').addEventListener('click', async () => {
    document.getElementById('btn-frs-retake').classList.add('d-none');
    document.getElementById('frs-canvas').classList.add('d-none');
    document.getElementById('btn-frs-register-webcam').disabled = true;
    state.capturedBlob = null;
    // Restart cam
    document.getElementById('btn-frs-startcam').click();
});

document.getElementById('btn-frs-register-webcam').addEventListener('click', () => {
    const name = document.getElementById('frs-name').value.trim();
    if (!name) { alert('Enter a name'); return; }
    if (!state.capturedBlob) { alert('Capture a photo first'); return; }
    const fd = new FormData();
    fd.append('name', name);
    fd.append('role', state.frsRole);
    fd.append('file', state.capturedBlob, 'capture.jpg');
    submitFRS(fd);
});

async function submitFRS(formData) {
    const res_el = document.getElementById('frs-result');
    res_el.innerHTML = '<span class="text-warning blink">Processing...</span>';
    const res = await fetchJSON('/api/frs/register', { method: 'POST', body: formData });
    if (res?.success) {
        res_el.innerHTML = `<span class="text-success">✓ ${state.frsRole.toUpperCase()} profile registered</span>`;
        loadProfiles();
    } else {
        res_el.innerHTML = `<span class="text-danger">✗ ${res?.error || 'Failed'}</span>`;
    }
}

async function loadProfiles() {
    const profiles = await fetchJSON('/api/frs/profiles');
    if (!profiles) return;

    const grid = document.getElementById('frs-profiles-grid');
    if (!profiles.length) {
        grid.innerHTML = `
            <div class="empty-state" style="grid-column: 1 / -1;">
                <i class="fa-solid fa-users mb-2"></i>
                <div>No profiles registered</div>
            </div>`;
        return;
    }

    grid.innerHTML = profiles.map(p => `
        <div class="frs-profile-card ${p.role}">
            <div class="profile-icon">
                <i class="fa-solid ${p.role === 'suspect' ? 'fa-skull' : 'fa-user-check'}"></i>
            </div>
            <div style="word-break:break-all;">${p.name}</div>
            <div class="frs-role-badge ${p.role}">${p.role.toUpperCase()}</div>
            <button class="frs-delete-btn" onclick="deleteProfile('${p.name}')">
                <i class="fa-solid fa-trash"></i>
            </button>
        </div>
    `).join('');

    // Match history
    const alerts = await fetchJSON('/api/alerts?alert_type=SUSPECT_DETECTED&limit=10');
    const matchEl = document.getElementById('frs-match-history');
    if (alerts?.length) {
        matchEl.innerHTML = alerts.map(a => `
            <div class="cam-status-item">
                <span class="cam-offline">${a.message}</span>
                <span class="text-muted">${a.timestamp?.substring(11,19)||''}</span>
            </div>
        `).join('');
    } else {
        matchEl.innerHTML = `
            <div class="empty-state mt-2 p-2">
                <i class="fa-solid fa-clock-rotate-left" style="font-size: 1.5rem;"></i>
                <div style="font-size: 0.8rem;">No recent matches</div>
            </div>`;
    }
}

async function deleteProfile(name) {
    if (!confirm(`Delete profile: ${name}?`)) return;
    await fetch(`/api/frs/profiles/${encodeURIComponent(name)}`, { method: 'DELETE' });
    loadProfiles();
}

document.getElementById('btn-refresh-profiles').addEventListener('click', loadProfiles);

// ══════════════════════════════════════════════════════════════════════════
// Utilities
// ══════════════════════════════════════════════════════════════════════════

async function fetchJSON(url, options = {}) {
    try {
        const r = await fetch(url, options);
        const data = await r.json().catch(() => null);
        if (!r.ok) return data || { error: 'HTTP Error ' + r.status };
        return data;
    } catch (err) {
        return { error: 'Network Error' };
    }
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

// ══════════════════════════════════════════════════════════════════════════
// Periodic refresh & initialization
// ══════════════════════════════════════════════════════════════════════════

// Refresh cameras every 5 seconds
setInterval(refreshCameras, 5000);

// Refresh dashboard stats every 10 seconds
setInterval(() => {
    if (!document.getElementById('dashboard').classList.contains('d-none'))
        refreshDashboard();
}, 10000);

// Initial load
(async () => {
    await refreshCameras();
    refreshDashboard();
})();
