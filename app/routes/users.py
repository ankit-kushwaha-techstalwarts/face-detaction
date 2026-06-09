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
POST   /api/users/<uid>/photo       — Upload face photo
POST   /api/users/<uid>/enroll      — Enrol face encoding
POST   /api/users/import            — Bulk CSV import
"""

import logging
import os
from datetime import datetime

from flask import Blueprint, request, current_app
from werkzeug.utils import secure_filename

from app.middleware import login_required, role_required, safe_page
from app.models import ok, err, audit, allowed_image
from app.models import users as user_dao

log = logging.getLogger('faceattend.routes.users')
users_bp = Blueprint('users', __name__)


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
    return ok(result)


@users_bp.route('/api/users/<int:uid>', methods=['GET'])
@login_required
def get_user(uid):
    user = user_dao.get_user(uid)
    if not user:
        return err('User not found', 404)
    return ok(user)


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


# ── Photo upload ───────────────────────────────────────────────────────────────

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

    if 'photo' in request.files:
        f     = request.files['photo']
        fname = secure_filename(f'enroll_{uid}_{int(datetime.now().timestamp())}.jpg')
        path  = os.path.join(face_dir, fname)
        f.save(path)
    else:
        photo_path = user_dao.get_user_photo_path(uid)
        if not photo_path:
            return err('No photo available for enrolment — upload a photo first')
        path = os.path.join(base_dir, photo_path)

    enc = current_app.encode_face(path)
    if enc is None:
        return err('No face detected. Please use a clear, well-lit frontal photo.')

    rel  = os.path.relpath(path, base_dir)
    blob = current_app.encoding_to_blob(enc)
    user_dao.update_user_encoding(uid, blob, rel)
    current_app.face_cache.reload()
    audit('ENROLL_FACE', str(uid))
    return ok(msg='Face enrolled successfully')


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
