// ══════════════════════════════════════════════════════════
//  SETTINGS
// ══════════════════════════════════════════════════════════
async function loadSettings() {
  const r = await api('/api/settings');
  if(r.status!=='ok') return;

  const byCategory = {};
  r.data.settings.forEach(s => {
    if(!byCategory[s.category]) byCategory[s.category]=[];
    byCategory[s.category].push(s);
    state.settings[s.key] = s.value;
  });

  ['general','attendance','recognition','compliance'].forEach(cat => {
    const el = $(`settings-${cat}`); if(!el) return;
    el.innerHTML = (byCategory[cat]||[]).map(s=>`
      <div class="form-group mb-3">
        <label>${s.label||s.key}</label>
        <input type="${s.key.includes('time')&&!s.key.includes('timeout')&&!s.key.includes('retention')&&!s.key.includes('length')&&!s.key.includes('attempts')?'time':'text'}"
               id="setting-${s.key}" value="${s.value||''}"
               data-key="${s.key}" data-cat="${cat}"/>
      </div>`).join('');
  });

  // Load theme colour inputs
  const pc = (byCategory['theme']||[]).find(s=>s.key==='primary_color');
  const ac = (byCategory['theme']||[]).find(s=>s.key==='accent_color');
  const ot = (byCategory['theme']||[]).find(s=>s.key==='org_title');
  const on2= (byCategory['general']||[]).find(s=>s.key==='org_name');
  if (pc) { $('theme-primary') && ($('theme-primary').value=$('theme-primary-hex').value=pc.value); }
  if (ac) { $('theme-accent')  && ($('theme-accent').value=$('theme-accent-hex').value=ac.value);  }
  if (ot) { $('theme-org-title') && ($('theme-org-title').value=ot.value); }
  if (on2){ $('theme-org-name')  && ($('theme-org-name').value=on2.value); }
  _updateThemeSwatches();

  // Favicon preview
  const fav = (byCategory['theme']||[]).find(s=>s.key==='favicon_path');
  if (fav && fav.value) {
    const fp = $('favicon-preview');
    if (fp) { fp.src='/'+fav.value; fp.style.display='block'; }
  }

  // Cameras in settings
  const camR = await api('/api/cameras');
  const camEl = $('settings-cameras');
  if(camR.status==='ok') {
    camEl.innerHTML = camR.data.length===0
      ? '<p class="text-sm text-gray">No cameras added yet.</p>'
      : camR.data.map(c=>`
          <div class="flex items-center gap-2 mb-2" style="font-size:13px">
            <span class="flex-1"><strong>${c.name}</strong> <span class="text-gray text-xs">${c.rtsp_url.slice(0,35)}…</span></span>
            <span class="tag ${c.active?'tag-active':'tag-inactive'}">${c.direction}</span>
            <button class="btn btn-outline btn-xs" onclick="openEditCamera(${c.id})">✏</button>
            <button class="btn btn-outline btn-xs" style="color:var(--red)" onclick="delCam(${c.id});loadSettings()">🗑</button>
          </div>`).join('');
  }

  // Logo
  _refreshLogoUI(r.data.settings.find(s=>s.key==='org_logo')?.value || '');

  // Departments
  state.departments = r.data.departments.map(d=>d.name);
  renderDeptList();

  // Sync settings — populate dedicated inputs by data-key
  const syncKeys = ['cloud_api_url','cloud_api_key','site_id','sync_interval','sync_enabled'];
  syncKeys.forEach(k => {
    const el = document.querySelector(`[data-cat="sync"][data-key="${k}"]`);
    if (!el) return;
    const setting = r.data.settings.find(s => s.key === k);
    if (setting) el.value = setting.value || '';
  });
  loadSyncStatus();
}

async function saveSettings(category) {
  const inputs = document.querySelectorAll(`[data-cat="${category}"]`);
  const data = {};
  // Exclude file-path keys managed by dedicated upload endpoints — saving these
  // as empty strings via the generic PUT would silently wipe uploaded files.
  const UPLOAD_KEYS = new Set(['org_logo', 'favicon_path']);
  inputs.forEach(i => {
    if (!UPLOAD_KEYS.has(i.dataset.key)) data[i.dataset.key] = i.value;
  });
  if (!Object.keys(data).length) { toast('Nothing to save', 'info'); return; }
  const r = await api('/api/settings',{method:'PUT',body:JSON.stringify(data)});
  if(r.status==='ok') toast('Settings saved','success');
  else toast(r.message,'error');
  Object.assign(state.settings, data);
}

// ══════════════════════════════════════════════════════════
//  CAMERA MODAL
// ══════════════════════════════════════════════════════════
function openAddCamera() {
  $('modal-cam-title').textContent = 'Add Camera';
  $('cf-id').value='';$('cf-name').value='';$('cf-location').value='';
  $('cf-rtsp').value='';$('cf-direction').value='BOTH';$('cf-active').value='1';
  openModal('modal-camera');
}

async function openEditCamera(id) {
  const r = await api('/api/cameras');
  if(r.status!=='ok') return;
  const c = r.data.find(x=>x.id===id);
  if(!c) return;
  $('modal-cam-title').textContent = 'Edit Camera';
  $('cf-id').value        = c.id;
  $('cf-name').value      = c.name;
  $('cf-location').value  = c.location||'';
  $('cf-rtsp').value      = c.rtsp_url;
  $('cf-direction').value = c.direction;
  $('cf-active').value    = c.active;
  openModal('modal-camera');
}

async function submitCamera() {
  const id   = $('cf-id').value;
  const data = {
    name:$('cf-name').value, rtsp_url:$('cf-rtsp').value,
    location:$('cf-location').value, direction:$('cf-direction').value,
    active:parseInt($('cf-active').value)
  };
  if(!data.name||!data.rtsp_url){toast('Name and RTSP URL required','error');return;}
  const r = id
    ? await api(`/api/cameras/${id}`,{method:'PUT',body:JSON.stringify(data)})
    : await api('/api/cameras',{method:'POST',body:JSON.stringify(data)});
  if(r.status==='ok'||r.status==='ok'){
    toast(id?'Camera updated':'Camera added','success');
    closeModal('modal-camera');
    loadCamGrid();
    if($('settings-cameras')) loadSettings();
  } else toast(r.message,'error');
}

// ══════════════════════════════════════════════════════════
//  ACCOUNT
// ══════════════════════════════════════════════════════════
async function changePassword() {
  const cur  = $('pw-current').value;
  const np   = $('pw-new').value;
  const nc   = $('pw-confirm').value;
  const msg  = $('pw-msg');
  if (!cur || !np || !nc) { msg.innerHTML='<p style="color:var(--red)">All fields required</p>'; return; }
  if (np !== nc) { msg.innerHTML='<p style="color:var(--red)">New passwords do not match</p>'; return; }
  if (np.length < 6) { msg.innerHTML='<p style="color:var(--red)">Min 6 characters</p>'; return; }
  const r = await api('/api/auth/change-password', {method:'POST', body:JSON.stringify({current_password:cur,new_password:np})});
  if (r.status==='ok') {
    msg.innerHTML='<p style="color:var(--green)">✅ Password changed!</p>';
    $('pw-current').value=$('pw-new').value=$('pw-confirm').value='';
    toast('Password updated','success');
  } else {
    msg.innerHTML=`<p style="color:var(--red)">❌ ${r.message}</p>`;
  }
}

// ══════════════════════════════════════════════════════════
//  LOGO UPLOAD
// ══════════════════════════════════════════════════════════
async function uploadLogo(input) {
  const file = input.files[0]; if (!file) return;
  const fd   = new FormData(); fd.append('logo', file);
  const r    = await fetch('/api/settings/logo', {method:'POST', body:fd}).then(x=>x.json());
  if (r.status === 'ok') {
    _refreshLogoUI(r.data.logo_path);
    toast('Logo uploaded', 'success');
  } else toast(r.message, 'error');
}

async function removeLogo() {
  await api('/api/settings', {method:'PUT', body:JSON.stringify({org_logo:''})});
  _refreshLogoUI('');
  toast('Logo removed', 'success');
}

// ══════════════════════════════════════════════════════════
//  SYSTEM USERS (Admin only)
// ══════════════════════════════════════════════════════════
async function loadSysUsers() {
  const r = await api('/api/admin-users');
  if (r.status !== 'ok') { $('sysusers-tbody').innerHTML = `<tr class="empty-row"><td colspan="7">Access denied</td></tr>`; return; }
  const users = r.data;
  const roleTag = {admin:'tag-absent', manager:'tag-late', guard:'tag-in'};
  $('sysusers-tbody').innerHTML = users.length === 0
    ? `<tr class="empty-row"><td colspan="7">No system users</td></tr>`
    : users.map(u => `<tr>
        <td><strong>${u.username}</strong></td>
        <td>${u.full_name || '—'}</td>
        <td><span class="tag ${roleTag[u.role]||'tag-in'}">${u.role}</span></td>
        <td class="text-gray">${u.department || '—'}</td>
        <td class="text-gray">${u.last_login ? fmtTime(u.last_login) : 'Never'}</td>
        <td><span class="tag ${u.active ? 'tag-active' : 'tag-inactive'}">${u.active ? 'Active' : 'Inactive'}</span></td>
        <td>
          <div style="display:flex;gap:4px">
            <button class="btn btn-outline btn-xs" onclick="openEditSysUser(${u.id})">✏</button>
            <button class="btn btn-outline btn-xs" style="color:var(--red)" onclick="deleteSysUser(${u.id})">🗑</button>
          </div>
        </td>
      </tr>`).join('');
}

function openAddSysUser() {
  $('modal-sysuser-title').textContent = 'Add System User';
  $('su-id').value = '';
  $('su-username').value = '';
  $('su-fullname').value = '';
  $('su-password').value = '';
  $('su-role').value = 'manager';
  $('su-dept').value = '';
  $('su-active').value = '1';
  $('su-pw-hint').textContent = '(required)';
  $('su-username').removeAttribute('readonly');
  openModal('modal-sysuser');
}

async function openEditSysUser(id) {
  const r = await api('/api/admin-users');
  if (r.status !== 'ok') return;
  const u = r.data.find(x => x.id === id);
  if (!u) return;
  $('modal-sysuser-title').textContent = 'Edit System User';
  $('su-id').value       = u.id;
  $('su-username').value = u.username;
  $('su-fullname').value = u.full_name || '';
  $('su-password').value = '';
  $('su-role').value     = u.role;
  $('su-dept').value     = u.department || '';
  $('su-active').value   = String(u.active);
  $('su-pw-hint').textContent = '(leave blank to keep)';
  $('su-username').setAttribute('readonly', 'readonly');
  openModal('modal-sysuser');
}

async function submitSysUser() {
  const id       = $('su-id').value;
  const username = $('su-username').value.trim();
  const password = $('su-password').value;
  const role     = $('su-role').value;

  if (!username) { toast('Username required', 'error'); return; }
  if (!id && !password) { toast('Password required for new user', 'error'); return; }
  if (password && password.length < 6) { toast('Password must be at least 6 characters', 'error'); return; }

  const data = {
    username,
    full_name:  $('su-fullname').value.trim(),
    role,
    department: $('su-dept').value.trim(),
    active:     parseInt($('su-active').value),
  };
  if (password) data.password = password;

  const r = id
    ? await api(`/api/admin-users/${id}`, {method:'PUT',  body:JSON.stringify(data)})
    : await api('/api/admin-users',        {method:'POST', body:JSON.stringify(data)});

  if (r.status === 'ok') {
    toast(id ? 'User updated' : 'User created', 'success');
    closeModal('modal-sysuser');
    loadSysUsers();
  } else {
    toast(r.message, 'error');
  }
}

async function deleteSysUser(id) {
  if (!confirm('Delete this system user? This cannot be undone.')) return;
  const r = await api(`/api/admin-users/${id}`, {method:'DELETE'});
  if (r.status === 'ok') { toast('User deleted', 'success'); loadSysUsers(); }
  else toast(r.message, 'error');
}

// ══════════════════════════════════════════════════════════
//  SETTINGS TAB SWITCHER
// ══════════════════════════════════════════════════════════
function switchSettingsTab(tab) {
  ['general','theme','attendance','recognition','cameras','compliance','account','sync'].forEach(t => {
    const btn = $('stab-'+t), panel = $('spanel-'+t);
    if (btn)   btn.classList.toggle('active', t === tab);
    if (panel) panel.classList.toggle('active', t === tab);
  });
}

// ══════════════════════════════════════════════════════════
//  THEME — colour pickers & preset
// ══════════════════════════════════════════════════════════
function syncColorInput(which) {
  // Sync hex text → colour picker
  const hex   = $('theme-'+which+'-hex').value.trim();
  const picker = $('theme-'+which);
  if (/^#[0-9a-fA-F]{6}$/.test(hex)) {
    picker.value = hex;
    _updateThemeSwatches();
    _applyThemeLive();
  }
}

$('theme-primary') && $('theme-primary').addEventListener('input', function() {
  $('theme-primary-hex').value = this.value;
  _updateThemeSwatches(); _applyThemeLive();
});
$('theme-accent') && $('theme-accent').addEventListener('input', function() {
  $('theme-accent-hex').value = this.value;
  _updateThemeSwatches(); _applyThemeLive();
});

function _updateThemeSwatches() {
  const pc = $('theme-primary'), ac = $('theme-accent');
  if (pc) { $('theme-primary-swatch') && ($('theme-primary-swatch').style.background = pc.value); }
  if (ac) { $('theme-accent-swatch')  && ($('theme-accent-swatch').style.background  = ac.value); }
  // Live preview boxes
  $('prev-primary') && ($('prev-primary').style.background = pc ? pc.value : '');
  $('prev-accent')  && ($('prev-accent').style.background  = ac ? ac.value : '');
}

function _applyThemeLive() {
  const pc = $('theme-primary'); const ac = $('theme-accent');
  if (pc) {
    document.documentElement.style.setProperty('--blue', pc.value);
    // Derive blue-dark (darken ~15%)
    document.documentElement.style.setProperty('--blue-dark', _darken(pc.value, 0.15));
    document.documentElement.style.setProperty('--blue-light', _lighten(pc.value, 0.88));
  }
  if (ac) {
    document.documentElement.style.setProperty('--green', ac.value);
    document.documentElement.style.setProperty('--green-light', _lighten(ac.value, 0.88));
  }
}

function _darken(hex, amount) {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.max(0, (n>>16) - Math.round(255*amount));
  const g = Math.max(0, ((n>>8)&0xff) - Math.round(255*amount));
  const b = Math.max(0, (n&0xff) - Math.round(255*amount));
  return '#'+[r,g,b].map(x=>x.toString(16).padStart(2,'0')).join('');
}
function _lighten(hex, amount) {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.min(255, (n>>16) + Math.round(255*amount));
  const g = Math.min(255, ((n>>8)&0xff) + Math.round(255*amount));
  const b = Math.min(255, (n&0xff) + Math.round(255*amount));
  return '#'+[r,g,b].map(x=>x.toString(16).padStart(2,'0')).join('');
}

function applyPreset(primary, accent) {
  const pp = $('theme-primary'), ap = $('theme-accent');
  if (pp) { pp.value = primary; $('theme-primary-hex').value = primary; }
  if (ap) { ap.value = accent;  $('theme-accent-hex').value  = accent; }
  _updateThemeSwatches();
  _applyThemeLive();
}

async function saveTheme() {
  const pc  = $('theme-primary-hex').value || $('theme-primary')?.value;
  const ac  = $('theme-accent-hex').value  || $('theme-accent')?.value;
  const ot  = $('theme-org-title')?.value  || '';
  const on2 = $('theme-org-name')?.value   || '';
  const r   = await api('/api/settings/theme', {
    method: 'PUT',
    body: JSON.stringify({ primary_color: pc, accent_color: ac, org_title: ot, org_name: on2 })
  });
  if (r.status === 'ok') {
    toast('Theme saved ✅', 'success');
    if (ot) document.title = ot;
  } else {
    toast(r.message, 'error');
  }
}

async function uploadFavicon(input) {
  const file = input.files[0]; if (!file) return;
  const fd   = new FormData(); fd.append('favicon', file);
  const r    = await fetch('/api/settings/favicon', {method:'POST', body:fd}).then(x=>x.json());
  const msg  = $('favicon-msg');
  if (r.status === 'ok') {
    const src = '/' + r.data.favicon_path + '?t=' + Date.now();
    const fp  = $('favicon-preview'); if (fp) { fp.src = src; fp.style.display='block'; }
    // Update <link rel="icon">
    let link = document.querySelector("link[rel~='icon']");
    if (!link) { link = document.createElement('link'); link.rel='icon'; document.head.appendChild(link); }
    link.href = src;
    if (msg) msg.innerHTML = '<p style="color:var(--green);font-size:13px">✅ Favicon uploaded</p>';
    toast('Favicon uploaded', 'success');
  } else {
    if (msg) msg.innerHTML = `<p style="color:var(--red);font-size:13px">❌ ${r.message}</p>`;
  }
}

// ══════════════════════════════════════════════════════════
//  HOLIDAY CALENDAR
// ══════════════════════════════════════════════════════════
const HOL_TYPE_STYLE = {
  national:   'background:#fde8e8;color:#c81e1e',
  state:      'background:#fdf6b2;color:#c27803',
  restricted: 'background:#ebf5ff;color:#1a56db',
  office:     'background:#def7ec;color:#057a55',
};
const HOL_TYPE_LABEL = {
  national:'National Holiday', state:'State Holiday',
  restricted:'Restricted Holiday', office:'Office Closure'
};
const DAYS = ['','Mon','Tue','Wed','Thu','Fri','Sat','Sun'];

function initHolidays() {
  const sel = $('hol-year');
  if (sel && !sel.options.length) {
    const yr = new Date().getFullYear();
    for (let y = yr - 1; y <= yr + 2; y++) {
      sel.add(new Option(y, y, y === yr, y === yr));
    }
  }
  loadHolidays();
}

async function loadHolidays() {
  const year = $('hol-year') ? $('hol-year').value : new Date().getFullYear();
  const r = await api(`/api/holidays?year=${year}`);
  if (r.status !== 'ok') return;

  const rows = r.data;
  $('hol-tbody').innerHTML = rows.length === 0
    ? `<tr class="empty-row"><td colspan="5">No holidays for ${year}. Click "Seed 2025 Govt Holidays" to pre-fill.</td></tr>`
    : rows.map(h => {
        const day  = new Date(h.date).getDay(); // 0=Sun
        const isoD = new Date(h.date).getDay() === 0 ? 7 : new Date(h.date).getDay(); // 1=Mon…7=Sun
        const isWE = isoD >= 6;
        return `<tr ${isWE ? 'style="opacity:.55"' : ''}>
          <td><strong>${h.date}</strong></td>
          <td class="text-gray">${DAYS[isoD] || '—'} ${isWE ? '<span class="tag" style="font-size:10px;background:var(--gray-100)">Weekend</span>':''}</td>
          <td>${h.name}</td>
          <td><span class="tag" style="${HOL_TYPE_STYLE[h.type]||''}">${HOL_TYPE_LABEL[h.type]||h.type}</span></td>
          <td class="nav-role-manager">
            <div style="display:flex;gap:4px">
              <button class="btn btn-outline btn-xs" onclick="openEditHoliday(${h.id},'${h.date}','${h.name.replace(/'/g,"\\'")}','${h.type}')">✏</button>
              <button class="btn btn-outline btn-xs" style="color:var(--red)" onclick="deleteHoliday(${h.id})">🗑</button>
            </div>
          </td>
        </tr>`;
      }).join('');

  // Monthly working-day impact grid
  const card = $('hol-summary-card');
  if (card) {
    card.style.display = '';
    const byMonth = {};
    rows.forEach(h => {
      const m = h.date.slice(0,7);
      if (!byMonth[m]) byMonth[m] = 0;
      byMonth[m]++;
    });
    $('hol-monthly-grid').innerHTML = Array.from({length:12}, (_,i)=>{
      const m   = `${year}-${String(i+1).padStart(2,'0')}`;
      const cnt = byMonth[m] || 0;
      const mon = new Date(year,i,1).toLocaleString('en-IN',{month:'short'});
      return `<div style="background:#fff;border-radius:8px;padding:12px;border:1px solid var(--gray-200);text-align:center">
        <div style="font-size:12px;font-weight:600;color:var(--gray-600)">${mon}</div>
        <div style="font-size:20px;font-weight:700;color:${cnt?'var(--red)':'var(--green)'};margin-top:4px">${cnt}</div>
        <div style="font-size:10px;color:var(--gray-400)">${cnt===1?'holiday':cnt===0?'No holidays':'holidays'}</div>
      </div>`;
    }).join('');
  }
}

function openAddHoliday() {
  $('modal-hol-title').textContent = 'Add Holiday';
  $('hf-id').value = '';
  $('hf-date').value = new Date().toISOString().split('T')[0];
  $('hf-name').value = '';
  $('hf-type').value = 'national';
  openModal('modal-holiday');
}

function openEditHoliday(id, date, name, type) {
  $('modal-hol-title').textContent = 'Edit Holiday';
  $('hf-id').value   = id;
  $('hf-date').value = date;
  $('hf-name').value = name;
  $('hf-type').value = type;
  openModal('modal-holiday');
}

async function submitHoliday() {
  const id   = $('hf-id').value;
  const date = $('hf-date').value;
  const name = $('hf-name').value.trim();
  const type = $('hf-type').value;
  if (!date || !name) { toast('Date and name required', 'error'); return; }
  const body = JSON.stringify({date, name, type});
  const r = id
    ? await api(`/api/holidays/${id}`, {method:'PUT',  body})
    : await api('/api/holidays',         {method:'POST', body});
  if (r.status === 'ok') {
    toast(id ? 'Holiday updated' : 'Holiday added', 'success');
    closeModal('modal-holiday');
    loadHolidays();
  } else toast(r.message, 'error');
}

async function deleteHoliday(id) {
  if (!confirm('Delete this holiday?')) return;
  const r = await api(`/api/holidays/${id}`, {method:'DELETE'});
  if (r.status === 'ok') { toast('Deleted', 'success'); loadHolidays(); }
  else toast(r.message, 'error');
}

async function seedIndiaHolidays() {
  // Central Government of India 2025 gazetted holidays
  const holidays = [
    {date:'2025-01-26', name:'Republic Day',                         type:'national'},
    {date:'2025-03-31', name:'Id-ul-Fitr (Eid)',                     type:'national'},
    {date:'2025-04-10', name:'Mahavir Jayanti',                      type:'national'},
    {date:'2025-04-14', name:'Dr. Ambedkar Jayanti',                 type:'national'},
    {date:'2025-04-18', name:'Good Friday',                          type:'national'},
    {date:'2025-05-12', name:'Buddha Purnima',                       type:'national'},
    {date:'2025-06-07', name:'Id-ul-Zuha (Bakrid)',                  type:'national'},
    {date:'2025-07-06', name:'Muharram',                             type:'national'},
    {date:'2025-08-15', name:'Independence Day',                     type:'national'},
    {date:'2025-09-05', name:'Milad-un-Nabi (Prophet\'s Birthday)',  type:'national'},
    {date:'2025-10-02', name:'Gandhi Jayanti / Dussehra',            type:'national'},
    {date:'2025-10-20', name:'Diwali (Deepavali)',                   type:'national'},
    {date:'2025-11-05', name:'Guru Nanak Jayanti',                   type:'national'},
    {date:'2025-12-25', name:'Christmas Day',                        type:'national'},
  ];
  let added = 0;
  for (const h of holidays) {
    const r = await api('/api/holidays', {method:'POST', body: JSON.stringify(h)});
    if (r.status === 'ok') added++;
  }
  toast(`${added} holidays added (duplicates skipped)`, 'success');
  loadHolidays();
}

// ══════════════════════════════════════════════════════════
//  LOGO LOAD in Settings
// ══════════════════════════════════════════════════════════
function _refreshLogoUI(logoPath) {
  const preview = $('logo-preview');
  const placeholder = $('logo-placeholder-icon');
  const removeBtn = $('btn-remove-logo');
  const hl = $('header-logo');
  if (logoPath) {
    const src = '/' + logoPath + '?t=' + Date.now();
    if (preview) { preview.src = src; preview.style.display = 'block'; }
    if (placeholder) placeholder.style.display = 'none';
    if (removeBtn) removeBtn.style.display = '';
    if (hl) { hl.src = src; hl.style.display = 'block'; }
  } else {
    if (preview) { preview.src = ''; preview.style.display = 'none'; }
    if (placeholder) placeholder.style.display = 'flex';
    if (removeBtn) removeBtn.style.display = 'none';
    if (hl) { hl.src = ''; hl.style.display = 'none'; }
  }
}
