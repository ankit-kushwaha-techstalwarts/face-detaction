// ══════════════════════════════════════════════════════════
//  UNKNOWN FACES
// ══════════════════════════════════════════════════════════
async function loadUnknown(page=1) {
  state.unkPage = page;
  const rev = $('unk-filter').value;
  const params = new URLSearchParams({page,per_page:24});
  if(rev!=='') params.set('reviewed',rev);
  const r = await api(`/api/unknown-faces?${params}`);
  if(r.status!=='ok') return;
  $('unk-count').textContent = `${r.data.total} record(s)`;
  $('unk-grid').innerHTML = r.data.records.length===0
    ? '<p class="text-sm text-gray">No unknown face records found.</p>'
    : r.data.records.map(f=>`
        <div class="unk-card ${f.reviewed?'unk-reviewed':''}" data-fid="${f.id}"
             onclick="toggleUnkCard(${f.id},this,this.querySelector('.unk-checkbox'))">
          <input type="checkbox" class="unk-checkbox" onclick="event.stopPropagation();toggleUnkCard(${f.id},this.closest('.unk-card'),this)"/>
          <img src="${f.snapshot_path?'/'+f.snapshot_path:''}" onerror="this.style.display='none'" alt="Unknown face"/>
          <div class="unk-badge">${f.camera_name||'CAM'}</div>
          <div class="info">
            <div>${fmtTime(f.detected_at)}</div>
            <div class="text-gray">${f.location||''}</div>
            <div style="display:flex;gap:4px;margin-top:6px;flex-wrap:wrap" onclick="event.stopPropagation()">
              ${!f.reviewed?`<button class="btn btn-outline btn-xs" onclick="reviewUnk(${f.id})">✓ Review</button>`:'<span style="color:var(--green);font-size:11px">✓ Reviewed</span>'}
              <button class="btn btn-outline btn-xs" onclick="assignUnk(${f.id})" title="Assign to an employee to improve recognition">👤 Assign</button>
              <button class="btn btn-outline btn-xs" style="color:var(--red)" onclick="delUnk(${f.id})">🗑</button>
            </div>
          </div>
        </div>`).join('');
  renderPagination('unk-pagination', page, Math.ceil(r.data.total/24), loadUnknown);
}

// ── Unknown Visitors (grouped repeat captures, with IN/OUT visit counts) ──
function switchUnkView(view) {
  const isAll = view === 'all';
  $('unk-tab-all').className     = `btn btn-sm ${isAll ? 'btn-primary' : 'btn-outline'}`;
  $('unk-tab-persons').className = `btn btn-sm ${!isAll ? 'btn-primary' : 'btn-outline'}`;
  $('unk-view-all').style.display     = isAll ? '' : 'none';
  $('unk-view-persons').style.display = isAll ? 'none' : '';
  if (!isAll) loadUnknownPersons(state.unkPersonsPage);
}

async function loadUnknownPersons(page=1) {
  state.unkPersonsPage = page;
  const r = await api(`/api/unknown-faces/persons?page=${page}&per_page=24`);
  if (r.status !== 'ok') return;
  $('unk-persons-count').textContent = `${r.data.total} unique visitor(s)`;
  $('unk-persons-grid').innerHTML = r.data.records.length===0
    ? '<p class="text-sm text-gray">No unidentified visitors recorded yet.</p>'
    : r.data.records.map(p => `
        <div class="unk-card" onclick="viewVisitorTimeline('${p.cluster_id}')" style="cursor:pointer">
          <img src="${p.snapshot_path?'/'+p.snapshot_path:''}" onerror="this.style.display='none'" alt="Unknown visitor"/>
          <div class="unk-badge">${p.camera_name||'CAM'}</div>
          <div class="info">
            <div style="font-weight:600">Seen ${p.visit_count}×</div>
            <div style="display:flex;gap:6px;margin-top:4px">
              <span class="tag tag-in">IN ${p.in_count}</span>
              <span class="tag tag-out">OUT ${p.out_count}</span>
            </div>
            <div class="text-gray" style="margin-top:4px">Last seen: ${fmtTime(p.last_seen)}</div>
            ${p.unreviewed_count>0?`<div style="color:var(--orange);font-size:11px;margin-top:2px">${p.unreviewed_count} unreviewed</div>`:''}
          </div>
        </div>`).join('');
  renderPagination('unk-persons-pagination', page, Math.ceil(r.data.total/24), loadUnknownPersons);
}

async function viewVisitorTimeline(clusterId) {
  const r = await api(`/api/unknown-faces/persons/${clusterId}`);
  if (r.status !== 'ok') { toast(r.message,'error'); return; }
  const recs = r.data.records;
  const inCount  = recs.filter(x=>x.punch_type==='IN').length;
  const outCount = recs.filter(x=>x.punch_type==='OUT').length;
  $('visitor-timeline-summary').textContent = `Seen ${recs.length} time(s) — IN ${inCount} · OUT ${outCount}`;
  $('visitor-timeline-list').innerHTML = recs.map(v => `
    <div style="display:flex;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--gray-100)">
      <img src="${v.snapshot_path?'/'+v.snapshot_path:''}" onerror="this.style.display='none'" style="width:48px;height:48px;border-radius:6px;object-fit:cover;background:var(--gray-100)"/>
      <div style="flex:1">
        <div class="text-sm">${fmtTime(v.detected_at)}</div>
        <div class="text-xs text-gray">${v.camera_name||''}${v.location?' · '+v.location:''}</div>
      </div>
      <span class="tag ${v.punch_type==='OUT'?'tag-out':'tag-in'}">${v.punch_type||'-'}</span>
    </div>`).join('');
  openModal('modal-visitor-timeline');
}

async function reviewUnk(id) {
  await api(`/api/unknown-faces/${id}/review`,{method:'PUT',body:JSON.stringify({notes:'Reviewed'})});
  toast('Marked as reviewed','success');
  loadUnknown(state.unkPage);
  loadDashboard();
}
async function delUnk(id) {
  if(!confirm('Delete this record?')) return;
  await api(`/api/unknown-faces/${id}`,{method:'DELETE'});
  loadUnknown(state.unkPage);
  loadDashboard();
}

// ── Assign unknown face to an employee (improves recognition accuracy) ──
let _assignUnkId = null;
function assignUnk(id) {
  _assignUnkId = id;
  $('assign-emp-search').value = '';
  $('assign-emp-results').innerHTML = '<p class="text-xs text-gray" style="padding:8px">Type a name or employee ID to search…</p>';
  openModal('modal-assign-unk');
  setTimeout(() => $('assign-emp-search').focus(), 50);
}

let _assignSearchTimer = null;
$('assign-emp-search').addEventListener('input', () => {
  clearTimeout(_assignSearchTimer);
  const q = $('assign-emp-search').value.trim();
  if (!q) {
    $('assign-emp-results').innerHTML = '<p class="text-xs text-gray" style="padding:8px">Type a name or employee ID to search…</p>';
    return;
  }
  _assignSearchTimer = setTimeout(async () => {
    const r = await api(`/api/users?q=${encodeURIComponent(q)}&per_page=20&active=1`);
    if (r.status !== 'ok') return;
    const users = r.data.users || r.data;
    $('assign-emp-results').innerHTML = users.length === 0
      ? '<p class="text-xs text-gray" style="padding:8px">No employees found</p>'
      : users.map(u => `
          <div class="assign-emp-row" onclick="confirmAssignUnk(${u.id},'${u.name.replace(/'/g,"\\'")}')">
            <div style="font-weight:600;font-size:13px">${u.name}</div>
            <div class="text-xs text-gray">${u.emp_id}${u.department?' · '+u.department:''}</div>
          </div>`).join('');
  }, 250);
});

async function confirmAssignUnk(userId, name) {
  if (!confirm(`Assign this photo to ${name} and improve their recognition profile?`)) return;
  const r = await api(`/api/unknown-faces/${_assignUnkId}/assign`, {method:'POST', body:JSON.stringify({user_id:userId})});
  if (r.status === 'ok') {
    toast(r.message, 'success');
    closeModal('modal-assign-unk');
    loadUnknown(state.unkPage);
    loadDashboard();
  } else {
    toast(r.message, 'error');
  }
}

// ══════════════════════════════════════════════════════════
//  UNKNOWN FACE DETECTION TOGGLE
// ══════════════════════════════════════════════════════════
async function loadUnknownDetectionToggle() {
  const r = await api('/api/settings');
  if (r.status !== 'ok') return;
  const s   = r.data.settings.find(x => x.key === 'unknown_detection');
  const on  = s ? s.value === '1' : true;
  const tog = $('unk-detect-toggle');
  if (tog) tog.checked = on;
  _applyDetectionBanner(on);
}

function _applyDetectionBanner(on) {
  const banner = $('unk-detect-banner');
  if (!banner) return;
  banner.style.borderLeft = `4px solid ${on ? 'var(--green)' : 'var(--red)'}`;
}

async function toggleUnknownDetection(checkbox) {
  const val = checkbox.checked ? '1' : '0';
  const r   = await api('/api/settings', {
    method: 'PUT',
    body: JSON.stringify({ unknown_detection: val })
  });
  if (r.status === 'ok') {
    toast(checkbox.checked ? '✅ Unknown face detection ON' : '⛔ Unknown face detection OFF',
          checkbox.checked ? 'success' : 'info');
    _applyDetectionBanner(checkbox.checked);
    state.settings['unknown_detection'] = val;
  } else {
    checkbox.checked = !checkbox.checked;   // revert
    toast(r.message, 'error');
  }
}

// ══════════════════════════════════════════════════════════
//  UNKNOWN FACES — MULTI-SELECT
// ══════════════════════════════════════════════════════════
let unkSelectionMode = false;
const unkSelected    = new Set();

function toggleSelectionMode() {
  unkSelectionMode = !unkSelectionMode;
  const grid = $('unk-grid');
  const bar  = $('unk-selection-bar');
  const btn  = $('btn-sel-mode');
  grid.classList.toggle('selection-mode', unkSelectionMode);
  bar.style.display  = unkSelectionMode ? 'flex' : 'none';
  btn.textContent    = unkSelectionMode ? '✕ Cancel Selection' : '☑ Select Multiple';
  btn.className      = unkSelectionMode ? 'btn btn-outline btn-sm' : 'btn btn-outline btn-sm';
  if (!unkSelectionMode) {
    unkSelected.clear();
    document.querySelectorAll('.unk-checkbox').forEach(cb => cb.checked = false);
    document.querySelectorAll('.unk-card').forEach(c => c.classList.remove('selected'));
    updateSelCount();
  }
}

function updateSelCount() {
  $('unk-sel-count').textContent = `${unkSelected.size} selected`;
  $('btn-bulk-delete').disabled  = unkSelected.size === 0;
}

function toggleUnkCard(fid, card, cb) {
  if (!unkSelectionMode) return;
  if (unkSelected.has(fid)) {
    unkSelected.delete(fid);
    card.classList.remove('selected');
    cb.checked = false;
  } else {
    unkSelected.add(fid);
    card.classList.add('selected');
    cb.checked = true;
  }
  updateSelCount();
}

function selectAllUnknown() {
  document.querySelectorAll('.unk-card[data-fid]').forEach(card => {
    const fid = parseInt(card.dataset.fid);
    unkSelected.add(fid);
    card.classList.add('selected');
    const cb = card.querySelector('.unk-checkbox');
    if (cb) cb.checked = true;
  });
  updateSelCount();
}

function selectNoneUnknown() {
  unkSelected.clear();
  document.querySelectorAll('.unk-card').forEach(c => {
    c.classList.remove('selected');
    const cb = c.querySelector('.unk-checkbox');
    if (cb) cb.checked = false;
  });
  updateSelCount();
}

async function bulkDeleteUnknown() {
  if (unkSelected.size === 0) return;
  if (!confirm(`Delete ${unkSelected.size} selected face record(s)?`)) return;
  const ids = [...unkSelected];
  const r   = await api('/api/unknown-faces/bulk-delete', {method:'POST', body:JSON.stringify({ids})});
  if (r.status === 'ok') {
    toast(r.message, 'success');
    unkSelected.clear();
    updateSelCount();
    loadUnknown(state.unkPage);
    loadDashboard();
  } else toast(r.message, 'error');
}

async function markAllReviewed() {
  if (unkSelected.size === 0) return;
  for (const fid of unkSelected) {
    await api(`/api/unknown-faces/${fid}/review`, {method:'PUT', body:JSON.stringify({notes:'Bulk reviewed'})});
  }
  toast(`${unkSelected.size} record(s) marked reviewed`, 'success');
  unkSelected.clear();
  updateSelCount();
  loadUnknown(state.unkPage);
  loadDashboard();
}
