// ══════════════════════════════════════════════════════════
//  ATTENDANCE
// ══════════════════════════════════════════════════════════
function initAttFilters() {
  const today = new Date().toISOString().split('T')[0];
  if (!$('att-from').value) $('att-from').value = today;
  if (!$('att-to').value)   $('att-to').value   = today;
  loadDepartments();
}

async function loadAttendance(page=1) {
  state.attPage = page;
  const from   = $('att-from').value;
  const to     = $('att-to').value;
  const dept   = $('att-dept').value;
  const type   = $('att-type').value;
  const search = $('att-search').value;
  const params = new URLSearchParams({from,to,page,per_page:30});
  if(dept)   params.set('department',dept);
  if(type)   params.set('punch_type',type);

  const r = await api(`/api/attendance?${params}`);
  if(r.status!=='ok') return;

  let rows = r.data.records;
  if(search) {
    const q = search.toLowerCase();
    rows = rows.filter(x=>x.name.toLowerCase().includes(q)||x.emp_id.toLowerCase().includes(q));
  }

  $('att-tbody').innerHTML = rows.length===0
    ? `<tr class="empty-row"><td colspan="9">No records found</td></tr>`
    : rows.map(a=>`<tr>
        <td>${a.name}</td>
        <td class="text-gray">${a.emp_id}</td>
        <td>${a.department||'—'}</td>
        <td><span class="tag tag-${a.punch_type.toLowerCase()}">${a.punch_type}</span></td>
        <td>${fmtTime(a.punch_time)}</td>
        <td class="text-gray">${a.camera_name||'—'}</td>
        <td>${a.confidence?a.confidence.toFixed(1)+'%':'—'}</td>
        <td>${a.snapshot_path
          ? `<button class="btn btn-outline btn-xs" onclick="viewSnap('${a.snapshot_path}','${a.name} — ${fmtTime(a.punch_time)}')">👁 View</button>`
          : '—'}</td>
        <td><button class="btn btn-outline btn-xs" style="color:var(--red)" onclick="delAtt(${a.id})">🗑</button></td>
      </tr>`).join('');

  // Pagination
  renderPagination('att-pagination', page, r.data.pages, loadAttendance);
}

async function delAtt(id) {
  if(!confirm('Delete this record?')) return;
  await api(`/api/attendance/${id}`,{method:'DELETE'});
  toast('Record deleted','success');
  loadAttendance(state.attPage);
}

async function exportAttendance() {
  const from=$('att-from').value, to=$('att-to').value, dept=$('att-dept').value;
  const params = new URLSearchParams({from,to,format:'csv'});
  if(dept) params.set('department',dept);
  window.location.href = `/api/reports/attendance?${params}`;
}

function openManualAttModal() {
  $('manual-user').innerHTML = '<option value="">Select employee…</option>' +
    state.users.map(u=>`<option value="${u.id}">${u.name} (${u.emp_id})</option>`).join('');
  openModal('modal-manual');
}

async function submitManual() {
  const uid = $('manual-user').value;
  const type = $('manual-type').value;
  if(!uid){toast('Select employee','error');return;}
  const r = await api('/api/attendance/manual',{method:'POST',body:JSON.stringify({user_id:uid,punch_type:type})});
  if(r.status==='ok'){toast('Attendance logged','success');closeModal('modal-manual');loadAttendance();}
  else toast(r.message,'error');
}

function viewSnap(path, info) {
  $('snapshot-img').src = '/'+path;
  $('snapshot-info').textContent = info;
  openModal('modal-snapshot');
}

// ══════════════════════════════════════════════════════════
//  TODAY'S SUMMARY
// ══════════════════════════════════════════════════════════
function initTodayFilters() { loadDepartments(); }

async function loadTodaySummary() {
  const dept = $('today-dept').value;
  const params = dept ? `?department=${dept}` : '';
  const r = await api(`/api/attendance/today-summary${params}`);
  if(r.status!=='ok') return;
  const workStart = state.settings['work_start'] || '09:00';
  const lateMins  = parseInt(state.settings['late_threshold']||15);
  $('today-tbody').innerHTML = r.data.length===0
    ? `<tr class="empty-row"><td colspan="7">No data</td></tr>`
    : r.data.map(u => {
        let status, cls;
        if(!u.first_in){status='Absent';cls='absent';}
        else {
          try {
            const d = new Date().toISOString().split('T')[0];
            const fi = new Date(u.first_in);
            const ws = new Date(`${d}T${workStart}:00`);
            ws.setMinutes(ws.getMinutes()+lateMins);
            status = fi > ws ? 'Late' : 'Present';
            cls    = fi > ws ? 'late' : 'in';
          } catch { status='Present'; cls='in'; }
        }
        return `<tr>
          <td>${u.emp_id}</td>
          <td>${u.name}</td>
          <td>${u.department||'—'}</td>
          <td class="text-gray">${u.designation||'—'}</td>
          <td>${u.first_in ? fmtTime(u.first_in) : '—'}</td>
          <td>${u.last_out ? fmtTime(u.last_out) : '—'}</td>
          <td><span class="tag tag-${cls}">${status}</span></td>
        </tr>`;
      }).join('');
}

async function exportToday() {
  const dept = $('today-dept').value;
  const params = new URLSearchParams({from:new Date().toISOString().split('T')[0],to:new Date().toISOString().split('T')[0],format:'csv'});
  if(dept) params.set('department',dept);
  window.location.href = `/api/reports/attendance?${params}`;
}
