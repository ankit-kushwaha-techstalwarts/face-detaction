// ══════════════════════════════════════════════════════════
//  USERS / ENROLLMENT  (server-side pagination for 500+ users)
// ══════════════════════════════════════════════════════════
function initUserFilters() { loadDepartments(); }

async function loadUsers(page = 1) {
  state.userPage = page;
  const q      = $('user-search').value.trim();
  const dept   = $('user-dept-filter').value;
  const active = $('user-active-filter').value;
  const params = new URLSearchParams({ page, per_page: 50, active });
  if (q)    params.set('q', q);
  if (dept) params.set('department', dept);

  const r = await api(`/api/users?${params}`);
  if (r.status !== 'ok') return;

  // API now returns { users, total, page, pages }
  const users = r.data.users || r.data;   // backward-compat if still a plain array
  const total = r.data.total  || users.length;
  const pages = r.data.pages  || 1;

  // Keep a flat list for dropdowns (manual attendance modal etc.)
  if (page === 1) state.users = users;

  $('users-total').textContent = `${total} employee${total !== 1 ? 's' : ''}`;

  $('users-tbody').innerHTML = users.length === 0
    ? `<tr class="empty-row"><td colspan="10">No employees found</td></tr>`
    : users.map(u => {
        const photoCount = (u.enrolled_at ? 1 : 0) + (u.template_count || 0);
        return `<tr>
        <td>
          <img class="avatar"
               src="${u.photo_path ? '/' + u.photo_path : ''}"
               onerror="this.src='data:image/svg+xml,%3Csvg xmlns=&quot;http://www.w3.org/2000/svg&quot; viewBox=&quot;0 0 40 40&quot;%3E%3Ccircle cx=&quot;20&quot; cy=&quot;20&quot; r=&quot;20&quot; fill=&quot;%23e5e7eb&quot;/%3E%3C/svg%3E'"/>
        </td>
        <td>${u.emp_id}</td>
        <td>${u.name}</td>
        <td>${u.department || '—'}</td>
        <td class="text-gray">${u.designation || '—'}</td>
        <td class="text-gray">${u.email || u.phone || '—'}</td>
        <td class="text-gray">${u.enrolled_at ? fmtDate(u.enrolled_at) : '<span style="color:var(--orange)">Not enrolled</span>'}</td>
        <td>${photoCount > 0
              ? `<span class="tag" title="${u.template_count || 0} extra template(s) + ${u.enrolled_at ? 1 : 0} anchor photo">📷 ${photoCount}</span>`
              : '<span class="text-gray">—</span>'}</td>
        <td><span class="tag ${u.active ? 'tag-active' : 'tag-inactive'}">${u.active ? 'Active' : 'Inactive'}</span></td>
        <td>
          <div style="display:flex;gap:4px;flex-wrap:wrap">
            <button class="btn btn-outline btn-xs" onclick="openEditUser(${u.id})">✏</button>
            <button class="btn btn-outline btn-xs" onclick="openEnroll(${u.id})" title="Enroll Face">🤖</button>
            <button class="btn btn-outline btn-xs" style="color:var(--red)" onclick="deactivateUser(${u.id})">🗑</button>
          </div>
        </td>
      </tr>`;
      }).join('');

  renderPagination('users-pagination', page, pages, loadUsers);
}

// Debounce search so it doesn't fire on every keystroke (important at 500+ users)
let _userSearchTimer = null;
$('user-search').addEventListener('input', () => {
  clearTimeout(_userSearchTimer);
  _userSearchTimer = setTimeout(() => loadUsers(1), 300);
});
$('user-dept-filter').addEventListener('change', () => loadUsers(1));
$('user-active-filter').addEventListener('change', () => loadUsers(1));

function openAddUserModal() {
  $('modal-user-title').textContent = 'Add Employee';
  $('user-form').reset();
  $('uf-id').value = '';
  $('uf-photo-preview').src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 80 80'%3E%3Ccircle cx='40' cy='40' r='40' fill='%23e5e7eb'/%3E%3C/svg%3E";
  $('uf-photo-section').style.display = 'none';
  $('uf-enroll-section').style.display = '';
  nuWidget.reset();
  openModal('modal-user');
}

async function openEditUser(id) {
  const r = await api(`/api/users/${id}`);
  if(r.status!=='ok') return;
  const u = r.data;
  $('modal-user-title').textContent = 'Edit Employee';
  $('uf-id').value          = u.id;
  $('uf-emp_id').value      = u.emp_id;
  $('uf-name').value        = u.name;
  $('uf-department').value  = u.department||'';
  $('uf-designation').value = u.designation||'';
  $('uf-email').value       = u.email||'';
  $('uf-phone').value       = u.phone||'';
  $('uf-photo-preview').src = u.photo_path?'/'+u.photo_path:"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 80 80'%3E%3Ccircle cx='40' cy='40' r='40' fill='%23e5e7eb'/%3E%3C/svg%3E";
  $('uf-photo-section').style.display = '';
  $('uf-enroll-section').style.display = 'none';
  nuWidget.stopWebcam();
  openModal('modal-user');
}

async function submitUser(e) {
  e.preventDefault();
  const id   = $('uf-id').value;
  const data = {
    emp_id:$('uf-emp_id').value, name:$('uf-name').value,
    department:$('uf-department').value, designation:$('uf-designation').value,
    email:$('uf-email').value, phone:$('uf-phone').value
  };
  let uid = id;
  if(id) {
    const r = await api(`/api/users/${id}`,{method:'PUT',body:JSON.stringify(data)});
    if(r.status!=='ok'){toast(r.message,'error');return;}
  } else {
    const r = await api('/api/users',{method:'POST',body:JSON.stringify(data)});
    if(r.status!=='ok'){toast(r.message,'error');return;}
    uid = r.data.id;
  }
  if(id) {
    // Edit Employee: single profile-photo upload
    const photoFile = $('uf-photo').files[0];
    if(photoFile && uid) {
      const fd = new FormData(); fd.append('photo', photoFile);
      await fetch(`/api/users/${uid}/photo`,{method:'POST',body:fd});
    }
  } else if(uid && nuWidget.queue.length) {
    // Add Employee: enroll the guided multi-pose photo queue
    const fd = new FormData();
    for (const p of nuWidget.queue) fd.append('photo', p.file, p.file.name);
    fd.append('mode', 'replace');
    const er = await fetch(`/api/users/${uid}/enroll`,{method:'POST',body:fd}).then(x=>x.json());
    if(er.status !== 'ok') toast(`Employee created, but face enrolment failed: ${er.message}`,'error');
  }
  if(!id) { nuWidget.stopWebcam(); nuWidget.clearQueue(); }
  toast(id?'Employee updated':'Employee created','success');
  closeModal('modal-user');
  loadUsers();
}

function previewPhoto(input) {
  const file = input.files[0]; if(!file) return;
  const reader = new FileReader();
  reader.onload = e => $('uf-photo-preview').src = e.target.result;
  reader.readAsDataURL(file);
}

async function deactivateUser(id) {
  if(!confirm('Deactivate this employee?')) return;
  await api(`/api/users/${id}`,{method:'DELETE'});
  toast('Employee deactivated','success');
  loadUsers();
}

// ══════════════════════════════════════════════════════════
//  GUIDED POSE-CAPTURE WIDGET (camera + upload + step guide)
//  Shared by the "Enroll Face" modal (enrollWidget) and the
//  "Add Employee" modal's guided face enrolment (nuWidget).
// ══════════════════════════════════════════════════════════
const ENROLL_STEPS = [
  { icon:'😐', text:'Look straight at the camera' },
  { icon:'↩️', text:'Turn your head slightly LEFT' },
  { icon:'↪️', text:'Turn your head slightly RIGHT' },
  { icon:'⬇️', text:'Tilt your chin down slightly (matches ceiling-camera angle)' },
  { icon:'⬆️', text:'Tilt your chin up slightly' },
];

function createPoseWizard(ids) {
  const cam   = { stream: null, facingMode: 'user', captured: null };
  const queue = [];

  function switchTab(tab) {
    ['webcam','upload'].forEach(t => {
      $(ids[t+'Tab']).classList.toggle('active', t===tab);
      $(ids[t+'Panel']).classList.toggle('active', t===tab);
    });
    if (tab === 'webcam') startWebcam(); else stopWebcam();
  }

  async function startWebcam() {
    // Already running
    if (cam.stream && cam.stream.active) return;
    stopWebcam();

    const badge  = $(ids.statusBadge);
    const video  = $(ids.video);
    const btnCap = $(ids.btnCapture);
    badge.textContent = 'Starting…';
    btnCap.disabled   = true;
    $(ids.webcamError).style.display    = 'none';
    $(ids.webcamLiveWrap).style.display = '';

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: cam.facingMode, width:{ideal:1280}, height:{ideal:720} },
        audio: false
      });
      cam.stream      = stream;
      video.srcObject = stream;
      video.onloadedmetadata = () => {
        badge.textContent  = '● Live';
        badge.style.background = 'rgba(5,122,85,.8)';
        btnCap.disabled    = false;
      };
    } catch(err) {
      const msgs = {
        NotAllowedError:  'Camera permission denied. Please allow camera access in browser settings.',
        NotFoundError:    'No camera found on this device.',
        NotReadableError: 'Camera is in use by another application.',
      };
      $(ids.webcamErrorMsg).textContent = msgs[err.name] || `Camera error: ${err.message}`;
      $(ids.webcamError).style.display    = '';
      $(ids.webcamLiveWrap).style.display = 'none';
      switchTab('upload');   // auto-fallback
    }
  }

  function stopWebcam() {
    if (cam.stream) {
      cam.stream.getTracks().forEach(t => t.stop());
      cam.stream = null;
    }
    const video = $(ids.video);
    if (video) video.srcObject = null;
    const btn = $(ids.btnCapture);
    if (btn) btn.disabled = true;
  }

  function flipCamera() {
    cam.facingMode = cam.facingMode === 'user' ? 'environment' : 'user';
    startWebcam();
  }

  function captureFrame() {
    const video  = $(ids.video);
    const canvas = $(ids.canvas);
    if (!video || !video.videoWidth) { toast('Camera not ready', 'error'); return; }

    canvas.width  = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');

    // Mirror horizontally if front camera (matches what user sees)
    if (cam.facingMode === 'user') {
      ctx.translate(canvas.width, 0);
      ctx.scale(-1, 1);
    }
    ctx.drawImage(video, 0, 0);

    canvas.toBlob(blob => {
      cam.captured = blob;
      stopWebcam();
      $(ids.webcamLiveWrap).style.display     = 'none';
      $(ids.webcamCapturedWrap).style.display = '';
    }, 'image/jpeg', 0.92);
  }

  function retakePhoto() {
    cam.captured = null;
    $(ids.webcamCapturedWrap).style.display = 'none';
    $(ids.webcamLiveWrap).style.display     = '';
    startWebcam();
  }

  function addCapturedToQueue() {
    if (!cam.captured) { toast('No photo captured', 'error'); return; }
    const f = new File([cam.captured], `webcam_${queue.length+1}.jpg`, {type:'image/jpeg'});
    queue.push({ file: f, url: URL.createObjectURL(f) });
    cam.captured = null;
    renderQueue();
    retakePhoto();   // back to live view for the next angle
  }

  function addFiles(files) {
    for (const f of files) {
      if (!f.type || !f.type.startsWith('image/')) continue;
      queue.push({ file: f, url: URL.createObjectURL(f) });
    }
    renderQueue();
  }

  function removePhoto(i) {
    URL.revokeObjectURL(queue[i].url);
    queue.splice(i, 1);
    renderQueue();
  }

  function clearQueue() {
    queue.forEach(p => URL.revokeObjectURL(p.url));
    queue.length = 0;
    renderQueue();
  }

  function renderStep() {
    const guide = $(ids.stepGuide);
    if (!guide) return;
    const n = queue.length;
    $(ids.stepDots).innerHTML = ENROLL_STEPS.map((_, i) =>
      `<span class="step-dot ${i < n ? 'done' : i === n ? 'current' : ''}"></span>`
    ).join('');
    if (n < ENROLL_STEPS.length) {
      const step = ENROLL_STEPS[n];
      $(ids.stepLabel).textContent = `Step ${n + 1} of ${ENROLL_STEPS.length}`;
      $(ids.stepInstruction).textContent = `${step.icon} ${step.text}`;
    } else {
      $(ids.stepLabel).textContent = `All ${ENROLL_STEPS.length} angles captured ✅`;
      $(ids.stepInstruction).textContent = 'Add extra angles (optional) or tap Enroll Photos below';
    }
  }

  function renderQueue() {
    const wrap = $(ids.queueWrap);
    if (!wrap) return;
    wrap.style.display = queue.length ? '' : 'none';
    $(ids.count).textContent = queue.length;
    $(ids.thumbs).innerHTML = queue.map((p, i) => `
      <div class="enroll-thumb">
        <img src="${p.url}" alt="photo ${i+1}"/>
        <button type="button" class="rm" title="Remove" onclick="${ids.name}.removePhoto(${i})">✕</button>
      </div>`).join('');
    renderStep();
    if (ids.onRender) ids.onRender(queue);
  }

  function reset() {
    clearQueue();
    cam.captured = null;
    $(ids.webcamLiveWrap).style.display     = '';
    $(ids.webcamCapturedWrap).style.display = 'none';
    $(ids.webcamError).style.display        = 'none';
    switchTab('webcam');
  }

  return {
    switchTab, startWebcam, stopWebcam, flipCamera, captureFrame, retakePhoto,
    addCapturedToQueue, addFiles, removePhoto, clearQueue, renderQueue, reset,
    queue,
  };
}

// "Enroll Face" modal — existing employees
const enrollWidget = createPoseWizard({
  name: 'enrollWidget',
  webcamTab: 'tab-webcam', uploadTab: 'tab-upload',
  webcamPanel: 'panel-webcam', uploadPanel: 'panel-upload',
  stepGuide: 'enroll-step-guide', stepLabel: 'enroll-step-label',
  stepInstruction: 'enroll-step-instruction', stepDots: 'enroll-step-dots',
  webcamLiveWrap: 'webcam-live-wrap', video: 'enroll-video',
  statusBadge: 'webcam-status-badge', btnCapture: 'btn-capture',
  webcamCapturedWrap: 'webcam-captured-wrap', canvas: 'enroll-canvas',
  webcamError: 'webcam-error', webcamErrorMsg: 'webcam-error-msg',
  queueWrap: 'enroll-queue-wrap', count: 'enroll-count', thumbs: 'enroll-thumbs',
  onRender: queue => {
    $('btn-enroll-submit').textContent = `✅ Enroll ${queue.length} Photo${queue.length>1?'s':''}`;
  },
});

// "Add Employee" modal — guided face enrolment for new employees
const nuWidget = createPoseWizard({
  name: 'nuWidget',
  webcamTab: 'nu-tab-webcam', uploadTab: 'nu-tab-upload',
  webcamPanel: 'nu-panel-webcam', uploadPanel: 'nu-panel-upload',
  stepGuide: 'nu-enroll-step-guide', stepLabel: 'nu-enroll-step-label',
  stepInstruction: 'nu-enroll-step-instruction', stepDots: 'nu-enroll-step-dots',
  webcamLiveWrap: 'nu-webcam-live-wrap', video: 'nu-enroll-video',
  statusBadge: 'nu-webcam-status-badge', btnCapture: 'nu-btn-capture',
  webcamCapturedWrap: 'nu-webcam-captured-wrap', canvas: 'nu-enroll-canvas',
  webcamError: 'nu-webcam-error', webcamErrorMsg: 'nu-webcam-error-msg',
  queueWrap: 'nu-enroll-queue-wrap', count: 'nu-enroll-count', thumbs: 'nu-enroll-thumbs',
});

function openEnroll(uid) {
  $('enroll-uid').value = uid;
  $('enroll-status').innerHTML = '';
  $('enroll-photo').value = '';
  loadEnrollStatus(uid);
  enrollWidget.reset();
  openModal('modal-enroll');
}

// Current enrollment summary shown at the top of the modal
let enrollHasFace = false;
async function loadEnrollStatus(uid) {
  const box = $('enroll-current');
  box.innerHTML = '';
  enrollHasFace = false;
  $('enroll-mode-wrap').style.display = 'none';
  try {
    const [u, t] = await Promise.all([
      fetch(`/api/users/${uid}`).then(x=>x.json()),
      fetch(`/api/users/${uid}/templates`).then(x=>x.json()),
    ]);
    if (u.status === 'ok' && u.data && u.data.enrolled_at) {
      enrollHasFace = true;
      const s = (t.status==='ok' && t.data && t.data.templates) || {};
      const extra = (s.enroll||0) + (s.merge||0) + (s.auto||0);
      box.innerHTML = `<span style="color:var(--green);font-weight:600">✅ Enrolled</span>
        <span class="text-gray">— 1 anchor photo + ${extra} extra template(s)${s.auto?` (${s.auto} auto-learned)`:''}.
        You can add more angle photos below.</span>`;
      $('enroll-mode-wrap').style.display = '';
    } else {
      box.innerHTML = `<span style="color:var(--orange);font-weight:600">Not enrolled yet</span>
        <span class="text-gray">— add 3–5 photos for best recognition.</span>`;
    }
  } catch(e) { /* status line is informational only */ }
}

async function submitEnrollQueue() {
  const uid = $('enroll-uid').value;
  if (!enrollWidget.queue.length) return;
  const mode = enrollHasFace
    ? (document.querySelector('input[name="enroll-mode"]:checked')?.value || 'append')
    : 'replace';
  const st  = $('enroll-status');
  const btn = $('btn-enroll-submit');
  btn.disabled = true;
  st.innerHTML = `<div class="flex items-center gap-2"><span class="loader"></span> Processing ${enrollWidget.queue.length} photo(s)…</div>`;
  const fd = new FormData();
  for (const p of enrollWidget.queue) fd.append('photo', p.file, p.file.name);
  fd.append('mode', mode);
  try {
    const r = await fetch(`/api/users/${uid}/enroll`, {method:'POST', body:fd}).then(x=>x.json());
    if (r.status === 'ok') {
      st.innerHTML = `<p style="color:var(--green);font-weight:600">✅ ${r.message || 'Face enrolled successfully!'}</p>`;
      toast('Face photos enrolled!', 'success');
      enrollWidget.clearQueue();
      loadEnrollStatus(uid);
      loadUsers();
    } else {
      st.innerHTML = `<p style="color:var(--red)">❌ ${r.message}</p>`;
    }
  } catch(e) {
    st.innerHTML = `<p style="color:var(--red)">❌ Upload failed — check connection and try again.</p>`;
  }
  btn.disabled = false;
}

function closeEnrollModal() {
  enrollWidget.stopWebcam();
  enrollWidget.clearQueue();
  closeModal('modal-enroll');
}

// ── Profile photo quick-snap (in Add Employee modal) ────────
const profileSnap = { stream: null };

async function startProfileSnap() {
  $('uf-snap-wrap').style.display = '';
  const video = $('uf-snap-video');
  try {
    profileSnap.stream  = await navigator.mediaDevices.getUserMedia({video:{facingMode:'user'},audio:false});
    video.srcObject     = profileSnap.stream;
  } catch(e) {
    toast('Camera access denied', 'error');
    $('uf-snap-wrap').style.display = 'none';
  }
}

function stopProfileSnap() {
  if (profileSnap.stream) { profileSnap.stream.getTracks().forEach(t=>t.stop()); profileSnap.stream=null; }
  $('uf-snap-video').srcObject = null;
  $('uf-snap-wrap').style.display = 'none';
}

function snapProfilePhoto() {
  const video  = $('uf-snap-video');
  const canvas = $('uf-snap-canvas');
  canvas.width  = video.videoWidth  || 640;
  canvas.height = video.videoHeight || 480;
  const ctx = canvas.getContext('2d');
  ctx.translate(canvas.width, 0); ctx.scale(-1,1);
  ctx.drawImage(video, 0, 0);
  $('uf-photo-preview').src = canvas.toDataURL('image/jpeg', 0.92);
  // Store as file-like blob on the input for later upload
  canvas.toBlob(blob => {
    const dt = new DataTransfer();
    dt.items.add(new File([blob], 'profile_snap.jpg', {type:'image/jpeg'}));
    $('uf-photo').files = dt.files;
  }, 'image/jpeg', 0.92);
  stopProfileSnap();
}

// ── Import ─────────────────────────────────────────────────
function openImportModal() { $('import-file').click(); }

async function importCSV(input) {
  const file = input.files[0]; if(!file) return;
  const fd = new FormData(); fd.append('file', file);
  const r  = await fetch('/api/users/import',{method:'POST',body:fd}).then(x=>x.json());
  const el = $('import-result');
  if(r.status==='ok') {
    el.innerHTML = `<p class="text-sm" style="color:var(--green)">✅ ${r.message}</p>
      ${r.data.errors.length?`<ul style="font-size:12px;color:var(--red);margin-top:4px">${r.data.errors.map(e=>`<li>${e}</li>`).join('')}</ul>`:''}`;
    toast(r.message,'success');
    loadUsers();
  } else {
    el.innerHTML = `<p class="text-sm" style="color:var(--red)">❌ ${r.message}</p>`;
  }
}
