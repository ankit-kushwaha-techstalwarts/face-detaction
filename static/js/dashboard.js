// ══════════════════════════════════════════════════════════
//  DASHBOARD
// ══════════════════════════════════════════════════════════
async function loadDashboard() {
  const r = await api('/api/dashboard');
  if (r.status !== 'ok') return;
  const d = r.data;
  $('s-total').textContent   = d.total_employees;
  $('s-present').textContent = d.present_today;
  $('s-absent').textContent  = d.absent_today;
  $('s-late').textContent    = d.late_today;
  $('s-unknown').textContent = d.unknown_today;

  // Sidebar badge: total unreviewed unknown faces (all time)
  const unkBadge = $('unk-nav-badge');
  if (d.unknown_unreviewed_total > 0) {
    unkBadge.textContent = d.unknown_unreviewed_total;
    unkBadge.style.display = 'inline-block';
  } else {
    unkBadge.style.display = 'none';
  }

  // Weekly chart
  const maxP = Math.max(...d.weekly_trend.map(x=>x.present), 1);
  $('trend-chart').innerHTML = d.weekly_trend.map(x => {
    const h = Math.max(4, Math.round((x.present/maxP)*170));
    const label = new Date(x.date).toLocaleDateString('en-IN',{weekday:'short'});
    return `<div class="chart-bar" style="height:${h}px" title="${x.date}: ${x.present} present">
      <span class="bar-val">${x.present}</span>
      <span class="bar-label">${label}</span>
    </div>`;
  }).join('');

  // Dept breakdown
  $('dept-breakdown').innerHTML = d.dept_stats.map(ds => {
    const pct = ds.total ? Math.round((ds.present/ds.total)*100) : 0;
    return `<div class="mb-3">
      <div class="flex items-center gap-2 mb-1">
        <span class="text-sm" style="flex:1">${ds.department||'Unassigned'}</span>
        <span class="text-xs text-gray">${ds.present}/${ds.total}</span>
        <span class="text-xs" style="color:var(--blue)">${pct}%</span>
      </div>
      <div class="progress"><div class="progress-bar" style="width:${pct}%"></div></div>
    </div>`;
  }).join('') || '<p class="text-sm text-gray">No data</p>';

  // Recent activity
  $('recent-tbody').innerHTML = d.recent_activity.length === 0
    ? `<tr class="empty-row"><td colspan="5">No recent activity</td></tr>`
    : d.recent_activity.map(a => `<tr>
        <td><div class="flex items-center gap-2">${a.name}</div></td>
        <td class="text-gray">${a.emp_id}</td>
        <td><span class="tag tag-${a.punch_type.toLowerCase()}">${a.punch_type}</span></td>
        <td>${fmtTime(a.punch_time)}</td>
        <td>${a.confidence ? a.confidence.toFixed(1)+'%' : '—'}</td>
      </tr>`).join('');

  // ── New widgets ────────────────────────────────────────────

  // Hourly heatmap
  if (d.hourly_heatmap) {
    const maxH = Math.max(...d.hourly_heatmap.map(x => x.count), 1);
    $('hourly-chart').innerHTML = d.hourly_heatmap.map(x => {
      const h   = Math.max(4, Math.round((x.count / maxH) * 170));
      const lbl = x.hour < 12 ? `${x.hour}am` : x.hour === 12 ? '12pm' : `${x.hour-12}pm`;
      return `<div class="chart-bar" style="height:${h}px;background:${x.count>0?'var(--blue)':'var(--gray-200)'}" title="${lbl}: ${x.count} punch-ins">
        ${x.count > 0 ? `<span class="bar-val">${x.count}</span>` : ''}
        <span class="bar-label">${lbl}</span>
      </div>`;
    }).join('');
  }

  // Top late arrivals
  if (d.top_late_month) {
    $('top-late-list').innerHTML = d.top_late_month.length === 0
      ? '<p class="text-sm text-gray">No late arrivals this month 🎉</p>'
      : d.top_late_month.map((u, i) => `
        <div class="flex items-center gap-2 mb-3">
          <div style="width:24px;height:24px;border-radius:50%;background:${i===0?'var(--red-light)':'var(--gray-100)'};
               display:grid;place-items:center;font-size:11px;font-weight:700;color:${i===0?'var(--red)':'var(--gray-600)'}">${i+1}</div>
          <div style="flex:1">
            <div class="text-sm" style="font-weight:600">${u.name}</div>
            <div class="text-xs text-gray">${u.department || '—'} · ${u.emp_id}</div>
          </div>
          <span class="tag tag-late">${u.late_days} day${u.late_days!==1?'s':''}</span>
        </div>`).join('');
  }

  // Camera status
  if (d.camera_status) {
    const cs = d.camera_status;
    $('cam-status-widget').innerHTML = `
      <div style="display:flex;gap:16px;margin-bottom:12px">
        <div class="stat-card" style="flex:1;padding:14px">
          <div class="stat-icon" style="background:var(--green-light);width:38px;height:38px;font-size:18px">📹</div>
          <div><h3 style="font-size:22px">${cs.live}</h3><p style="font-size:11px">Live</p></div>
        </div>
        <div class="stat-card" style="flex:1;padding:14px">
          <div class="stat-icon" style="background:var(--red-light);width:38px;height:38px;font-size:18px">📷</div>
          <div><h3 style="font-size:22px">${cs.offline}</h3><p style="font-size:11px">Offline</p></div>
        </div>
        <div class="stat-card" style="flex:1;padding:14px">
          <div class="stat-icon" style="background:var(--gray-100);width:38px;height:38px;font-size:18px">🎥</div>
          <div><h3 style="font-size:22px">${cs.total}</h3><p style="font-size:11px">Total</p></div>
        </div>
      </div>
      <button class="btn btn-outline btn-sm w-full" onclick="navTo('live')">Open Live Cameras →</button>`;
  }

  // Unknown alerts
  if (d.unknown_alerts) {
    $('unknown-alerts-widget').innerHTML = d.unknown_alerts.length === 0
      ? '<p class="text-sm text-gray">No unreviewed unknown faces today ✅</p>'
      : d.unknown_alerts.map(u => `
          <div style="position:relative;width:80px">
            <img src="${u.snapshot_path ? '/'+u.snapshot_path : ''}"
                 style="width:80px;height:80px;object-fit:cover;border-radius:8px;border:2px solid var(--red-light)"
                 onerror="this.parentElement.style.display='none'"/>
            <div style="position:absolute;bottom:0;left:0;right:0;background:rgba(0,0,0,.6);color:#fff;font-size:9px;padding:2px 4px;border-radius:0 0 6px 6px;text-align:center">${u.camera_name||'CAM'}</div>
          </div>`).join('') +
        (d.unknown_today > 5 ? `<div style="display:flex;align-items:center;color:var(--gray-400);font-size:12px">+${d.unknown_today - 5} more</div>` : '');
  }
}

// ══════════════════════════════════════════════════════════
//  CAMERAS (Live)
// ══════════════════════════════════════════════════════════
async function loadCamGrid() {
  const r = await api('/api/cameras');
  const el = $('cam-grid');
  if (r.status !== 'ok' || !r.data.length) {
    el.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:48px 0">
      <div style="font-size:40px;margin-bottom:10px">📹</div>
      <p class="text-sm text-gray">No cameras configured yet.</p>
      <button class="btn btn-primary btn-sm mt-3" onclick="openAddCamera()">➕ Add Camera</button>
    </div>`;
    return;
  }
  el.innerHTML = r.data.map(c => {
    // Three distinct states:
    //   1. not started  → dark placeholder + Start button
    //   2. connecting   → spinner + Stop button  (streaming=true, connected=false)
    //   3. live         → MJPEG img  (streaming=true, connected=true)
    const statusDot = c.connected
      ? `<span style="font-size:10px;color:var(--green)">● Live</span>`
      : c.streaming
        ? `<span style="font-size:10px;color:var(--yellow,#f59e0b)">◌ Connecting…</span>`
        : c.active
          ? `<span style="font-size:10px;color:var(--gray-400)">○ Idle</span>`
          : `<span style="font-size:10px;color:var(--red)">✕ Inactive</span>`;

    let feedHtml = '';
    if (c.connected) {
      feedHtml = `<img class="cam-feed" id="cam-feed-${c.id}"
        src="/video_feed/${c.id}"
        alt="${c.name} feed"
        style="height:${camState.height}px;display:block;width:100%;object-fit:cover"/>`;
    } else if (c.streaming) {
      feedHtml = `<div id="cam-nofeed-${c.id}"
        style="height:${camState.height}px;background:#111;display:flex;align-items:center;
               justify-content:center;flex-direction:column;gap:8px;color:#aaa">
        <div style="font-size:28px;animation:spin 1.4s linear infinite">⏳</div>
        <p style="font-size:12px">Connecting to camera…</p>
        <button class="btn btn-danger btn-xs" onclick="stopCam(${c.id})">⏹ Stop</button>
      </div>`;
    } else {
      feedHtml = `<div id="cam-nofeed-${c.id}"
        style="height:${camState.height}px;background:#111;display:flex;align-items:center;
               justify-content:center;flex-direction:column;gap:8px;color:#666">
        <div style="font-size:32px">📷</div>
        <p style="font-size:12px">Camera not started</p>
        <button class="btn btn-success btn-xs" onclick="startCam(${c.id})">▶ Start Stream</button>
      </div>`;
    }

    return `<div class="cam-card">
      ${feedHtml}
      <div class="cam-info">
        <h3>${c.name} ${statusDot}</h3>
        <p>${c.location || '—'} · ${c.direction}</p>
        <div class="cam-controls">
          ${c.streaming
            ? `<button class="btn btn-danger btn-xs" onclick="stopCam(${c.id})">⏹ Stop</button>`
            : `<button class="btn btn-success btn-xs" onclick="startCam(${c.id})">▶ Start</button>`
          }
          <button class="btn btn-outline btn-xs" onclick="openEditCamera(${c.id})">✏ Edit</button>
          <button class="btn btn-outline btn-xs" style="color:var(--red)" onclick="delCam(${c.id})">🗑</button>
        </div>
      </div>
    </div>`;
  }).join('');

  // Restore saved size settings after rendering
  const h = $('cam-height-slider');
  if (h) { h.value = camState.height; $('cam-height-val').textContent = camState.height + 'px'; }
  document.querySelectorAll('.cam-size-btn').forEach(b => {
    b.classList.toggle('active', parseInt(b.dataset.cols) === camState.cols);
  });
  applyCamSize();
}

// Track which cameras are currently being polled so we don't double-start
const _camPolling = new Set();

async function startCam(id) {
  const r = await api(`/api/cameras/${id}/start`, {method:'POST'});
  if (r.status !== 'ok') { toast(r.message, 'error'); return; }
  toast('Camera starting — waiting for frames…', 'success');
  loadCamGrid(); // show "Connecting…" state immediately
  if (_camPolling.has(id)) return;
  _camPolling.add(id);
  // Poll every 2 s for up to 30 s until frames are flowing
  let attempts = 0;
  const poll = async () => {
    attempts++;
    const status = await api('/api/cameras');
    const cam = (status.data || []).find(c => c.id === id);
    if (cam && cam.connected) {
      _camPolling.delete(id);
      loadCamGrid(); // switch to live MJPEG view
      return;
    }
    if (!cam || !cam.streaming) {
      // Thread died before connecting — stop polling
      _camPolling.delete(id);
      loadCamGrid();
      return;
    }
    if (attempts < 15) {
      setTimeout(poll, 2000);
    } else {
      _camPolling.delete(id);
      toast('Camera stream timed out — check RTSP URL and network', 'error');
      loadCamGrid();
    }
  };
  setTimeout(poll, 2000);
}
async function stopCam(id) {
  await api(`/api/cameras/${id}/stop`,{method:'POST'});
  loadCamGrid();
}
async function delCam(id) {
  if(!confirm('Delete this camera?')) return;
  await api(`/api/cameras/${id}`,{method:'DELETE'});
  toast('Camera removed','success');
  loadCamGrid();
}

// ══════════════════════════════════════════════════════════
//  CAMERA SIZE CONTROLS
// ══════════════════════════════════════════════════════════
const camState = {
  cols:   parseInt(localStorage.getItem('cam_cols')  || '4'),
  height: parseInt(localStorage.getItem('cam_height') || '175'),
};

function setCamSize(btn, cols) {
  document.querySelectorAll('.cam-size-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  camState.cols = cols;
  localStorage.setItem('cam_cols', cols);
  applyCamSize();
}

function updateCamHeight(val) {
  camState.height = parseInt(val);
  $('cam-height-val').textContent = val + 'px';
  localStorage.setItem('cam_height', val);
  applyCamSize();
}

function applyCamSize() {
  const grid = $('cam-grid'); if (!grid) return;
  const minW = {1:'100%', 2:'48%', 3:'31%', 4:'22%'}[camState.cols] || '22%';
  grid.style.gridTemplateColumns = `repeat(auto-fill, minmax(${minW}, 1fr))`;
  document.querySelectorAll('.cam-feed').forEach(v => v.style.height = camState.height + 'px');
}

// applyCamSize is called inside loadCamGrid after render — no wrapper needed
