// ══════════════════════════════════════════════════════════
//  STATE
// ══════════════════════════════════════════════════════════
const state = {
  attPage: 1, unkPage: 1, unkPersonsPage: 1,
  departments: [],
  settings: {},
  users: []
};

// ══════════════════════════════════════════════════════════
//  UTILS
// ══════════════════════════════════════════════════════════
const $ = id => document.getElementById(id);
const fmt = (v, fb='-') => v || fb;

async function api(url, opts={}) {
  try {
    const r = await fetch(url, {
      headers: {'Content-Type':'application/json'},
      ...opts
    });
    return await r.json();
  } catch(e) {
    return {status:'error', message:e.message};
  }
}

function toast(msg, type='info') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<span>${type==='success'?'✅':type==='error'?'❌':'ℹ️'}</span> ${msg}`;
  $('toast-container').appendChild(el);
  setTimeout(() => el.remove(), 3800);
}

function openModal(id) { $(id).classList.add('open') }
function closeModal(id) { $(id).classList.remove('open') }

// The server stores naive "YYYY-MM-DD HH:MM:SS" timestamps that are actually
// UTC (server clock runs in UTC). Treat them as UTC so they can be displayed
// in IST regardless of the viewer's browser timezone.
function toUTCDate(s) {
  if (/Z|[+-]\d{2}:\d{2}$/.test(s)) return new Date(s);
  return new Date(s.replace(' ', 'T') + 'Z');
}
function fmtTime(s) {
  if (!s) return '-';
  try { return toUTCDate(s).toLocaleString('en-IN',{hour12:true,hour:'2-digit',minute:'2-digit',day:'2-digit',month:'short',timeZone:'Asia/Kolkata'}); }
  catch { return s; }
}
function fmtDate(s) {
  if (!s) return '-';
  try { return toUTCDate(s).toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric',timeZone:'Asia/Kolkata'}); }
  catch { return s; }
}

// ── Clock ──────────────────────────────────────────────────
setInterval(() => {
  $('clock').textContent = new Date().toLocaleString('en-IN',{
    weekday:'short',day:'2-digit',month:'short',
    hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:true,
    timeZone:'Asia/Kolkata'
  });
}, 1000);

// ── Nav ────────────────────────────────────────────────────
function navTo(page) {
  const link = document.querySelector(`.nav-link[data-page="${page}"]`);
  if (link) link.click();
}

document.querySelectorAll('.nav-link').forEach(a => {
  a.addEventListener('click', e => {
    e.preventDefault();
    const page = a.dataset.page;
    document.querySelectorAll('.nav-link').forEach(x=>x.classList.remove('active'));
    a.classList.add('active');
    document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
    $('page-'+page).classList.add('active');
    const titles = {
      dashboard:'Dashboard', live:'Live Cameras', inout:'In/Out Records',
      today:"Today's Summary", unknown:'Unknown Faces', users:'User Enrollment',
      reports:'Reports', settings:'Settings', sysusers:'System Users',
      holidays:'Holiday Calendar', audit:'Audit Log'
    };
    $('page-title').textContent = titles[page] || page;
    if (page==='dashboard')   loadDashboard();
    if (page==='inout')       { initAttFilters(); loadAttendance(); }
    if (page==='today')       { initTodayFilters(); loadTodaySummary(); }
    if (page==='users')       { initUserFilters(); loadUsers(); }
    if (page==='unknown')     { loadUnknownDetectionToggle(); loadUnknown(); }
    if (page==='live')        loadCamGrid();
    if (page==='reports')     initReports();
    if (page==='settings')    loadSettings();
    if (page==='sysusers')    loadSysUsers();
    if (page==='holidays')    initHolidays();
    if (page==='audit')       initAuditLog();
  });
});

// ── Role-based nav visibility ──────────────────────────────
function applyRoleNav(role) {
  const isAdmin = role === 'admin';
  const isMgr   = role === 'admin' || role === 'manager';
  document.querySelectorAll('.nav-role-admin').forEach(el   => el.style.display = isAdmin ? '' : 'none');
  document.querySelectorAll('.nav-role-manager').forEach(el => el.style.display = isMgr   ? '' : 'none');
  // Role badge colours
  const badge = $('role-badge');
  const colours = {admin:'background:#fde8e8;color:#c81e1e', manager:'background:#fdf6b2;color:#c27803', guard:'background:var(--green-light);color:var(--green)'};
  badge.setAttribute('style', `font-size:11px;padding:2px 10px;border-radius:20px;font-weight:600;${colours[role]||''}`);
  badge.textContent = role ? role.charAt(0).toUpperCase()+role.slice(1) : '';
}

// ══════════════════════════════════════════════════════════
//  DEPARTMENTS
// ══════════════════════════════════════════════════════════
async function loadDepartments() {
  const r = await api('/api/departments');
  if (r.status==='ok') {
    state.departments = r.data;
    document.querySelectorAll('.dept-sel').forEach(sel => {
      const val = sel.value;
      sel.innerHTML = '<option value="">All Departments</option>' +
        r.data.map(d=>`<option value="${d}">${d}</option>`).join('');
      sel.value = val;
    });
    ['uf-department','rep-dept','today-dept','att-dept','user-dept-filter'].forEach(id => {
      const el = $(id); if (!el) return;
      const prev = el.value;
      if (id==='uf-department') {
        el.innerHTML = '<option value="">Select…</option>' + r.data.map(d=>`<option value="${d}">${d}</option>`).join('');
      } else {
        el.innerHTML = '<option value="">All Departments</option>' + r.data.map(d=>`<option value="${d}">${d}</option>`).join('');
      }
      el.value = prev;
    });
    renderDeptList();
  }
}

function renderDeptList() {
  const el = $('settings-depts'); if (!el) return;
  el.innerHTML = state.departments.map(d =>
    `<div class="flex items-center gap-2 mb-2"><span class="flex-1 text-sm">${d}</span></div>`
  ).join('');
}

async function addDept() {
  const name = $('new-dept').value.trim();
  if (!name) return;
  const r = await api('/api/departments', {method:'POST', body:JSON.stringify({name})});
  if (r.status==='ok') { $('new-dept').value=''; await loadDepartments(); toast('Department added','success'); }
  else toast(r.message,'error');
}

// ══════════════════════════════════════════════════════════
//  PAGINATION (shared by attendance / unknown faces / users / audit)
// ══════════════════════════════════════════════════════════
function renderPagination(containerId, current, total, fn) {
  const el = $(containerId);
  if(total<=1){el.innerHTML='';return;}
  let html = '';
  for(let i=1;i<=total;i++) {
    if(i===1||i===total||Math.abs(i-current)<=1)
      html += `<button class="${i===current?'active':''}" onclick="${fn.name}(${i})">${i}</button>`;
    else if(Math.abs(i-current)===2)
      html += `<span style="padding:5px 4px;color:var(--gray-400)">…</span>`;
  }
  el.innerHTML = html;
}

// ══════════════════════════════════════════════════════════
//  AUTH
// ══════════════════════════════════════════════════════════
async function doLogout() {
  await api('/api/auth/logout', {method:'POST'});
  window.location.href = '/login';
}
