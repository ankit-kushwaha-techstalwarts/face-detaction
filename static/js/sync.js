// ══════════════════════════════════════════════════════════
//  CLOUD SYNC
// ══════════════════════════════════════════════════════════
async function loadSyncStatus() {
  // Unsynced record counts
  const sc = await api('/api/sync/local-status');
  if (sc.status === 'ok') {
    const el1 = $('sync-pending-att'); if(el1) el1.textContent = sc.data.pending_attendance ?? '—';
    const el2 = $('sync-pending-unk'); if(el2) el2.textContent = sc.data.pending_unknown ?? '—';
  }
  // Last push/pull timestamps from sync_log
  const sl = await api('/api/sync/log');
  if (sl.status === 'ok') {
    const pushRows = sl.data.filter(x => x.direction === 'PUSH' && x.status === 'ok');
    const pullRows = sl.data.filter(x => x.direction === 'PULL' && x.status === 'ok');
    const lp = $('sync-last-push'); if(lp) lp.textContent = (pushRows[0]?.synced_at || '—').slice(0,16);
    const ll = $('sync-last-pull'); if(ll) ll.textContent = (pullRows[0]?.synced_at || '—').slice(0,16);
    // Populate log list
    const el = $('sync-log-list');
    if (el && sl.data.length) {
      el.innerHTML = sl.data.slice(0,20).map(row => {
        const icon = row.status === 'ok' ? '✅' : '❌';
        return `<div style="padding:4px 0;border-bottom:1px solid var(--gray-100);display:flex;gap:6px;align-items:baseline">
          <span>${icon}</span>
          <span style="flex:1">${row.direction} ${row.entity} · <strong>${row.records}</strong></span>
          <span style="color:var(--gray-400);white-space:nowrap">${(row.synced_at||'').slice(0,16)}</span>
        </div>`;
      }).join('');
    } else if (el) {
      el.innerHTML = '<p class="text-xs text-gray">No sync events yet.</p>';
    }
  }
}

function loadSyncLog() {
  loadSyncStatus();  // loadSyncStatus now handles both counts and log list
}

async function testSyncConnection() {
  const msg = $('sync-test-msg');
  if (msg) msg.innerHTML = '<span style="color:var(--gray-400)">Testing…</span>';
  const r = await api('/api/sync/test-connection', {method:'POST'});
  if (msg) {
    msg.innerHTML = r.status === 'ok'
      ? `<span style="color:var(--green)">✅ ${r.message}</span>`
      : `<span style="color:var(--red)">❌ ${r.message}</span>`;
  }
}
