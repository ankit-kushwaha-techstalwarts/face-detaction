// ══════════════════════════════════════════════════════════
//  AUDIT LOG
// ══════════════════════════════════════════════════════════
function initAuditLog() {
  const today = new Date().toISOString().split('T')[0];
  const week  = new Date(Date.now() - 7*86400000).toISOString().split('T')[0];
  if (!$('audit-from').value) $('audit-from').value = week;
  if (!$('audit-to').value)   $('audit-to').value   = today;
  loadAuditLog();
}

async function loadAuditLog(page = 1) {
  state.auditPage = page;
  const from = $('audit-from').value, to = $('audit-to').value;
  const user = $('audit-user').value;
  const params = new URLSearchParams({from, to, page, per_page: 50});
  if (user) params.set('username', user);
  const r = await api(`/api/audit-log?${params}`);
  if (r.status !== 'ok') {
    $('audit-tbody').innerHTML = `<tr class="empty-row"><td colspan="7">Access denied — admin only</td></tr>`;
    return;
  }

  // Populate user filter dropdown
  const sel = $('audit-user');
  if (sel && r.data.users) {
    const prev = sel.value;
    sel.innerHTML = '<option value="">All Users</option>' +
      r.data.users.map(u=>`<option value="${u}">${u}</option>`).join('');
    sel.value = prev;
  }

  const ACTION_STYLE = {
    'LOGIN':         'tag-in',
    'LOGOUT':        '',
    'LOGIN_FAILED':  'tag-absent',
    'CHANGE_PASSWORD':'tag-late',
    'ADD_HOLIDAY':   'tag-in',
    'DELETE_HOLIDAY':'tag-absent',
    'UPLOAD_FAVICON':'',
    'SAVE_THEME':    '',
  };

  $('audit-tbody').innerHTML = r.data.records.length === 0
    ? `<tr class="empty-row"><td colspan="7">No audit records found</td></tr>`
    : r.data.records.map(a => {
        const cls = ACTION_STYLE[a.action] || (a.action.includes('DELETE')||a.action.includes('FAIL')?'tag-absent':a.action.includes('EDIT')?'tag-late':'');
        return `<tr>
          <td style="white-space:nowrap">${fmtTime(a.created_at)}</td>
          <td><strong>${a.username}</strong></td>
          <td><span class="tag" style="background:var(--gray-100);color:var(--gray-700)">${a.role||'—'}</span></td>
          <td><span class="tag ${cls}" style="font-size:10.5px">${a.action}</span></td>
          <td class="text-gray">${a.target||'—'}</td>
          <td class="text-gray text-xs" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${a.detail||''}">${a.detail||'—'}</td>
          <td class="text-gray text-xs">${a.ip_address||'—'}</td>
        </tr>`;
      }).join('');

  renderPagination('audit-pagination', page, r.data.pages, loadAuditLog);
}
