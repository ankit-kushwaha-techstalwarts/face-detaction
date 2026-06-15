// ══════════════════════════════════════════════════════════
//  INIT
// ══════════════════════════════════════════════════════════
(async function init() {
  const me = await api('/api/auth/me');
  if (me.status === 'ok') {
    $('header-user').textContent = '👤 ' + (me.data.full_name || me.data.username);
    applyRoleNav(me.data.role || 'guard');
    state.role = me.data.role || 'guard';
  }
  await loadDepartments();
  await loadDashboard();
  setInterval(() => {
    if($('page-dashboard').classList.contains('active')) loadDashboard();
  }, 60000);
})();

// ══════════════════════════════════════════════════════════
//  SESSION TIMEOUT (auto-logout on idle)
// ══════════════════════════════════════════════════════════
(function initSessionTimeout() {
  let _timer = null;
  let _mins  = 0;

  function resetTimer() {
    if (!_mins) return;
    clearTimeout(_timer);
    _timer = setTimeout(async () => {
      toast('Session expired — logging out…', 'info');
      await new Promise(r => setTimeout(r, 1500));
      await api('/api/auth/logout', {method:'POST'});
      window.location.href = '/login';
    }, _mins * 60 * 1000);
  }

  // Load timeout from settings after page is ready
  window.addEventListener('load', async () => {
    const r = await api('/api/settings');
    if (r.status === 'ok') {
      const st = r.data.settings.find(s => s.key === 'session_timeout');
      _mins = st ? parseInt(st.value) : 30;
      if (_mins > 0) {
        ['mousemove','keydown','click','scroll','touchstart'].forEach(ev =>
          document.addEventListener(ev, resetTimer, {passive:true})
        );
        resetTimer();
      }
    }
  });
})();

// Apply saved theme colours on page load
(async function applyStoredTheme() {
  const r = await api('/api/settings');
  if (r.status !== 'ok') return;
  const smap = {};
  r.data.settings.forEach(s => smap[s.key] = s.value);
  if (smap.primary_color) {
    document.documentElement.style.setProperty('--blue', smap.primary_color);
    document.documentElement.style.setProperty('--blue-dark',  _darken(smap.primary_color, 0.15));
    document.documentElement.style.setProperty('--blue-light', _lighten(smap.primary_color, 0.88));
  }
  if (smap.accent_color) {
    document.documentElement.style.setProperty('--green', smap.accent_color);
    document.documentElement.style.setProperty('--green-light', _lighten(smap.accent_color, 0.88));
  }
  if (smap.org_title) document.title = smap.org_title;
  if (smap.favicon_path) {
    let link = document.querySelector("link[rel~='icon']");
    if (!link) { link = document.createElement('link'); link.rel='icon'; document.head.appendChild(link); }
    link.href = '/' + smap.favicon_path + '?t=' + Date.now();
  }
})();
