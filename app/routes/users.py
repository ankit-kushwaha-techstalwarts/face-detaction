"""
app/routes/users.py — Users Blueprint
======================================
Endpoints
---------
GET    /api/users                   — Paginated employee list
GET    /api/users/<uid>             — Single employee
POST   /api/users                   — Create employee
PUT    /api/users/<uid>             — Update employee
DELETE /api/users/<uid>             — Soft-delete (deactivate)
GET    /api/users/<uid>/photos      — All photos for a user
POST   /api/users/<uid>/photo       — Upload face photo
POST   /api/users/<uid>/enroll      — Enrol face encoding
POST   /api/users/import            — Bulk CSV import
"""

import logging
import os
from datetime import datetime

import numpy as np

from flask import Blueprint, request, current_app
from werkzeug.utils import secure_filename

from app.middleware import login_required, role_required, safe_page
from app.models import ok, err, audit, allowed_image
from app.models import users as user_dao

log = logging.getLogger('faceattend.routes.users')
users_bp = Blueprint('users', __name__)


def _with_photo_url(user: dict) -> dict:
    """Add an absolute 'photo_url' built from the relative 'photo_path',
    so cross-origin frontends can use it directly as an <img src>."""
    photo_path = user.get('photo_path')
    user['photo_url'] = f"{request.host_url.rstrip('/')}/{photo_path}" if photo_path else None
    return user


@users_bp.route('/api/users', methods=['GET'])
@login_required
def list_users():
    page, per_page = safe_page(
        request.args.get('page', 1),
        request.args.get('per_page', 50),
    )
    result = user_dao.list_users(
        dept     = request.args.get('department'),
        search   = request.args.get('q', '').strip(),
        active   = request.args.get('active', '1'),
        page     = page,
        per_page = per_page,
    )
    result['users'] = [_with_photo_url(u) for u in result['users']]
    return ok(result)


@users_bp.route('/api/users/<int:uid>', methods=['GET'])
@login_required
def get_user(uid):
    user = user_dao.get_user(uid)
    if not user:
        return err('User not found', 404)
    return ok(_with_photo_url(user))


@users_bp.route('/api/users', methods=['POST'])
@role_required('admin', 'manager')
def create_user():
    data = request.get_json() or {}
    if not data.get('emp_id') or not data.get('name'):
        return err('emp_id and name are required')
    try:
        uid = user_dao.create_user(data)
    except Exception as exc:
        # Expose only a safe, generic message to the client
        log.warning("[Users] Create failed: %s", exc)
        return err('Could not create user — employee ID may already exist')
    audit('CREATE_USER', data['emp_id'], data.get('name', ''))
    return ok({'id': uid}, 'User created'), 201


@users_bp.route('/api/users/<int:uid>', methods=['PUT'])
@role_required('admin', 'manager')
def update_user(uid):
    data = request.get_json() or {}
    updated = user_dao.update_user(uid, data)
    if not updated:
        return err('Nothing to update')
    current_app.face_cache.reload()
    audit('UPDATE_USER', str(uid), str(list(data.keys())))
    return ok(msg='User updated')


@users_bp.route('/api/users/<int:uid>', methods=['DELETE'])
@role_required('admin', 'manager')
def delete_user(uid):
    user_dao.deactivate_user(uid)
    current_app.face_cache.reload()
    audit('DEACTIVATE_USER', str(uid))
    return ok(msg='User deactivated')


# ── Photos ────────────────────────────────────────────────────────────────────

MAX_ENROLL_PHOTOS = 5

@users_bp.route('/api/users/<int:uid>/photos', methods=['GET'])
@role_required('admin', 'manager')
def get_user_photos(uid):
    """Return all photos for a user (main + enrollment snapshots)."""
    photos = user_dao.get_user_photos(uid)
    if photos is None:
        return err('User not found', 404)
    host = request.host_url.rstrip('/')
    for p in photos:
        path = p.get('photo_path')
        p['photo_url'] = f"{host}/{path}" if path else None
    return ok({'photos': photos, 'total': len(photos), 'max': MAX_ENROLL_PHOTOS})


@users_bp.route('/api/users/<int:uid>/photo', methods=['POST'])
@role_required('admin', 'manager')
def upload_photo(uid):
    if 'photo' not in request.files:
        return err('No photo file provided')
    f = request.files['photo']
    if not f.filename or not allowed_image(f.filename):
        return err('Invalid image format (JPG / PNG / WEBP)')

    face_dir = current_app.FACE_DIR
    if not face_dir:
        return err('Face directory not configured (cloud mode?)', 503)

    fname = secure_filename(f'user_{uid}_{int(datetime.now().timestamp())}.jpg')
    path  = os.path.join(face_dir, fname)
    f.save(path)

    base_dir = current_app.config['BASE_DIR']
    rel      = os.path.relpath(path, base_dir)
    user_dao.update_user_photo(uid, rel)
    audit('UPLOAD_PHOTO', str(uid), fname)
    return ok({'photo_path': rel}, 'Photo uploaded')


# ── Face enrolment ─────────────────────────────────────────────────────────────

@users_bp.route('/api/users/<int:uid>/enroll', methods=['POST'])
@role_required('admin', 'manager')
def enroll_face(uid):
    if current_app.config.get('CLOUD_MODE'):
        return err('Face enrolment is not available in cloud mode', 503)

    face_dir = current_app.FACE_DIR
    base_dir = current_app.config['BASE_DIR']

    # Multi-photo enrolment. mode=replace (default): first accepted photo
    # becomes the anchor encoding (synced to cloud), the rest become extra
    # pose templates, and the previous gallery is wiped. mode=append: anchor
    # and gallery are kept; every accepted photo becomes an extra template.
    mode = (request.form.get('mode') or 'replace').lower()
    if mode not in ('replace', 'append'):
        return err("mode must be 'replace' or 'append'")

    # ── Collect uploaded files ────────────────────────────────────────────────
    saved_paths = []   # track files written to disk so we can clean up on failure
    paths = []
    if 'photo' in request.files:
        files = request.files.getlist('photo')
        if len(files) > MAX_ENROLL_PHOTOS:
            return err(f'Maximum {MAX_ENROLL_PHOTOS} photos allowed per enrolment')

        # In append mode, check how many slots remain before touching the disk
        if mode == 'append':
            row = user_dao.get_user_with_encoding(uid)
            if not (row and row.get('face_encoding')):
                mode = 'replace'
            else:
                existing = user_dao.count_enroll_templates(uid)
                # anchor occupies 1 slot; enroll templates fill the rest
                slots = (MAX_ENROLL_PHOTOS - 1) - existing
                if slots <= 0:
                    return err(
                        f'Maximum {MAX_ENROLL_PHOTOS} photos already enrolled. '
                        f'Delete some enroll templates first.'
                    )
                if len(files) > slots:
                    return err(
                        f'Only {slots} slot(s) remaining (max {MAX_ENROLL_PHOTOS} total). '
                        f'Send at most {slots} photo(s).'
                    )

        ts = int(datetime.now().timestamp())
        for i, f in enumerate(files):
            fname = secure_filename(f'enroll_{uid}_{ts}_{i}.jpg')
            path  = os.path.join(face_dir, fname)
            f.save(path)
            saved_paths.append(path)
            paths.append(path)
    else:
        photo_path = user_dao.get_user_photo_path(uid)
        if not photo_path:
            return err('No photo available for enrolment — upload a photo first')
        paths.append(os.path.join(base_dir, photo_path))

    # ── Encode faces ─────────────────────────────────────────────────────────
    accepted, rejected = [], []
    for path in paths:
        enc, det_score, sharpness, reason = current_app.encode_face_detailed(path)
        if enc is None:
            rejected.append(f"{os.path.basename(path)}: {reason}")
            continue
        # Reject if too similar to an already-accepted photo in this batch —
        # forces the wizard to collect genuinely different angles.
        duplicate = False
        for _, prev_enc, _, _ in accepted:
            sim = float(
                np.dot(enc / (np.linalg.norm(enc) or 1),
                       prev_enc / (np.linalg.norm(prev_enc) or 1))
            )
            if sim > 0.92:
                rejected.append(
                    f"{os.path.basename(path)}: too similar to another photo "
                    f"in this batch ({sim:.0%}) — use a different angle"
                )
                duplicate = True
                break
        if not duplicate:
            accepted.append((path, enc, det_score, sharpness))

    if not accepted:
        # Clean up files we saved before failing
        for p in saved_paths:
            try: os.remove(p)
            except OSError: pass
        return err(rejected[0] if len(rejected) == 1
                   else 'All photos rejected — ' + ' | '.join(rejected))

    # mode may have been promoted from append→replace above
    if mode == 'append':
        for path, extra_enc, det_score, sharpness in accepted:
            rel = os.path.relpath(path, base_dir)
            current_app.add_face_template(uid, extra_enc, source='enroll',
                                          reload_cache=False, photo_path=rel,
                                          det_score=det_score, sharpness=sharpness)
        msg = f'Added {len(accepted)} photo(s) to face profile'
    else:
        anchor_path, anchor_enc, _, _ = accepted[0]
        rel = os.path.relpath(anchor_path, base_dir)
        user_dao.update_user_encoding(uid, current_app.encoding_to_blob(anchor_enc), rel)
        # Re-enrolment resets the gallery (including auto-learned templates)
        current_app.clear_face_templates(uid)
        for path, extra_enc, det_score, sharpness in accepted[1:]:
            extra_rel = os.path.relpath(path, base_dir)
            current_app.add_face_template(uid, extra_enc, source='enroll',
                                          reload_cache=False, photo_path=extra_rel,
                                          det_score=det_score, sharpness=sharpness)
        msg = f'Face enrolled with {len(accepted)} photo(s)'
    current_app.face_cache.reload()
    audit('ENROLL_FACE', str(uid), f"mode={mode} {len(accepted)} photo(s)")
    if rejected:
        msg += f" — {len(rejected)} rejected: " + ' | '.join(rejected)
    return ok(msg=msg)


@users_bp.route('/api/users/<int:uid>/templates', methods=['GET', 'DELETE'])
@role_required('admin', 'manager')
def manage_templates(uid):
    """Inspect or purge a user's face-template gallery.
    DELETE ?source=auto removes only self-learned templates."""
    if request.method == 'GET':
        return ok({'templates': current_app.get_template_stats(uid)})
    source = request.args.get('source')
    if source and source not in ('enroll', 'merge', 'auto'):
        return err('source must be enroll, merge or auto')
    deleted = current_app.clear_face_templates(uid, source)
    audit('PURGE_TEMPLATES', str(uid), f"source={source or 'all'} deleted={deleted}")
    return ok(msg=f'Deleted {deleted} template(s)')


# ── Bulk CSV import ────────────────────────────────────────────────────────────

@users_bp.route('/api/users/import', methods=['POST'])
@role_required('admin', 'manager')
def import_users():
    if 'file' not in request.files:
        return err('No file uploaded')
    f       = request.files['file']
    content = f.read()
    result  = user_dao.import_users_from_csv(content)
    audit('IMPORT_USERS', '', f"inserted={result['inserted']} skipped={result['skipped']}")
    return ok(
        result,
        f"Import complete: {result['inserted']} inserted, {result['skipped']} skipped",
    )
