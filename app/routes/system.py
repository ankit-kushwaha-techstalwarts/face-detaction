"""
app/routes/system.py — System Blueprint
========================================
Endpoints
---------
GET  /api/dashboard              — Aggregated KPIs and charts
GET  /api/settings               — All settings (public for branding)
PUT  /api/settings               — Save settings
POST /api/settings/logo          — Upload organisation logo
POST /api/settings/favicon       — Upload favicon
PUT  /api/settings/theme         — Save theme colours
GET  /api/departments            — Department list
POST /api/departments            — Add department
GET  /api/holidays               — Holiday list
POST /api/holidays               — Add holiday
PUT  /api/holidays/<hid>         — Update holiday
DELETE /api/holidays/<hid>       — Delete holiday
GET  /api/admin-users            — List system users  [admin]
POST /api/admin-users            — Create system user [admin]
PUT  /api/admin-users/<uid>      — Update system user [admin]
DELETE /api/admin-users/<uid>    — Delete system user [admin]
GET  /api/audit-log              — Paginated audit log [admin]
--- Cloud sync ---
POST /api/sync/attendance        — Receive attendance batch  [API-key]
POST /api/sync/unknown-faces     — Receive unknown faces     [API-key]
GET  /api/sync/employees         — Push employee list        [API-key]
GET  /api/sync/status            — Cloud sync log            [API-key]
GET  /api/sync/local-status      — Pending unsynced counts   [login]
GET  /api/sync/log               — Local sync log entries    [login]
POST /api/sync/test-connection   — Test cloud portal reachability [login]
"""

import logging
import os
from datetime import datetime, timedelta

from flask import Blueprint, request, current_app
from werkzeug.utils import secure_filename

from app.middleware import login_required, role_required, safe_page
from app.models import ok, err, audit, allowed_image, CLOUD_MODE
from app.models import attendance as att_dao
from app.models import settings as sett_dao
from app.models.settings import verify_sync_key

log = logging.getLogger('faceattend.routes.system')
system_bp = Blueprint('system', __name__)


# ── Dashboard ─────────────────────────────────────────────────────────────────

@system_bp.route('/api/dashboard', methods=['GET'])
@login_required
def dashboard():
    data = att_dao.dashboard_stats(current_app.camera_manager)
    return ok(data)


# ── Settings ──────────────────────────────────────────────────────────────────

@system_bp.route('/api/settings', methods=['GET'])
def get_settings():
    # Intentionally public — login page needs org_name, primary_color, etc.
    return ok(sett_dao.get_all_settings())


@system_bp.route('/api/settings', methods=['PUT'])
@role_required('admin', 'manager')
def save_settings():
    data = request.get_json() or {}
    sett_dao.save_settings(data)
    audit('SAVE_SETTINGS', '', str(list(data.keys())))
    return ok(msg='Settings saved')


@system_bp.route('/api/settings/logo', methods=['POST'])
@role_required('admin')
def upload_logo():
    if 'logo' not in request.files:
        return err('No logo file provided')
    f = request.files['logo']
    if not f.filename or not allowed_image(f.filename):
        return err('Invalid image format (JPG / PNG / WEBP)')

    logo_dir = current_app.config['LOGO_DIR']
    os.makedirs(logo_dir, exist_ok=True)
    ext   = secure_filename(f.filename).rsplit('.', 1)[-1].lower()
    fname = f'org_logo.{ext}'
    path  = os.path.join(logo_dir, fname)
    f.save(path)
    rel   = f'uploads/logo/{fname}'
    sett_dao.save_logo(rel)
    audit('UPLOAD_LOGO', rel)
    return ok({'logo_path': rel}, 'Logo uploaded')


@system_bp.route('/api/settings/favicon', methods=['POST'])
@role_required('admin')
def upload_favicon():
    if 'favicon' not in request.files:
        return err('No favicon file provided')
    f = request.files['favicon']
    if not f.filename:
        return err('Empty filename')

    logo_dir = current_app.config['LOGO_DIR']
    os.makedirs(logo_dir, exist_ok=True)
    ext   = f.filename.rsplit('.', 1)[-1].lower()
    fname = f'favicon.{ext}'
    path  = os.path.join(logo_dir, fname)
    f.save(path)
    rel   = f'uploads/logo/{fname}'
    sett_dao.save_favicon(rel)
    audit('UPLOAD_FAVICON', rel)
    return ok({'favicon_path': rel}, 'Favicon uploaded')


@system_bp.route('/api/settings/theme', methods=['PUT'])
@role_required('admin')
def save_theme():
    data = request.get_json() or {}
    sett_dao.save_theme(data)
    audit('SAVE_THEME', '', str(data))
    return ok(msg='Theme saved')


# ── Departments ───────────────────────────────────────────────────────────────

@system_bp.route('/api/departments', methods=['GET'])
@login_required
def list_departments():
    return ok(sett_dao.list_departments())


@system_bp.route('/api/departments', methods=['POST'])
@role_required('admin', 'manager')
def add_department():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return err('name is required')
    sett_dao.add_department(name)
    audit('ADD_DEPARTMENT', name)
    return ok(msg='Department added')


# ── Holidays ──────────────────────────────────────────────────────────────────

@system_bp.route('/api/holidays', methods=['GET'])
@login_required
def list_holidays():
    year = request.args.get('year', datetime.now().strftime('%Y'))
    return ok(sett_dao.list_holidays(year))


@system_bp.route('/api/holidays', methods=['POST'])
@role_required('admin', 'manager')
def add_holiday():
    data  = request.get_json() or {}
    date  = (data.get('date')  or '').strip()
    name  = (data.get('name')  or '').strip()
    htype = data.get('type', 'national')
    if not date or not name:
        return err('date and name are required')
    try:
        sett_dao.add_holiday(date, name, htype)
    except Exception:
        return err('A holiday on this date already exists')
    audit('ADD_HOLIDAY', date, name)
    return ok(msg='Holiday added')


@system_bp.route('/api/holidays/<int:hid>', methods=['PUT'])
@role_required('admin', 'manager')
def update_holiday(hid):
    data = request.get_json() or {}
    sett_dao.update_holiday(hid, data)
    audit('EDIT_HOLIDAY', str(hid), data.get('name', ''))
    return ok(msg='Holiday updated')


@system_bp.route('/api/holidays/<int:hid>', methods=['DELETE'])
@role_required('admin', 'manager')
def delete_holiday(hid):
    sett_dao.delete_holiday(hid)
    audit('DELETE_HOLIDAY', str(hid))
    return ok(msg='Holiday deleted')


# ── Admin user management ─────────────────────────────────────────────────────

@system_bp.route('/api/admin-users', methods=['GET'])
@role_required('admin')
def list_admin_users():
    return ok(sett_dao.list_admin_users())


@system_bp.route('/api/admin-users', methods=['POST'])
@role_required('admin')
def create_admin_user():
    from app.models import hash_password, validate_password
    data      = request.get_json() or {}
    username  = (data.get('username')  or '').strip()
    password  = data.get('password', '')
    role      = data.get('role', 'manager')
    full_name = (data.get('full_name') or '').strip()
    dept      = (data.get('department') or '').strip()

    if not username or not password:
        return err('Username and password are required')
    if role not in sett_dao.VALID_ROLES:
        return err(f'Invalid role. Must be one of: {", ".join(sett_dao.VALID_ROLES)}')

    pw_err = validate_password(password)
    if pw_err:
        return err(pw_err)

    try:
        uid = sett_dao.create_admin_user(
            username, hash_password(password), role, full_name, dept
        )
    except Exception as exc:
        log.warning("[AdminUsers] Create failed: %s", exc)
        return err('Could not create user — username may already exist')

    audit('CREATE_ADMIN_USER', username, f'role={role}')
    return ok({'id': uid}, 'User created'), 201


@system_bp.route('/api/admin-users/<int:uid>', methods=['PUT'])
@role_required('admin')
def update_admin_user(uid):
    from app.models import hash_password, validate_password
    data = request.get_json() or {}

    # Separate password from other fields
    update_data = {k: v for k, v in data.items() if k != 'password'}

    if data.get('password'):
        pw_err = validate_password(data['password'])
        if pw_err:
            return err(pw_err)
        update_data['password_hash'] = hash_password(data['password'])

    if update_data.get('role') and update_data['role'] not in sett_dao.VALID_ROLES:
        return err(f'Invalid role. Must be one of: {", ".join(sett_dao.VALID_ROLES)}')

    updated = sett_dao.update_admin_user(uid, update_data)
    if not updated:
        return err('Nothing to update')

    audit('UPDATE_ADMIN_USER', str(uid), str(list(update_data.keys())))
    return ok(msg='User updated')


@system_bp.route('/api/admin-users/<int:uid>', methods=['DELETE'])
@role_required('admin')
def delete_admin_user(uid):
    if uid == 1:
        return err('Cannot delete the primary admin account')
    sett_dao.delete_admin_user(uid)
    audit('DELETE_ADMIN_USER', str(uid))
    return ok(msg='User deleted')


# ── Audit log ─────────────────────────────────────────────────────────────────

@system_bp.route('/api/audit-log', methods=['GET'])
@role_required('admin')
def get_audit_log():
    page, per_page = safe_page(
        request.args.get('page', 1),
        request.args.get('per_page', 50),
    )
    result = sett_dao.get_audit_log(
        date_from = request.args.get('from', (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')),
        date_to   = request.args.get('to',   datetime.now().strftime('%Y-%m-%d')),
        username  = request.args.get('username', ''),
        page      = page,
        per_page  = per_page,
    )
    return ok(result)


# ── Cloud Sync endpoints ──────────────────────────────────────────────────────
# These are called by the local sync_agent, not by browser sessions.
# Authentication is via X-API-Key header (shared secret in settings).

def _require_sync_key():
    """Return error response or None. Call at top of each sync endpoint."""
    provided = request.headers.get('X-API-Key', '')
    if not verify_sync_key(provided):
        log.warning("[Sync] Rejected request with invalid API key from %s", request.remote_addr)
        return err('Unauthorised', 401)
    return None


@system_bp.route('/api/sync/attendance', methods=['POST'])
def sync_receive_attendance():
    if (e := _require_sync_key()):
        return e
    body    = request.get_json() or {}
    site_id = body.get('site_id', 'unknown')
    records = body.get('records', [])
    if not records:
        return ok(msg='No records')
    inserted = att_dao.sync_receive_attendance(records, site_id, CLOUD_MODE)
    log.info("[Sync] Received %d/%d attendance records from %s", inserted, len(records), site_id)
    return ok({'inserted': inserted}, f'Received {inserted} records')


@system_bp.route('/api/sync/unknown-faces', methods=['POST'])
def sync_receive_unknown_faces():
    if (e := _require_sync_key()):
        return e
    body    = request.get_json() or {}
    site_id = body.get('site_id', 'unknown')
    records = body.get('records', [])
    if not records:
        return ok(msg='No records')
    inserted = att_dao.sync_receive_unknown_faces(records, site_id, CLOUD_MODE)
    return ok({'inserted': inserted}, f'Received {inserted} unknown-face records')


@system_bp.route('/api/sync/employees', methods=['GET'])
def sync_get_employees():
    if (e := _require_sync_key()):
        return e
    since  = request.args.get('since', '1970-01-01 00:00:00')
    result = att_dao.sync_get_employees(since, CLOUD_MODE)
    return ok(result)


@system_bp.route('/api/sync/status', methods=['GET'])
def sync_status():
    if (e := _require_sync_key()):
        return e
    rows = sett_dao.get_full_sync_log(100)
    return ok(rows)


@system_bp.route('/api/sync/local-status', methods=['GET'])
@login_required
def sync_local_status():
    counts = att_dao.unsynced_counts()
    return ok(counts)


@system_bp.route('/api/sync/log', methods=['GET'])
@login_required
def sync_log_local():
    rows = sett_dao.get_sync_log(50)
    return ok(rows)


@system_bp.route('/api/sync/test-connection', methods=['POST'])
@login_required
def sync_test_connection():
    from app.models import get_setting
    url = get_setting('cloud_api_url', '').rstrip('/')
    key = get_setting('cloud_api_key', '')
    if not url or not key:
        return err('Cloud URL and API Key must be configured first')
    try:
        import requests as req
        resp = req.get(
            f"{url}/api/sync/status",
            headers={'X-API-Key': key},
            timeout=10,
        )
        if resp.status_code == 200:
            return ok(msg=f'Connected to {url} ✓')
        if resp.status_code == 401:
            return err('Reached cloud portal but API key was rejected — check the key')
        return err(f'Cloud portal responded with HTTP {resp.status_code}')
    except Exception as exc:
        return err(f'Connection failed: {exc}')
