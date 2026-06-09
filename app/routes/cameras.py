"""
app/routes/cameras.py — Cameras Blueprint
==========================================
Endpoints
---------
GET    /api/cameras           — List all cameras with live status
POST   /api/cameras           — Register a new camera
PUT    /api/cameras/<cid>     — Update camera config
DELETE /api/cameras/<cid>     — Remove a camera
POST   /api/cameras/<cid>/start — Start RTSP stream worker
POST   /api/cameras/<cid>/stop  — Stop RTSP stream worker
GET    /video_feed/<cid>      — MJPEG stream (multipart)

GET  /static/<path>    — Static assets
GET  /uploads/<path>   — Uploaded files (photos, logos)
GET  /cert.pem         — Download self-signed cert for device trust
"""

import logging
import os

from flask import Blueprint, request, Response, current_app, send_from_directory

from app.middleware import login_required, role_required
from app.models import ok, err, audit
from app.models import cameras as cam_dao

log = logging.getLogger('faceattend.routes.cameras')
cameras_bp = Blueprint('cameras', __name__)


# ── Camera CRUD ───────────────────────────────────────────────────────────────

@cameras_bp.route('/api/cameras', methods=['GET'])
@login_required
def list_cameras():
    rows       = cam_dao.list_all()
    cam_status = current_app.camera_manager.status()

    for cam in rows:
        s = cam_status.get(cam['id'], {})
        cam['streaming'] = s.get('alive',  False)   # thread alive
        cam['connected'] = s.get('frames', False)   # frames flowing

    return ok(rows)


@cameras_bp.route('/api/cameras', methods=['POST'])
@role_required('admin', 'manager')
def add_camera():
    data = request.get_json() or {}
    if not data.get('name') or not data.get('rtsp_url'):
        return err('name and rtsp_url are required')

    cid = cam_dao.add_camera(data)
    audit('ADD_CAMERA', str(cid), data.get('name', ''))
    return ok({'id': cid}, 'Camera added'), 201


@cameras_bp.route('/api/cameras/<int:cid>', methods=['PUT'])
@role_required('admin', 'manager')
def update_camera(cid):
    data = request.get_json() or {}
    updated = cam_dao.update_camera(cid, data)
    if not updated:
        return err('Nothing to update')
    audit('UPDATE_CAMERA', str(cid))
    return ok(msg='Camera updated')


@cameras_bp.route('/api/cameras/<int:cid>', methods=['DELETE'])
@role_required('admin')
def delete_camera(cid):
    current_app.camera_manager.stop_camera(cid)
    cam_dao.delete_camera(cid)
    audit('DELETE_CAMERA', str(cid))
    return ok(msg='Camera deleted')


# ── Stream control ────────────────────────────────────────────────────────────

@cameras_bp.route('/api/cameras/<int:cid>/start', methods=['POST'])
@role_required('admin', 'manager')
def start_camera(cid):
    if current_app.config.get('CLOUD_MODE'):
        return err('Camera streaming is not available in cloud mode', 503)
    row = cam_dao.get_rtsp(cid)
    if not row:
        return err('Camera not found', 404)
    current_app.camera_manager.start_camera(cid, row['rtsp_url'], row['direction'])
    return ok(msg='Camera stream started')


@cameras_bp.route('/api/cameras/<int:cid>/stop', methods=['POST'])
@role_required('admin', 'manager')
def stop_camera(cid):
    current_app.camera_manager.stop_camera(cid)
    return ok(msg='Camera stream stopped')


@cameras_bp.route('/video_feed/<int:cid>')
@login_required
def video_feed(cid):
    if current_app.config.get('CLOUD_MODE'):
        return err('Camera streaming is not available in cloud mode', 503)
    return Response(
        current_app.gen_mjpeg(cid),
        mimetype='multipart/x-mixed-replace; boundary=frame',
    )


# ── Static & uploaded file serving ───────────────────────────────────────────

@cameras_bp.route('/static/<path:path>')
def static_files(path):
    return send_from_directory(
        os.path.join(current_app.config['BASE_DIR'], 'static'), path
    )


@cameras_bp.route('/uploads/<path:path>')
@login_required
def uploaded_files(path):
    return send_from_directory(
        os.path.join(current_app.config['BASE_DIR'], 'uploads'), path
    )


@cameras_bp.route('/cert.pem')
def download_cert():
    """Allow network devices to download the self-signed cert for manual trust."""
    base_dir  = current_app.config['BASE_DIR']
    cert_path = os.path.join(base_dir, 'cert.pem')
    if not os.path.exists(cert_path):
        return err('Certificate not found', 404)
    return send_from_directory(
        base_dir, 'cert.pem',
        as_attachment=True,
        mimetype='application/x-pem-file',
    )
