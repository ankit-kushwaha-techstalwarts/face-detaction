// ══════════════════════════════════════════════════════════
//  REPORTS
// ══════════════════════════════════════════════════════════
function initReports() {
  const today = new Date().toISOString().split('T')[0];
  const month = new Date().toISOString().slice(0,7);
  // Attendance tab
  if(!$('rep-from').value)   $('rep-from').value   = today;
  if(!$('rep-to').value)     $('rep-to').value     = today;
  if(!$('rep-month').value)  $('rep-month').value  = month;
  // Late / Absent / Hours / Employee-history tabs — default to last 30 days
  const d30ago = new Date(); d30ago.setDate(d30ago.getDate() - 30);
  const prior  = d30ago.toISOString().split('T')[0];
  ['late','absent','hours'].forEach(p => {
    const f = $(p+'-from'), t = $(p+'-to');
    if(f && !f.value) f.value = prior;
    if(t && !t.value) t.value = today;
  });
  const ehf = $('emp-hist-from'), eht = $('emp-hist-to');
  if(ehf && !ehf.value) ehf.value = prior;
  if(eht && !eht.value) eht.value = today;
  // Populate all report dept selects
  ['late-dept','absent-dept','hours-dept','rep-dept','monthly-dept'].forEach(id => {
    const el = $(id); if(!el) return;
    const prev = el.value;
    el.innerHTML = '<option value="">All</option>' +
      state.departments.map(d=>`<option value="${d}">${d}</option>`).join('');
    el.value = prev;
  });
  // Populate employee-history dropdown
  _loadEmpHistorySelect();
  loadDepartments();
}

async function _loadEmpHistorySelect() {
  const sel = $('emp-hist-uid'); if (!sel) return;
  const r   = await api('/api/users?per_page=500&active=1');
  const users = (r.status==='ok') ? (r.data.users || r.data) : [];
  sel.innerHTML = '<option value="">Select employee…</option>' +
    users.map(u=>`<option value="${u.id}">${u.name} (${u.emp_id})</option>`).join('');
}

// ── Report-tab switcher ────────────────────────────────────
function switchReportTab(tab) {
  ['attendance','monthly','late','absent','hours','employee'].forEach(t => {
    $('rtab-'+t).classList.toggle('active', t===tab);
    $('rpanel-'+t).classList.toggle('active', t===tab);
  });
}

async function loadReport() {
  const from=$('rep-from').value, to=$('rep-to').value, dept=$('rep-dept').value;
  const params = new URLSearchParams({from,to});
  if(dept) params.set('department',dept);
  const r = await api(`/api/reports/attendance?${params}`);
  if(r.status!=='ok') return;
  const workStart = state.settings['work_start']||'09:00';
  const lateMins  = parseInt(state.settings['late_threshold']||15);
  $('report-tbody').innerHTML = r.data.length===0
    ? `<tr class="empty-row"><td colspan="7">No records</td></tr>`
    : r.data.map(row=>{
        let status='Absent', cls='absent';
        if(row.first_in) {
          try {
            const fi = new Date(row.first_in);
            const ws = new Date(`${row.date}T${workStart}:00`);
            ws.setMinutes(ws.getMinutes()+lateMins);
            status = fi>ws?'Late':'Present';
            cls    = fi>ws?'late':'in';
          } catch{ status='Present'; cls='in'; }
        }
        return `<tr>
          <td>${row.emp_id}</td>
          <td>${row.name}</td>
          <td>${row.department||'—'}</td>
          <td>${row.date||'—'}</td>
          <td>${row.first_in?fmtTime(row.first_in):'—'}</td>
          <td>${row.last_out?fmtTime(row.last_out):'—'}</td>
          <td><span class="tag tag-${cls}">${status}</span></td>
        </tr>`;
      }).join('');
}

async function loadMonthly() {
  const month = $('rep-month').value;
  const dept  = $('monthly-dept') ? $('monthly-dept').value : '';
  const params = new URLSearchParams({month});
  if (dept) params.set('department', dept);
  const r = await api(`/api/reports/monthly-summary?${params}`);
  if (r.status !== 'ok') return;

  // Backend returns { rows, working_days, holidays, month }
  const rows        = r.data.rows || r.data;         // backward compat
  const wdays       = r.data.working_days ?? '—';
  const holidays    = r.data.holidays    || [];

  // Meta bar
  const meta = $('monthly-meta');
  if (meta) {
    meta.style.display = '';
    $('mm-wdays').textContent = wdays;
    $('mm-hdays').textContent = holidays.length;
    $('mm-emps').textContent  = rows.length;
    $('mm-holidays-list').innerHTML = holidays.length
      ? '<strong>Holidays:</strong> ' + holidays.map(h=>`<span class="tag tag-late" style="margin:2px">${h}</span>`).join('')
      : '';
  }

  $('monthly-tbody').innerHTML = rows.length === 0
    ? `<tr class="empty-row"><td colspan="9">No data</td></tr>`
    : rows.map(u => {
        const pct   = u.attendance_pct ?? (wdays ? Math.round((u.days_present/wdays)*100) : 0);
        const color = pct >= 90 ? 'var(--green)' : pct >= 75 ? 'var(--orange)' : 'var(--red)';
        return `<tr>
          <td>${u.emp_id}</td>
          <td>${u.name}</td>
          <td>${u.department||'—'}</td>
          <td class="text-gray">${u.designation||'—'}</td>
          <td style="text-align:center">${wdays}</td>
          <td style="text-align:center;font-weight:600;color:var(--green)">${u.days_present}</td>
          <td style="text-align:center;color:var(--red)">${u.days_absent ?? (wdays !== '—' ? Math.max(0,wdays-u.days_present) : '—')}</td>
          <td style="text-align:center;color:var(--orange)">${u.days_late ?? '—'}</td>
          <td>
            <div class="flex items-center gap-2">
              <div class="progress" style="flex:1;max-width:80px"><div class="progress-bar" style="width:${pct}%;background:${color}"></div></div>
              <span class="text-xs" style="color:${color};font-weight:600">${pct}%</span>
            </div>
          </td>
        </tr>`;
      }).join('');
}

function exportMonthly() {
  const month = $('rep-month').value;
  const dept  = $('monthly-dept') ? $('monthly-dept').value : '';
  const p = new URLSearchParams({month, format:'csv'});
  if (dept) p.set('department', dept);
  window.location.href = `/api/reports/monthly-summary?${p}`;
}

function exportReport() {
  const from=$('rep-from').value, to=$('rep-to').value, dept=$('rep-dept').value;
  const params = new URLSearchParams({from,to,format:'csv'});
  if(dept) params.set('department',dept);
  window.location.href = `/api/reports/attendance?${params}`;
}

// ══════════════════════════════════════════════════════════
//  LATE ARRIVALS REPORT
// ══════════════════════════════════════════════════════════
async function loadLateReport() {
  const from = $('late-from').value, to = $('late-to').value, dept = $('late-dept').value;
  if (!from || !to) { toast('Select date range', 'error'); return; }
  const params = new URLSearchParams({from, to});
  if (dept) params.set('department', dept);
  const r = await api(`/api/reports/late-arrivals?${params}`);
  if (r.status !== 'ok') { toast(r.message, 'error'); return; }
  const rows = r.data;
  const totalMins = rows.reduce((s, x) => s + (x.minutes_late || 0), 0);
  $('late-summary').innerHTML = rows.length
    ? `<span class="tag tag-late">${rows.length} late arrival${rows.length !== 1 ? 's' : ''}</span>
       <span class="text-sm text-gray" style="margin-left:8px">Avg late: ${rows.length ? Math.round(totalMins/rows.length) : 0} min</span>`
    : '<span class="tag tag-in">No late arrivals in this period 🎉</span>';
  $('late-tbody').innerHTML = rows.length === 0
    ? `<tr class="empty-row"><td colspan="7">No late arrivals</td></tr>`
    : rows.map(r => `<tr>
        <td>${r.date || '—'}</td>
        <td>${r.emp_id}</td>
        <td>${r.name}</td>
        <td>${r.department || '—'}</td>
        <td>${r.first_in ? fmtTime(r.first_in) : '—'}</td>
        <td>${r.deadline || '—'}</td>
        <td><span class="tag tag-late">${r.minutes_late} min</span></td>
      </tr>`).join('');
}

function exportLateReport() {
  const from = $('late-from').value, to = $('late-to').value, dept = $('late-dept').value;
  const p = new URLSearchParams({from, to, format:'csv', type:'late'});
  if (dept) p.set('department', dept);
  window.location.href = `/api/reports/late-arrivals?${p}`;
}

// ══════════════════════════════════════════════════════════
//  ABSENTEES REPORT
// ══════════════════════════════════════════════════════════
async function loadAbsentReport() {
  const from = $('absent-from').value, to = $('absent-to').value, dept = $('absent-dept').value;
  if (!from || !to) { toast('Select date range', 'error'); return; }
  const params = new URLSearchParams({from, to});
  if (dept) params.set('department', dept);
  const r = await api(`/api/reports/absentees?${params}`);
  if (r.status !== 'ok') { toast(r.message, 'error'); return; }
  const rows = r.data;
  $('absent-summary').innerHTML = rows.length
    ? `<span class="tag tag-absent">${rows.length} absence record${rows.length !== 1 ? 's' : ''}</span>`
    : '<span class="tag tag-in">Full attendance in this period 🎉</span>';
  $('absent-tbody').innerHTML = rows.length === 0
    ? `<tr class="empty-row"><td colspan="5">No absences found</td></tr>`
    : rows.map(r => `<tr>
        <td>${r.date || '—'}</td>
        <td>${r.emp_id}</td>
        <td>${r.name}</td>
        <td>${r.department || '—'}</td>
        <td class="text-gray">${r.designation || '—'}</td>
      </tr>`).join('');
}

function exportAbsentReport() {
  const from = $('absent-from').value, to = $('absent-to').value, dept = $('absent-dept').value;
  const p = new URLSearchParams({from, to, format:'csv'});
  if (dept) p.set('department', dept);
  window.location.href = `/api/reports/absentees?${p}`;
}

// ══════════════════════════════════════════════════════════
//  WORKING HOURS REPORT
// ══════════════════════════════════════════════════════════
async function loadHoursReport() {
  const from = $('hours-from').value, to = $('hours-to').value, dept = $('hours-dept').value;
  if (!from || !to) { toast('Select date range', 'error'); return; }
  const params = new URLSearchParams({from, to});
  if (dept) params.set('department', dept);
  const r = await api(`/api/reports/working-hours?${params}`);
  if (r.status !== 'ok') { toast(r.message, 'error'); return; }
  const workEnd = state.settings['work_end'] || '18:00';
  $('hours-tbody').innerHTML = r.data.length === 0
    ? `<tr class="empty-row"><td colspan="9">No records found</td></tr>`
    : r.data.map(row => {
        const hrs    = row.hours_worked != null ? row.hours_worked.toFixed(1) : '—';
        const expH   = _timeToHours(workEnd) - _timeToHours(state.settings['work_start'] || '09:00');
        const ot     = row.hours_worked != null ? Math.max(0, row.hours_worked - expH).toFixed(1) : '—';
        const tag    = !row.first_in ? 'absent' : row.hours_worked >= expH - 0.5 ? 'in' : 'late';
        const label  = !row.first_in ? 'Absent' : row.hours_worked >= expH - 0.5 ? 'Full Day' : 'Short';
        return `<tr>
          <td>${row.date || '—'}</td>
          <td>${row.emp_id}</td>
          <td>${row.name}</td>
          <td>${row.department || '—'}</td>
          <td>${row.first_in  ? fmtTime(row.first_in)  : '—'}</td>
          <td>${row.last_out  ? fmtTime(row.last_out)  : '—'}</td>
          <td><strong>${hrs}</strong> hrs</td>
          <td>${ot !== '—' && parseFloat(ot) > 0 ? `<span class="tag tag-late">+${ot}h</span>` : '—'}</td>
          <td><span class="tag tag-${tag}">${label}</span></td>
        </tr>`;
      }).join('');
}

function _timeToHours(t) {
  if (!t) return 0;
  const [h, m] = t.split(':').map(Number);
  return h + (m || 0) / 60;
}

function exportHoursReport() {
  const from = $('hours-from').value, to = $('hours-to').value, dept = $('hours-dept').value;
  const p = new URLSearchParams({from, to, format:'csv'});
  if (dept) p.set('department', dept);
  window.location.href = `/api/reports/working-hours?${p}`;
}

// ══════════════════════════════════════════════════════════
//  EMPLOYEE HISTORY REPORT
// ══════════════════════════════════════════════════════════
async function loadEmpHistory() {
  const uid  = $('emp-hist-uid').value;
  const from = $('emp-hist-from').value;
  const to   = $('emp-hist-to').value;
  if (!uid) { toast('Select an employee', 'error'); return; }
  if (!from || !to) { toast('Select date range', 'error'); return; }

  const params = new URLSearchParams({user_id: uid, from, to});
  const r = await api(`/api/reports/employee-history?${params}`);
  if (r.status !== 'ok') { toast(r.message, 'error'); return; }

  const d = r.data;
  const emp = d.employee || {};   // backend returns 'employee' key

  // Profile card
  const profile = $('emp-hist-profile');
  profile.style.display = '';
  $('ehp-photo').src    = emp.photo_path ? '/' + emp.photo_path : '';
  $('ehp-name').textContent = emp.name || '—';
  $('ehp-meta').textContent = `${emp.emp_id || '—'} · ${emp.department || '—'} · ${emp.designation || '—'}`;
  const daysPresent   = d.total_days_present || (d.daily || []).length;
  const totalPunches  = (d.records || []).length;
  $('ehp-summary').textContent =
    `${daysPresent} day${daysPresent !== 1 ? 's' : ''} present · ${totalPunches} punch record${totalPunches !== 1 ? 's' : ''}`;

  // Daily summary table — backend field: total_punches (not punch_count)
  $('emp-hist-daily').innerHTML = (d.daily || []).length === 0
    ? `<tr class="empty-row"><td colspan="4">No daily records</td></tr>`
    : d.daily.map(day => `<tr>
        <td>${day.date}</td>
        <td>${day.first_in  ? fmtTime(day.first_in)  : '—'}</td>
        <td>${day.last_out  ? fmtTime(day.last_out)  : '—'}</td>
        <td>${day.total_punches ?? day.punch_count ?? '—'}</td>
      </tr>`).join('');

  // All punch records
  $('emp-hist-records').innerHTML = (d.records || []).length === 0
    ? `<tr class="empty-row"><td colspan="5">No punch records</td></tr>`
    : d.records.map(rec => `<tr>
        <td>${fmtTime(rec.punch_time)}</td>
        <td><span class="tag tag-${rec.punch_type.toLowerCase()}">${rec.punch_type}</span></td>
        <td class="text-gray">${rec.camera_name || '—'}</td>
        <td>${rec.confidence ? rec.confidence.toFixed(1)+'%' : '—'}</td>
        <td>${rec.snapshot_path
          ? `<button class="btn btn-outline btn-xs" onclick="viewSnap('${rec.snapshot_path}','${emp.name || ''}')">👁</button>`
          : '—'}</td>
      </tr>`).join('');
}
