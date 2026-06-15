"""
Face Recognition Attendance System
Government Department — Web Application Backend

Modes
-----
Local (default):  Runs face engine, cameras, local SQLite.
                  Set sync_enabled=1 in Settings to push data to cloud.

Cloud:            Web portal only — no cameras, no face engine.
                  Set env vars:
                    DATABASE_URL=postgresql://user:pw@host:5432/dbname
                    CLOUD_MODE=1
                    SECRET_KEY=<random 64-char hex>
"""

import os, io, csv, json, logging, hashlib, secrets
import numpy as np
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, request, jsonify, send_from_directory,
                   Response, render_template, abort, session, redirect, url_for)
from werkzeug.utils import secure_filename

# ── DB adapter: SQLite locally, PostgreSQL on cloud ────────────────────────
_DATABASE_URL = os.environ.get('DATABASE_URL', '')
CLOUD_MODE    = bool(os.environ.get('CLOUD_MODE', ''))

if _DATABASE_URL and CLOUD_MODE:
    from database_pg import get_db, init_db, get_setting, set_setting
    log_prefix = "[CLOUD]"
else:
    from database import get_db, init_db, get_setting, set_setting
    log_prefix = "[LOCAL]"

# ── Face engine: local only ────────────────────────────────────────────────
if not CLOUD_MODE:
    from face_engine import (encode_face_from_image, encode_face_detailed,
                             encoding_to_blob,
                             blob_to_encoding, face_cache, camera_manager, gen_mjpeg,
                             add_face_template, clear_face_templates, get_template_stats,
                             FACE_DIR, SNAPSHOT_DIR, UNKNOWN_DIR)
else:
    # Stubs so the rest of the file imports cleanly on cloud
    camera_manager = None
    gen_mjpeg      = None
    FACE_DIR = SNAPSHOT_DIR = UNKNOWN_DIR = ''
    def encode_face_from_image(p): return None
    def encode_face_detailed(p): return None, 'Face engine unavailable in cloud mode'
    def add_face_template(*a, **k): return None
    def clear_face_templates(*a, **k): return 0
    def get_template_stats(uid): return {}
    def encoding_to_blob(e): return None
    def blob_to_encoding(b): return None
    class _FakeCache:
        def reload(self): pass
    face_cache = _FakeCache()

# ── App setup ──────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(__file__)
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads', 'faces')
LOGO_DIR   = os.path.join(BASE_DIR, 'uploads', 'logo')
os.makedirs(LOGO_DIR, exist_ok=True)

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # 64MB — multi-photo enrollment uploads
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# ── Auth helpers ───────────────────────────────────────────────────────────

def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def check_login(username: str, password: str):
    """Return (ok, role, full_name) from admin_users table."""
    conn = get_db()
    row  = conn.execute(
        'SELECT password_hash, role, full_name FROM admin_users WHERE username=? AND active=1',
        (username,)
    ).fetchone()
    if row and row['password_hash'] == hash_password(password):
        conn.execute(
            'UPDATE admin_users SET last_login=datetime("now","localtime") WHERE username=?',
            (username,)
        )
        conn.commit()
        conn.close()
        return True, row['role'], row['full_name'] or username
    conn.close()
    return False, None, None


# Role hierarchy: guard=1, manager=2, admin=3
ROLE_LEVEL = {'guard': 1, 'manager': 2, 'admin': 3}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': 'Not authenticated'}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    """Restrict endpoint to specific roles. Usage: @role_required('admin') or @role_required('admin','manager')"""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get('logged_in'):
                return jsonify({'status': 'error', 'message': 'Not authenticated'}), 401
            user_role = session.get('role', 'guard')
            if user_role not in roles:
                return jsonify({'status': 'error',
                                'message': f'Access denied. Required role: {" or ".join(roles)}'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


# Public API paths (no login required)
_PUBLIC_PATHS = {'/api/auth/login', '/api/settings'}   # settings GET for login branding

# Guard-accessible GET paths (read-only for guard role)
_GUARD_GET_PATHS = {
    '/api/dashboard', '/api/cameras', '/api/attendance/today-summary',
    '/api/settings', '/api/departments', '/api/holidays',
}

def _auth_guard():
    """Protect all /api/ routes. Guard role may only access limited GET endpoints."""
    if request.path.startswith('/api/') and request.path not in _PUBLIC_PATHS:
        if not session.get('logged_in'):
            return jsonify({'status': 'error', 'message': 'Not authenticated'}), 401
        role = session.get('role', 'guard')
        if role == 'guard':
            # Guards may GET a small set of paths and stream video feeds
            allowed = (
                request.method == 'GET' and any(
                    request.path.startswith(p) for p in _GUARD_GET_PATHS
                )
            ) or request.path.startswith('/video_feed/')
            if not allowed:
                return jsonify({'status': 'error', 'message': 'Access denied for your role'}), 403


# ── Utility ────────────────────────────────────────────────────────────────

def ok(data=None, msg='success', **kw):
    return jsonify({'status': 'ok', 'message': msg, 'data': data, **kw})


def err(msg, code=400):
    return jsonify({'status': 'error', 'message': msg}), code


ALLOWED_IMG = {'png', 'jpg', 'jpeg', 'webp', 'ico'}


def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMG


# ── Audit logging ──────────────────────────────────────────────────────────

def audit(action: str, target: str = '', detail: str = ''):
    """Write a row to audit_log. Safe to call from anywhere — never raises."""
    try:
        username = session.get('username', 'system')
        role     = session.get('role', '')
        ip       = request.remote_addr
        conn = get_db()
        conn.execute(
            'INSERT INTO audit_log (username, role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?)',
            (username, role, action, target, detail, ip)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # audit must never crash the main flow


# ── Password strength validation ───────────────────────────────────────────

def validate_password(pw: str):
    """Return error string or None if acceptable."""
    min_len = int(get_setting('min_pw_length', '8'))
    require = get_setting('require_strong_pw', '1') == '1'
    if len(pw) < min_len:
        return f'Password must be at least {min_len} characters'
    if require:
        if not any(c.isupper() for c in pw):
            return 'Password must contain at least one uppercase letter'
        if not any(c.isdigit() for c in pw):
            return 'Password must contain at least one digit'
    return None


# ── Working-day helpers (holiday-aware) ────────────────────────────────────

def _get_holidays_set(date_from: str, date_to: str) -> set:
    conn = get_db()
    rows = conn.execute(
        "SELECT date FROM holidays WHERE date BETWEEN ? AND ?",
        (date_from, date_to)
    ).fetchall()
    conn.close()
    return {r['date'] for r in rows}


def _working_days_in_range(date_from: str, date_to: str) -> list:
    """Working days in range: excludes weekends + public holidays."""
    work_day_nums = set(
        int(d) for d in get_setting('work_days', '1,2,3,4,5').split(',') if d.strip().isdigit()
    )
    holidays = _get_holidays_set(date_from, date_to)
    start = datetime.strptime(date_from, '%Y-%m-%d')
    end   = datetime.strptime(date_to,   '%Y-%m-%d')
    days, cur = [], start
    while cur <= end:
        ds = cur.strftime('%Y-%m-%d')
        if cur.isoweekday() in work_day_nums and ds not in holidays:
            days.append(ds)
        cur += timedelta(days=1)
    return days


# ── Auth routes ────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET'])
def login_page():
    if session.get('logged_in'):
        return redirect('/')
    return send_from_directory('templates', 'login.html')

@app.route('/api/auth/login', methods=['POST'])
def do_login():
    data     = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return err('Username and password required')
    ok_flag, role, full_name = check_login(username, password)
    if ok_flag:
        session['logged_in'] = True
        session['username']  = username
        session['role']      = role
        session['full_name'] = full_name
        session.permanent    = True
        audit('LOGIN', username, f'Role: {role}')
        return ok({'username': username, 'role': role, 'full_name': full_name}, 'Login successful')
    # Log failed attempt
    try:
        conn = get_db()
        ip = request.remote_addr
        conn.execute(
            "INSERT INTO audit_log (username, role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?)",
            (username, '', 'LOGIN_FAILED', username, 'Invalid credentials', ip)
        )
        conn.commit(); conn.close()
    except Exception:
        pass
    return err('Invalid username or password', 401)

@app.route('/api/auth/logout', methods=['POST'])
def do_logout():
    audit('LOGOUT', session.get('username', ''))
    session.clear()
    return ok(msg='Logged out')

@app.route('/api/auth/me', methods=['GET'])
@login_required
def auth_me():
    return ok({
        'username':  session.get('username', 'admin'),
        'role':      session.get('role', 'admin'),
        'full_name': session.get('full_name', 'Admin'),
    })

@app.route('/api/auth/change-password', methods=['POST'])
@login_required
def change_password():
    data     = request.get_json() or {}
    current  = data.get('current_password', '')
    new_pw   = data.get('new_password', '')
    username = session.get('username', 'admin')
    ok_flag, _, _ = check_login(username, current)
    if not ok_flag:
        return err('Current password is incorrect', 401)
    pw_err = validate_password(new_pw)
    if pw_err:
        return err(pw_err)
    conn = get_db()
    conn.execute('UPDATE admin_users SET password_hash=? WHERE username=?',
                 (hash_password(new_pw), username))
    conn.commit()
    conn.close()
    audit('CHANGE_PASSWORD', username)
    return ok(msg='Password changed successfully')


# ── Single-page app ────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    return send_from_directory('templates', 'index.html')


@app.route('/static/<path:path>')
def static_files(path):
    return send_from_directory('static', path)


@app.route('/uploads/<path:path>')
def uploaded_files(path):
    return send_from_directory('uploads', path)


@app.route('/cert.pem')
def download_cert():
    """Allow devices to download the self-signed cert for manual trust installation."""
    cert_path = os.path.join(BASE_DIR, 'cert.pem')
    if not os.path.exists(cert_path):
        return err('Certificate not found', 404)
    return send_from_directory(BASE_DIR, 'cert.pem',
                               as_attachment=True,
                               mimetype='application/x-pem-file')


# ── Logo upload ─────────────────────────────────────────────────────────────

@app.route('/api/settings/logo', methods=['POST'])
@login_required
def upload_logo():
    if 'logo' not in request.files:
        return err('No logo file')
    f = request.files['logo']
    if not allowed_image(f.filename):
        return err('Invalid image format (JPG/PNG/WEBP)')
    fname = 'org_logo' + os.path.splitext(secure_filename(f.filename))[1].lower()
    path  = os.path.join(LOGO_DIR, fname)
    f.save(path)
    rel   = 'uploads/logo/' + fname
    set_setting('org_logo', rel)
    return ok({'logo_path': rel}, 'Logo uploaded')


# ══════════════════════════════════════════════════════════════════════════
#  USERS
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/users', methods=['GET'])
def list_users():
    dept     = request.args.get('department')
    search   = request.args.get('q', '').strip()
    active   = request.args.get('active', '1')
    page     = max(1, int(request.args.get('page', 1)))
    per_page = min(100, max(10, int(request.args.get('per_page', 50))))

    if CLOUD_MODE:
        # face_templates doesn't exist in the cloud (PostgreSQL) schema.
        sql = ('SELECT id,emp_id,name,department,designation,email,phone,photo_path,active,enrolled_at, '
               '0 AS template_count FROM users WHERE 1=1')
    else:
        sql = ('SELECT u.id,u.emp_id,u.name,u.department,u.designation,u.email,u.phone,u.photo_path,u.active,u.enrolled_at, '
               'COALESCE(ft.cnt,0) AS template_count FROM users u '
               'LEFT JOIN (SELECT user_id, COUNT(*) AS cnt FROM face_templates GROUP BY user_id) ft ON ft.user_id=u.id '
               'WHERE 1=1')
    args = []
    if active != 'all':
        sql += ' AND active=?'; args.append(int(active))
    if dept:
        sql += ' AND department=?'; args.append(dept)
    if search:
        sql += ' AND (name LIKE ? OR emp_id LIKE ? OR designation LIKE ?)';
        args += [f'%{search}%', f'%{search}%', f'%{search}%']
    sql += ' ORDER BY name'

    conn  = get_db()
    total = conn.execute(f'SELECT COUNT(*) FROM ({sql})', args).fetchone()[0]
    sql  += f' LIMIT {per_page} OFFSET {(page - 1) * per_page}'
    rows  = conn.execute(sql, args).fetchall()
    conn.close()
    return ok({
        'users':    [dict(r) for r in rows],
        'total':    total,
        'page':     page,
        'per_page': per_page,
        'pages':    max(1, (total + per_page - 1) // per_page),
    })


@app.route('/api/users/<int:uid>', methods=['GET'])
def get_user(uid):
    conn = get_db()
    row  = conn.execute(
        'SELECT id,emp_id,name,department,designation,email,phone,photo_path,active,enrolled_at FROM users WHERE id=?', (uid,)
    ).fetchone()
    conn.close()
    if not row:
        return err('User not found', 404)
    return ok(dict(row))


@app.route('/api/users', methods=['POST'])
def create_user():
    data = request.get_json() or {}
    required = ('emp_id', 'name')
    for f in required:
        if not data.get(f):
            return err(f'Field {f} is required')
    conn = get_db()
    try:
        conn.execute(
            'INSERT INTO users (emp_id,name,department,designation,email,phone) VALUES (?,?,?,?,?,?)',
            (data['emp_id'], data['name'], data.get('department'), data.get('designation'),
             data.get('email'), data.get('phone'))
        )
        conn.commit()
        uid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    except Exception as e:
        return err(str(e))
    finally:
        conn.close()
    return ok({'id': uid}, 'User created', code=201), 201


@app.route('/api/users/<int:uid>', methods=['PUT'])
def update_user(uid):
    data = request.get_json() or {}
    fields = ['name','department','designation','email','phone','active']
    sets   = [f'{f}=?' for f in fields if f in data]
    vals   = [data[f] for f in fields if f in data]
    if not sets:
        return err('Nothing to update')
    conn = get_db()
    conn.execute(f'UPDATE users SET {",".join(sets)} WHERE id=?', vals + [uid])
    conn.commit()
    conn.close()
    face_cache.reload()
    return ok(msg='User updated')


@app.route('/api/users/<int:uid>', methods=['DELETE'])
def delete_user(uid):
    conn = get_db()
    conn.execute('UPDATE users SET active=0 WHERE id=?', (uid,))
    conn.commit()
    conn.close()
    face_cache.reload()
    return ok(msg='User deactivated')


# ── Photo upload ───────────────────────────────────────────────────────────

@app.route('/api/users/<int:uid>/photo', methods=['POST'])
def upload_photo(uid):
    if 'photo' not in request.files:
        return err('No photo file')
    f = request.files['photo']
    if not allowed_image(f.filename):
        return err('Invalid image format')
    fname = secure_filename(f'user_{uid}_{int(datetime.now().timestamp())}.jpg')
    path  = os.path.join(FACE_DIR, fname)
    f.save(path)
    rel   = os.path.relpath(path, BASE_DIR)
    conn  = get_db()
    conn.execute('UPDATE users SET photo_path=? WHERE id=?', (rel, uid))
    conn.commit()
    conn.close()
    return ok({'photo_path': rel}, 'Photo uploaded')


# ── Face enrolment ─────────────────────────────────────────────────────────

@app.route('/api/users/<int:uid>/enroll', methods=['POST'])
def enroll_face(uid):
    """
    Enroll face from uploaded image(s) or existing photo_path.

    Accepts multiple 'photo' files (frontal + slight angles recommended).

    mode=replace (default): the first accepted photo becomes the anchor
    encoding (users.face_encoding, synced to the cloud portal); every
    additional accepted photo is stored as an extra template. Wipes the
    previous gallery (including auto-learned templates) — a fresh baseline.

    mode=append: keeps the existing anchor and gallery; every accepted photo
    is added as an extra template, so live matching covers more poses (e.g.
    high-angle CCTV shots). Falls back to replace if the user has no anchor.
    """
    mode = (request.form.get('mode') or 'replace').lower()
    if mode not in ('replace', 'append'):
        return err("mode must be 'replace' or 'append'")

    paths = []
    if 'photo' in request.files:
        ts = int(datetime.now().timestamp())
        for i, f in enumerate(request.files.getlist('photo')):
            fname = secure_filename(f'enroll_{uid}_{ts}_{i}.jpg')
            path  = os.path.join(FACE_DIR, fname)
            f.save(path)
            paths.append(path)
    else:
        conn = get_db()
        row  = conn.execute('SELECT photo_path FROM users WHERE id=?', (uid,)).fetchone()
        conn.close()
        if not row or not row['photo_path']:
            return err('No photo available for enrollment')
        paths.append(os.path.join(BASE_DIR, row['photo_path']))

    accepted, rejected = [], []
    for path in paths:
        enc, reason = encode_face_detailed(path)
        if enc is None:
            rejected.append(f"{os.path.basename(path)}: {reason}")
        else:
            accepted.append((path, enc))

    if not accepted:
        return err(rejected[0] if len(rejected) == 1
                   else 'All photos rejected — ' + ' | '.join(rejected))

    conn = get_db()
    if mode == 'append':
        row = conn.execute('SELECT face_encoding FROM users WHERE id=?', (uid,)).fetchone()
        if not (row and row['face_encoding']):
            mode = 'replace'   # nothing to append to — establish a baseline

    if mode == 'append':
        for _, extra_enc in accepted:
            conn.execute(
                'INSERT INTO face_templates (user_id, embedding, source) VALUES (?,?,?)',
                (uid, encoding_to_blob(extra_enc), 'enroll')
            )
        msg = f'Added {len(accepted)} photo(s) to face profile'
    else:
        anchor_path, anchor_enc = accepted[0]
        rel = os.path.relpath(anchor_path, BASE_DIR)
        conn.execute(
            'UPDATE users SET face_encoding=?, photo_path=?, enrolled_at=datetime("now","localtime") WHERE id=?',
            (encoding_to_blob(anchor_enc), rel, uid)
        )
        conn.execute('DELETE FROM face_templates WHERE user_id=?', (uid,))
        for _, extra_enc in accepted[1:]:
            conn.execute(
                'INSERT INTO face_templates (user_id, embedding, source) VALUES (?,?,?)',
                (uid, encoding_to_blob(extra_enc), 'enroll')
            )
        msg = f'Face enrolled with {len(accepted)} photo(s)'
    conn.commit()
    conn.close()
    face_cache.reload()

    if rejected:
        msg += f" — {len(rejected)} rejected: " + ' | '.join(rejected)
    return ok(msg=msg)


@app.route('/api/users/<int:uid>/templates', methods=['GET', 'DELETE'])
def manage_templates(uid):
    """Inspect or purge a user's face-template gallery.
    DELETE ?source=auto removes only self-learned templates."""
    if request.method == 'GET':
        return ok({'templates': get_template_stats(uid)})
    source  = request.args.get('source')
    if source and source not in ('enroll', 'merge', 'auto'):
        return err('source must be enroll, merge or auto')
    deleted = clear_face_templates(uid, source)
    return ok(msg=f'Deleted {deleted} template(s)')


# ── Bulk CSV import ────────────────────────────────────────────────────────

@app.route('/api/users/import', methods=['POST'])
def import_users():
    if 'file' not in request.files:
        return err('No file uploaded')
    f       = request.files['file']
    content = f.read().decode('utf-8-sig')
    reader  = csv.DictReader(io.StringIO(content))

    inserted, skipped, errors = 0, 0, []
    conn = get_db()
    for i, row in enumerate(reader, 1):
        emp_id = row.get('emp_id','').strip()
        name   = row.get('name','').strip()
        if not emp_id or not name:
            errors.append(f'Row {i}: emp_id and name required')
            skipped += 1
            continue
        try:
            conn.execute(
                'INSERT OR IGNORE INTO users (emp_id,name,department,designation,email,phone) VALUES (?,?,?,?,?,?)',
                (emp_id, name, row.get('department',''), row.get('designation',''),
                 row.get('email',''), row.get('phone',''))
            )
            inserted += 1
        except Exception as e:
            errors.append(f'Row {i}: {str(e)}')
            skipped += 1
    conn.commit()
    conn.close()
    return ok({'inserted': inserted, 'skipped': skipped, 'errors': errors},
              f'Import complete: {inserted} inserted, {skipped} skipped')


# ══════════════════════════════════════════════════════════════════════════
#  ATTENDANCE
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/attendance', methods=['GET'])
def list_attendance():
    date_from = request.args.get('from', datetime.now().strftime('%Y-%m-%d'))
    date_to   = request.args.get('to',   datetime.now().strftime('%Y-%m-%d'))
    uid       = request.args.get('user_id')
    dept      = request.args.get('department')
    ptype     = request.args.get('punch_type')
    page      = int(request.args.get('page', 1))
    per_page  = int(request.args.get('per_page', 50))

    sql  = '''
        SELECT a.id, a.punch_type, a.confidence, a.snapshot_path, a.punch_time,
               u.emp_id, u.name, u.department, u.designation,
               c.name AS camera_name, c.location
        FROM attendance a
        JOIN users u ON u.id = a.user_id
        LEFT JOIN cameras c ON c.id = a.camera_id
        WHERE DATE(a.punch_time) BETWEEN ? AND ?
    '''
    args = [date_from, date_to]
    if uid:
        sql += ' AND a.user_id=?'; args.append(uid)
    if dept:
        sql += ' AND u.department=?'; args.append(dept)
    if ptype:
        sql += ' AND a.punch_type=?'; args.append(ptype)
    sql += ' ORDER BY a.punch_time DESC'

    conn   = get_db()
    total  = conn.execute(f'SELECT COUNT(*) FROM ({sql})', args).fetchone()[0]
    sql   += f' LIMIT {per_page} OFFSET {(page-1)*per_page}'
    rows   = conn.execute(sql, args).fetchall()
    conn.close()

    return ok({
        'records': [dict(r) for r in rows],
        'total': total, 'page': page, 'per_page': per_page,
        'pages': (total + per_page - 1) // per_page
    })


@app.route('/api/attendance/manual', methods=['POST'])
def manual_attendance():
    data = request.get_json() or {}
    uid  = data.get('user_id')
    pt   = data.get('punch_type', 'IN')
    if not uid:
        return err('user_id required')
    conn = get_db()
    conn.execute(
        'INSERT INTO attendance (user_id, punch_type, confidence) VALUES (?,?,?)',
        (uid, pt, 100.0)
    )
    conn.commit()
    conn.close()
    return ok(msg=f'Manual {pt} logged')


@app.route('/api/attendance/<int:aid>', methods=['DELETE'])
def delete_attendance(aid):
    conn = get_db()
    conn.execute('DELETE FROM attendance WHERE id=?', (aid,))
    conn.commit()
    conn.close()
    return ok(msg='Record deleted')


# ── Today's summary per employee ──────────────────────────────────────────

@app.route('/api/attendance/today-summary', methods=['GET'])
def today_summary():
    today = datetime.now().strftime('%Y-%m-%d')
    dept  = request.args.get('department')
    sql   = '''
        SELECT u.id, u.emp_id, u.name, u.department, u.designation,
               MIN(CASE WHEN a.punch_type='IN'  THEN a.punch_time END) AS first_in,
               MAX(CASE WHEN a.punch_type='OUT' THEN a.punch_time END) AS last_out,
               COUNT(CASE WHEN a.punch_type='IN' THEN 1 END)  AS in_count,
               COUNT(CASE WHEN a.punch_type='OUT' THEN 1 END) AS out_count
        FROM users u
        LEFT JOIN attendance a ON a.user_id=u.id AND DATE(a.punch_time)=?
        WHERE u.active=1
    '''
    args = [today]
    if dept:
        sql += ' AND u.department=?'; args.append(dept)
    sql += ' GROUP BY u.id ORDER BY u.name'

    conn = get_db()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


# ══════════════════════════════════════════════════════════════════════════
#  UNKNOWN FACES
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/unknown-faces', methods=['GET'])
def list_unknown():
    page     = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 24))
    reviewed = request.args.get('reviewed')

    sql  = '''
        SELECT uf.id, uf.snapshot_path, uf.detected_at, uf.reviewed, uf.notes,
               uf.cluster_id, uf.punch_type,
               c.name AS camera_name, c.location
        FROM unknown_faces uf
        LEFT JOIN cameras c ON c.id = uf.camera_id
        WHERE 1=1
    '''
    args = []
    if reviewed is not None:
        sql += ' AND uf.reviewed=?'; args.append(int(reviewed))
    sql += ' ORDER BY uf.detected_at DESC'

    conn  = get_db()
    total = conn.execute(f'SELECT COUNT(*) FROM ({sql})', args).fetchone()[0]
    sql  += f' LIMIT {per_page} OFFSET {(page-1)*per_page}'
    rows  = conn.execute(sql, args).fetchall()
    conn.close()
    return ok({'records': [dict(r) for r in rows], 'total': total})


@app.route('/api/unknown-faces/persons', methods=['GET'])
def list_unknown_persons():
    """Group unknown-face captures by visitor (face-similarity cluster) and
    show how many times each unidentified person has been seen, with their
    IN/OUT counts — like a mini attendance log for unrecognised visitors."""
    page     = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 24))

    sql = '''
        SELECT uf.cluster_id, uf.snapshot_path, uf.detected_at AS last_seen,
               c.name AS camera_name, c.location,
               g.visit_count, g.in_count, g.out_count, g.first_seen, g.unreviewed_count
        FROM unknown_faces uf
        JOIN (
            SELECT cluster_id,
                   COUNT(*)                                          AS visit_count,
                   SUM(CASE WHEN punch_type='IN'  THEN 1 ELSE 0 END)  AS in_count,
                   SUM(CASE WHEN punch_type='OUT' THEN 1 ELSE 0 END)  AS out_count,
                   MIN(detected_at)                                   AS first_seen,
                   MAX(id)                                            AS latest_id,
                   SUM(CASE WHEN reviewed=0 THEN 1 ELSE 0 END)        AS unreviewed_count
            FROM unknown_faces
            WHERE cluster_id IS NOT NULL
            GROUP BY cluster_id
        ) g ON g.latest_id = uf.id
        LEFT JOIN cameras c ON c.id = uf.camera_id
        ORDER BY uf.detected_at DESC
    '''
    conn  = get_db()
    total = conn.execute(
        'SELECT COUNT(DISTINCT cluster_id) FROM unknown_faces WHERE cluster_id IS NOT NULL'
    ).fetchone()[0]
    sql  += f' LIMIT {per_page} OFFSET {(page-1)*per_page}'
    rows  = conn.execute(sql).fetchall()
    conn.close()
    return ok({'records': [dict(r) for r in rows], 'total': total})


@app.route('/api/unknown-faces/persons/<cluster_id>', methods=['GET'])
def unknown_person_visits(cluster_id):
    """Full visit timeline for one unidentified-visitor cluster."""
    sql = '''
        SELECT uf.id, uf.snapshot_path, uf.detected_at, uf.punch_type, uf.reviewed,
               c.name AS camera_name, c.location
        FROM unknown_faces uf
        LEFT JOIN cameras c ON c.id = uf.camera_id
        WHERE uf.cluster_id = ?
        ORDER BY uf.detected_at DESC
    '''
    conn = get_db()
    rows = conn.execute(sql, (cluster_id,)).fetchall()
    conn.close()
    if not rows:
        return err('Visitor not found', 404)
    return ok({'records': [dict(r) for r in rows]})


@app.route('/api/unknown-faces/<int:fid>/review', methods=['PUT'])
def review_unknown(fid):
    data  = request.get_json() or {}
    notes = data.get('notes', '')
    conn  = get_db()
    conn.execute('UPDATE unknown_faces SET reviewed=1, notes=? WHERE id=?', (notes, fid))
    conn.commit()
    conn.close()
    return ok(msg='Marked as reviewed')


@app.route('/api/unknown-faces/<int:fid>/assign', methods=['POST'])
def assign_unknown(fid):
    """Link an unknown-face snapshot to an employee and blend it into that
    employee's stored face encoding, so future captures of this person are
    recognised instead of logged as Unknown."""
    data    = request.get_json() or {}
    user_id = data.get('user_id')
    if not user_id:
        return err('user_id is required')

    conn = get_db()
    uf   = conn.execute('SELECT snapshot_path, cluster_id FROM unknown_faces WHERE id=?', (fid,)).fetchone()
    if not uf:
        conn.close()
        return err('Unknown face record not found', 404)

    user = conn.execute('SELECT id, name, face_encoding FROM users WHERE id=?', (user_id,)).fetchone()
    if not user:
        conn.close()
        return err('Employee not found', 404)

    path = os.path.join(BASE_DIR, uf['snapshot_path']) if uf['snapshot_path'] else None
    if not path or not os.path.exists(path):
        conn.close()
        return err('Snapshot image is missing')

    new_enc, reason = encode_face_detailed(path)
    if new_enc is None:
        conn.close()
        return err(reason or 'No face detected in this snapshot')

    # Store as an extra template instead of averaging into the anchor —
    # averaging embeddings from different poses blurs both into a weaker
    # in-between vector; a separate template keeps each pose sharp.
    if user['face_encoding']:
        conn.execute(
            'INSERT INTO face_templates (user_id, embedding, source) VALUES (?,?,?)',
            (user_id, encoding_to_blob(new_enc.astype(np.float32)), 'merge')
        )
    else:
        conn.execute(
            'UPDATE users SET face_encoding=?, enrolled_at=datetime("now","localtime") WHERE id=?',
            (encoding_to_blob(new_enc.astype(np.float32)), user_id)
        )
    if uf['cluster_id']:
        # Same visitor was captured multiple times — clear the whole queue for them
        conn.execute(
            'UPDATE unknown_faces SET reviewed=1, notes=? WHERE cluster_id=?',
            (f"Assigned to {user['name']}", uf['cluster_id'])
        )
    else:
        conn.execute(
            'UPDATE unknown_faces SET reviewed=1, notes=? WHERE id=?',
            (f"Assigned to {user['name']}", fid)
        )
    conn.commit()
    conn.close()
    face_cache.reload()
    return ok(msg=f"Face assigned to {user['name']} — recognition updated")


@app.route('/api/unknown-faces/<int:fid>', methods=['DELETE'])
def delete_unknown(fid):
    conn = get_db()
    row  = conn.execute('SELECT snapshot_path FROM unknown_faces WHERE id=?', (fid,)).fetchone()
    if row and row['snapshot_path']:
        full = os.path.join(BASE_DIR, row['snapshot_path'])
        if os.path.exists(full):
            os.remove(full)
    conn.execute('DELETE FROM unknown_faces WHERE id=?', (fid,))
    conn.commit()
    conn.close()
    return ok(msg='Deleted')


@app.route('/api/unknown-faces/bulk-delete', methods=['POST'])
def bulk_delete_unknown():
    """Delete multiple unknown face records by ID list."""
    data = request.get_json() or {}
    ids  = data.get('ids', [])
    if not ids:
        return err('No IDs provided')
    conn    = get_db()
    deleted = 0
    for fid in ids:
        row = conn.execute('SELECT snapshot_path FROM unknown_faces WHERE id=?', (fid,)).fetchone()
        if row:
            if row['snapshot_path']:
                full = os.path.join(BASE_DIR, row['snapshot_path'])
                if os.path.exists(full):
                    try: os.remove(full)
                    except: pass
            conn.execute('DELETE FROM unknown_faces WHERE id=?', (fid,))
            deleted += 1
    conn.commit()
    conn.close()
    return ok({'deleted': deleted}, f'{deleted} record(s) deleted')


# ══════════════════════════════════════════════════════════════════════════
#  CAMERAS
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/cameras', methods=['GET'])
def list_cameras():
    conn  = get_db()
    rows  = conn.execute('SELECT * FROM cameras ORDER BY id').fetchall()
    conn.close()
    status = camera_manager.status()
    result = []
    for r in rows:
        d = dict(r)
        cam_status = status.get(r['id'], {})
        d['streaming'] = cam_status.get('alive', False)   # thread is running
        d['connected'] = cam_status.get('frames', False)  # frames are flowing
        result.append(d)
    return ok(result)


@app.route('/api/cameras', methods=['POST'])
def add_camera():
    data = request.get_json() or {}
    if not data.get('name') or not data.get('rtsp_url'):
        return err('name and rtsp_url required')
    conn = get_db()
    conn.execute(
        'INSERT INTO cameras (name, rtsp_url, location, direction) VALUES (?,?,?,?)',
        (data['name'], data['rtsp_url'], data.get('location', ''), data.get('direction', 'BOTH'))
    )
    conn.commit()
    cid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    conn.close()
    return ok({'id': cid}, 'Camera added'), 201


@app.route('/api/cameras/<int:cid>', methods=['PUT'])
def update_camera(cid):
    data   = request.get_json() or {}
    fields = ['name', 'rtsp_url', 'location', 'direction', 'active']
    sets   = [f'{f}=?' for f in fields if f in data]
    vals   = [data[f] for f in fields if f in data]
    if not sets:
        return err('Nothing to update')
    conn = get_db()
    conn.execute(f'UPDATE cameras SET {",".join(sets)} WHERE id=?', vals + [cid])
    conn.commit()
    conn.close()
    return ok(msg='Camera updated')


@app.route('/api/cameras/<int:cid>', methods=['DELETE'])
def delete_camera(cid):
    camera_manager.stop_camera(cid)
    conn = get_db()
    conn.execute('DELETE FROM cameras WHERE id=?', (cid,))
    conn.commit()
    conn.close()
    return ok(msg='Camera deleted')


@app.route('/api/cameras/<int:cid>/start', methods=['POST'])
def start_camera(cid):
    conn = get_db()
    row  = conn.execute('SELECT rtsp_url, direction FROM cameras WHERE id=?', (cid,)).fetchone()
    conn.close()
    if not row:
        return err('Camera not found', 404)
    camera_manager.start_camera(cid, row['rtsp_url'], row['direction'])
    return ok(msg='Camera stream started')


@app.route('/api/cameras/<int:cid>/stop', methods=['POST'])
def stop_camera(cid):
    camera_manager.stop_camera(cid)
    return ok(msg='Camera stream stopped')


@app.route('/video_feed/<int:cid>')
def video_feed(cid):
    return Response(gen_mjpeg(cid),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


# ══════════════════════════════════════════════════════════════════════════
#  DASHBOARD
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/dashboard', methods=['GET'])
def dashboard():
    today  = datetime.now().strftime('%Y-%m-%d')
    conn   = get_db()

    total_emp   = conn.execute('SELECT COUNT(*) FROM users WHERE active=1').fetchone()[0]
    present     = conn.execute(
        "SELECT COUNT(DISTINCT user_id) FROM attendance WHERE DATE(punch_time)=? AND punch_type='IN'",
        (today,)
    ).fetchone()[0]
    absent      = total_emp - present
    unknown_cnt = conn.execute(
        "SELECT COUNT(*) FROM unknown_faces WHERE DATE(detected_at)=?", (today,)
    ).fetchone()[0]
    unknown_unreviewed = conn.execute(
        "SELECT COUNT(*) FROM unknown_faces WHERE reviewed=0"
    ).fetchone()[0]
    late_time   = get_setting('work_start', '09:00')
    late_thresh = int(get_setting('late_threshold', 15))
    late_dt     = (datetime.strptime(f"{today} {late_time}", '%Y-%m-%d %H:%M')
                   + timedelta(minutes=late_thresh)).strftime('%Y-%m-%d %H:%M')
    late_count  = conn.execute(
        "SELECT COUNT(DISTINCT user_id) FROM attendance "
        "WHERE DATE(punch_time)=? AND punch_type='IN' AND punch_time > ?",
        (today, late_dt)
    ).fetchone()[0]

    # Dept-wise presence
    dept_stats = conn.execute('''
        SELECT u.department,
               COUNT(DISTINCT u.id)       AS total,
               COUNT(DISTINCT a.user_id)  AS present
        FROM users u
        LEFT JOIN attendance a ON a.user_id=u.id AND DATE(a.punch_time)=? AND a.punch_type='IN'
        WHERE u.active=1 AND u.department IS NOT NULL
        GROUP BY u.department ORDER BY u.department
    ''', (today,)).fetchall()

    # Recent 10 attendance
    recent = conn.execute('''
        SELECT a.punch_type, a.punch_time, a.confidence, u.name, u.emp_id
        FROM attendance a JOIN users u ON u.id=a.user_id
        ORDER BY a.id DESC LIMIT 10
    ''').fetchall()

    # Weekly trend (last 7 days)
    trend = []
    for i in range(6, -1, -1):
        d  = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        p  = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM attendance WHERE DATE(punch_time)=? AND punch_type='IN'", (d,)
        ).fetchone()[0]
        trend.append({'date': d, 'present': p})

    # Hourly punch-in heatmap (today)
    hourly = conn.execute('''
        SELECT CAST(strftime('%H', punch_time) AS INTEGER) AS hour,
               COUNT(*) AS count
        FROM attendance
        WHERE DATE(punch_time)=? AND punch_type='IN'
        GROUP BY hour ORDER BY hour
    ''', (today,)).fetchall()
    hourly_map = {r['hour']: r['count'] for r in hourly}
    hourly_data = [{'hour': h, 'count': hourly_map.get(h, 0)} for h in range(6, 21)]

    # Top 5 late arrivals this month
    this_month = datetime.now().strftime('%Y-%m')
    work_start = get_setting('work_start', '09:00')
    late_thresh = int(get_setting('late_threshold', 15))
    top_late = conn.execute('''
        SELECT u.name, u.emp_id, u.department,
               COUNT(*) AS late_days
        FROM attendance a JOIN users u ON u.id=a.user_id
        WHERE a.punch_type='IN'
          AND strftime('%Y-%m', a.punch_time)=?
          AND TIME(a.punch_time) > TIME(?, '+' || ? || ' minutes')
        GROUP BY u.id ORDER BY late_days DESC LIMIT 5
    ''', (this_month, work_start + ':00', str(late_thresh))).fetchall()

    # Camera status summary
    all_cameras  = conn.execute('SELECT COUNT(*) FROM cameras WHERE active=1').fetchone()[0]
    cam_status   = camera_manager.status()
    cams_live    = sum(1 for alive in cam_status.values() if alive)

    # Unknown face alerts — last 5 today with snapshot
    unknown_alerts = conn.execute('''
        SELECT uf.id, uf.snapshot_path, uf.detected_at,
               c.name AS camera_name
        FROM unknown_faces uf
        LEFT JOIN cameras c ON c.id=uf.camera_id
        WHERE DATE(uf.detected_at)=? AND uf.reviewed=0
        ORDER BY uf.detected_at DESC LIMIT 5
    ''', (today,)).fetchall()

    conn.close()
    return ok({
        'total_employees': total_emp,
        'present_today':   present,
        'absent_today':    absent,
        'late_today':      late_count,
        'unknown_today':   unknown_cnt,
        'unknown_unreviewed_total': unknown_unreviewed,
        'dept_stats':      [dict(r) for r in dept_stats],
        'recent_activity': [dict(r) for r in recent],
        'weekly_trend':    trend,
        # New widgets
        'hourly_heatmap':  hourly_data,
        'top_late_month':  [dict(r) for r in top_late],
        'camera_status':   {'total': all_cameras, 'live': cams_live, 'offline': all_cameras - cams_live},
        'unknown_alerts':  [dict(r) for r in unknown_alerts],
    })


# ══════════════════════════════════════════════════════════════════════════
#  REPORTS
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/reports/attendance', methods=['GET'])
def report_attendance():
    date_from = request.args.get('from', datetime.now().strftime('%Y-%m-%d'))
    date_to   = request.args.get('to',   datetime.now().strftime('%Y-%m-%d'))
    dept      = request.args.get('department')
    fmt       = request.args.get('format', 'json')

    sql = '''
        SELECT u.emp_id, u.name, u.department, u.designation,
               DATE(a.punch_time)                                     AS date,
               MIN(CASE WHEN a.punch_type='IN'  THEN a.punch_time END) AS first_in,
               MAX(CASE WHEN a.punch_type='OUT' THEN a.punch_time END) AS last_out,
               COUNT(CASE WHEN a.punch_type='IN' THEN 1 END)          AS in_count
        FROM users u
        LEFT JOIN attendance a ON a.user_id=u.id AND DATE(a.punch_time) BETWEEN ? AND ?
        WHERE u.active=1
    '''
    args = [date_from, date_to]
    if dept:
        sql += ' AND u.department=?'; args.append(dept)
    sql += ' GROUP BY u.id, DATE(a.punch_time) ORDER BY date DESC, u.name'

    conn = get_db()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    data = [dict(r) for r in rows]

    if fmt == 'csv':
        def gen_csv():
            header = ['Emp ID', 'Name', 'Department', 'Designation', 'Date', 'First IN', 'Last OUT', 'IN Count', 'Status']
            yield ','.join(header) + '\n'
            work_start = get_setting('work_start', '09:00')
            late_mins  = int(get_setting('late_threshold', 15))
            for r in data:
                if r['first_in']:
                    try:
                        fi    = datetime.strptime(r['first_in'], '%Y-%m-%d %H:%M:%S')
                        ws    = datetime.strptime(f"{r['date']} {work_start}", '%Y-%m-%d %H:%M')
                        ws   += timedelta(minutes=late_mins)
                        status = 'Late' if fi > ws else 'On Time'
                    except:
                        status = 'Present'
                elif r['date']:
                    status = 'Absent'
                else:
                    status = '-'
                row = [
                    r['emp_id'] or '', r['name'], r['department'] or '',
                    r['designation'] or '', r['date'] or '',
                    r['first_in'] or '', r['last_out'] or '',
                    str(r['in_count'] or 0), status
                ]
                yield ','.join(f'"{v}"' for v in row) + '\n'
        return Response(gen_csv(), mimetype='text/csv',
                        headers={'Content-Disposition': f'attachment; filename=attendance_{date_from}_{date_to}.csv'})

    return ok(data)


@app.route('/api/reports/monthly-summary', methods=['GET'])
def monthly_summary():
    month  = request.args.get('month', datetime.now().strftime('%Y-%m'))
    fmt    = request.args.get('format', 'json')
    dept   = request.args.get('department', '')
    year, mon = map(int, month.split('-'))
    # Calculate month date range
    date_from = f'{month}-01'
    last_day  = (datetime(year, mon, 1).replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    date_to   = last_day.strftime('%Y-%m-%d')

    working_days = _working_days_in_range(date_from, date_to)
    wday_count   = len(working_days)
    holidays_in_month = _get_holidays_set(date_from, date_to)

    work_start = get_setting('work_start', '09:00')
    late_mins  = int(get_setting('late_threshold', '15'))

    conn = get_db()
    sql = '''
        SELECT u.id, u.emp_id, u.name, u.department, u.designation,
               COUNT(DISTINCT CASE WHEN a.punch_type='IN' THEN DATE(a.punch_time) END) AS days_present
        FROM users u
        LEFT JOIN attendance a ON a.user_id=u.id AND strftime('%Y-%m', a.punch_time)=?
        WHERE u.active=1
    '''
    args = [month]
    if dept:
        sql += ' AND u.department=?'; args.append(dept)
    sql += ' GROUP BY u.id ORDER BY u.name'
    rows = conn.execute(sql, args).fetchall()

    # Late count per user for the month
    late_rows = conn.execute('''
        SELECT u.id,
               COUNT(DISTINCT CASE
                   WHEN a.punch_type='IN'
                        AND time(a.punch_time) > time(?, '+'||? ||' minutes')
                   THEN DATE(a.punch_time) END) AS late_days
        FROM users u
        LEFT JOIN attendance a ON a.user_id=u.id AND strftime('%Y-%m', a.punch_time)=?
        WHERE u.active=1
        GROUP BY u.id
    ''', (work_start, late_mins, month)).fetchall()
    late_map = {r['id']: r['late_days'] for r in late_rows}
    conn.close()

    data = []
    for r in rows:
        pct = round((r['days_present'] / wday_count) * 100, 1) if wday_count else 0
        data.append({
            'emp_id':       r['emp_id'],
            'name':         r['name'],
            'department':   r['department'] or '',
            'designation':  r['designation'] or '',
            'days_present': r['days_present'],
            'days_absent':  max(0, wday_count - r['days_present']),
            'days_late':    late_map.get(r['id'], 0),
            'working_days': wday_count,
            'attendance_pct': pct,
        })

    if fmt == 'csv':
        def gen():
            yield 'Emp ID,Name,Department,Designation,Working Days,Days Present,Days Absent,Days Late,Attendance %\n'
            for row in data:
                yield (f'"{row["emp_id"]}","{row["name"]}","{row["department"]}",'
                       f'"{row["designation"]}",{row["working_days"]},{row["days_present"]},'
                       f'{row["days_absent"]},{row["days_late"]},{row["attendance_pct"]}\n')
        return Response(gen(), mimetype='text/csv',
                        headers={'Content-Disposition': f'attachment; filename=monthly_{month}.csv'})

    return ok({
        'rows':         data,
        'working_days': wday_count,
        'holidays':     sorted(holidays_in_month),
        'month':        month,
    })


# ══════════════════════════════════════════════════════════════════════════
#  SETTINGS
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/settings', methods=['GET'])
def get_settings():
    conn = get_db()
    rows = conn.execute('SELECT key, value, label, category FROM settings ORDER BY category, key').fetchall()
    depts = conn.execute('SELECT id, name FROM departments ORDER BY name').fetchall()
    conn.close()
    return ok({
        'settings': [dict(r) for r in rows],
        'departments': [dict(r) for r in depts]
    })


@app.route('/api/settings', methods=['PUT'])
def save_settings():
    data = request.get_json() or {}
    conn = get_db()
    for key, value in data.items():
        conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                     (key, str(value)))
    conn.commit()
    conn.close()
    return ok(msg='Settings saved')


@app.route('/api/departments', methods=['GET'])
def list_departments():
    conn = get_db()
    rows = conn.execute('SELECT name FROM departments ORDER BY name').fetchall()
    conn.close()
    return ok([r['name'] for r in rows])


@app.route('/api/departments', methods=['POST'])
def add_department():
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    if not name:
        return err('name required')
    conn = get_db()
    conn.execute('INSERT OR IGNORE INTO departments (name) VALUES (?)', (name,))
    conn.commit()
    conn.close()
    return ok(msg='Department added')


# ══════════════════════════════════════════════════════════════════════════
#  ADMIN USER MANAGEMENT  (admin role only)
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/admin-users', methods=['GET'])
@role_required('admin')
def list_admin_users():
    conn = get_db()
    rows = conn.execute(
        'SELECT id,username,role,full_name,department,active,last_login,created_at FROM admin_users ORDER BY role,username'
    ).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@app.route('/api/admin-users', methods=['POST'])
@role_required('admin')
def create_admin_user():
    data = request.get_json() or {}
    username  = data.get('username', '').strip()
    password  = data.get('password', '')
    role      = data.get('role', 'manager')
    full_name = data.get('full_name', '').strip()
    dept      = data.get('department', '').strip()
    if not username or not password:
        return err('Username and password required')
    if role not in ('admin', 'manager', 'guard'):
        return err('Invalid role')
    if len(password) < 6:
        return err('Password must be at least 6 characters')
    conn = get_db()
    try:
        conn.execute(
            'INSERT INTO admin_users (username,password_hash,role,full_name,department) VALUES (?,?,?,?,?)',
            (username, hash_password(password), role, full_name, dept)
        )
        conn.commit()
        uid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    except Exception as e:
        return err(str(e))
    finally:
        conn.close()
    return ok({'id': uid}, 'User created'), 201


@app.route('/api/admin-users/<int:uid>', methods=['PUT'])
@role_required('admin')
def update_admin_user(uid):
    data   = request.get_json() or {}
    fields = ['full_name', 'role', 'department', 'active']
    sets   = [f'{f}=?' for f in fields if f in data]
    vals   = [data[f]  for f in fields if f in data]
    if 'password' in data and data['password']:
        if len(data['password']) < 6:
            return err('Password must be at least 6 characters')
        sets.append('password_hash=?')
        vals.append(hash_password(data['password']))
    if not sets:
        return err('Nothing to update')
    conn = get_db()
    conn.execute(f'UPDATE admin_users SET {",".join(sets)} WHERE id=?', vals + [uid])
    conn.commit()
    conn.close()
    return ok(msg='User updated')


@app.route('/api/admin-users/<int:uid>', methods=['DELETE'])
@role_required('admin')
def delete_admin_user(uid):
    if uid == 1:
        return err('Cannot delete the primary admin account')
    conn = get_db()
    conn.execute('DELETE FROM admin_users WHERE id=?', (uid,))
    conn.commit()
    conn.close()
    return ok(msg='User deleted')


# ══════════════════════════════════════════════════════════════════════════
#  EXTENDED REPORTS
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/reports/late-arrivals', methods=['GET'])
def report_late_arrivals():
    date_from = request.args.get('from', datetime.now().strftime('%Y-%m-%d'))
    date_to   = request.args.get('to',   datetime.now().strftime('%Y-%m-%d'))
    dept      = request.args.get('department')
    fmt       = request.args.get('format', 'json')

    work_start = get_setting('work_start', '09:00')
    late_mins  = int(get_setting('late_threshold', '15'))

    sql = '''
        SELECT u.emp_id, u.name, u.department, u.designation,
               DATE(a.punch_time) AS date,
               MIN(a.punch_time)  AS first_in
        FROM attendance a
        JOIN users u ON u.id = a.user_id
        WHERE a.punch_type = 'IN'
          AND DATE(a.punch_time) BETWEEN ? AND ?
    '''
    args = [date_from, date_to]
    if dept:
        sql += ' AND u.department=?'; args.append(dept)
    sql += ' GROUP BY u.id, DATE(a.punch_time) ORDER BY date DESC, first_in DESC'

    conn = get_db()
    rows = conn.execute(sql, args).fetchall()
    conn.close()

    late = []
    for r in rows:
        d = r['date']
        fi_str = r['first_in']
        if not fi_str:
            continue
        try:
            fi = datetime.strptime(fi_str, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            fi = datetime.strptime(fi_str, '%Y-%m-%d %H:%M')
        deadline = datetime.strptime(f"{d} {work_start}", '%Y-%m-%d %H:%M') + timedelta(minutes=late_mins)
        if fi > deadline:
            mins_late = int((fi - deadline).total_seconds() / 60)
            late.append({**dict(r), 'first_in': fi_str, 'minutes_late': mins_late,
                         'deadline': deadline.strftime('%H:%M')})

    if fmt == 'csv':
        def gen():
            yield 'Emp ID,Name,Department,Designation,Date,First IN,Deadline,Minutes Late\n'
            for r in late:
                yield f'"{r["emp_id"]}","{r["name"]}","{r["department"] or ""}","{r["designation"] or ""}","{r["date"]}","{r["first_in"]}","{r["deadline"]}",{r["minutes_late"]}\n'
        return Response(gen(), mimetype='text/csv',
                        headers={'Content-Disposition': f'attachment; filename=late_arrivals_{date_from}_{date_to}.csv'})
    return ok(late)


@app.route('/api/reports/absentees', methods=['GET'])
def report_absentees():
    date_from = request.args.get('from', datetime.now().strftime('%Y-%m-%d'))
    date_to   = request.args.get('to',   datetime.now().strftime('%Y-%m-%d'))
    dept      = request.args.get('department')
    fmt       = request.args.get('format', 'json')

    # Build a date list
    start = datetime.strptime(date_from, '%Y-%m-%d')
    end   = datetime.strptime(date_to,   '%Y-%m-%d')
    dates = [(start + timedelta(days=i)).strftime('%Y-%m-%d')
             for i in range((end - start).days + 1)]

    sql  = 'SELECT id, emp_id, name, department, designation FROM users WHERE active=1'
    args = []
    if dept:
        sql += ' AND department=?'; args.append(dept)
    conn  = get_db()
    users = conn.execute(sql, args).fetchall()

    absent = []
    for d in dates:
        present_ids = {r[0] for r in conn.execute(
            "SELECT DISTINCT user_id FROM attendance WHERE DATE(punch_time)=? AND punch_type='IN'", (d,)
        ).fetchall()}
        for u in users:
            if u['id'] not in present_ids:
                absent.append({'date': d, 'emp_id': u['emp_id'], 'name': u['name'],
                               'department': u['department'], 'designation': u['designation']})
    conn.close()
    absent.sort(key=lambda x: (x['date'], x['name']))

    if fmt == 'csv':
        def gen():
            yield 'Date,Emp ID,Name,Department,Designation\n'
            for r in absent:
                yield f'"{r["date"]}","{r["emp_id"]}","{r["name"]}","{r["department"] or ""}","{r["designation"] or ""}"\n'
        return Response(gen(), mimetype='text/csv',
                        headers={'Content-Disposition': f'attachment; filename=absentees_{date_from}_{date_to}.csv'})
    return ok(absent)


@app.route('/api/reports/working-hours', methods=['GET'])
def report_working_hours():
    date_from = request.args.get('from', datetime.now().strftime('%Y-%m-%d'))
    date_to   = request.args.get('to',   datetime.now().strftime('%Y-%m-%d'))
    dept      = request.args.get('department')
    fmt       = request.args.get('format', 'json')

    work_end_str   = get_setting('work_end', '18:00')
    work_start_str = get_setting('work_start', '09:00')
    std_hours = (datetime.strptime(work_end_str, '%H:%M') -
                 datetime.strptime(work_start_str, '%H:%M')).seconds / 3600

    sql = '''
        SELECT u.emp_id, u.name, u.department, u.designation,
               DATE(a.punch_time)                                          AS date,
               MIN(CASE WHEN a.punch_type='IN'  THEN a.punch_time END)    AS first_in,
               MAX(CASE WHEN a.punch_type='OUT' THEN a.punch_time END)    AS last_out
        FROM users u
        JOIN attendance a ON a.user_id = u.id
        WHERE DATE(a.punch_time) BETWEEN ? AND ? AND u.active=1
    '''
    args = [date_from, date_to]
    if dept:
        sql += ' AND u.department=?'; args.append(dept)
    sql += ' GROUP BY u.id, DATE(a.punch_time) ORDER BY date DESC, u.name'

    conn = get_db()
    rows = conn.execute(sql, args).fetchall()
    conn.close()

    result = []
    for r in rows:
        fi, lo = r['first_in'], r['last_out']
        hours_worked, overtime = None, None
        if fi and lo:
            try:
                fmt_in  = '%Y-%m-%d %H:%M:%S' if len(fi) > 16 else '%Y-%m-%d %H:%M'
                fmt_out = '%Y-%m-%d %H:%M:%S' if len(lo) > 16 else '%Y-%m-%d %H:%M'
                delta   = datetime.strptime(lo, fmt_out) - datetime.strptime(fi, fmt_in)
                hours_worked = round(delta.total_seconds() / 3600, 2)
                overtime     = round(max(0, hours_worked - std_hours), 2)
            except Exception:
                pass
        result.append({**dict(r), 'hours_worked': hours_worked,
                       'overtime_hours': overtime, 'std_hours': std_hours})

    if fmt == 'csv':
        def gen():
            yield 'Emp ID,Name,Department,Date,First IN,Last OUT,Hours Worked,Overtime,Status\n'
            for r in result:
                hw  = r['hours_worked']
                ot  = r['overtime_hours']
                st  = ('Overtime' if (ot or 0) > 0 else 'Normal') if hw else 'No OUT punch'
                yield (f'"{r["emp_id"]}","{r["name"]}","{r["department"] or ""}","{r["date"]}",'
                       f'"{r["first_in"] or ""}","{r["last_out"] or ""}",{hw or ""},{ ot or ""},"{st}"\n')
        return Response(gen(), mimetype='text/csv',
                        headers={'Content-Disposition': f'attachment; filename=working_hours_{date_from}_{date_to}.csv'})
    return ok(result)


@app.route('/api/reports/employee-history', methods=['GET'])
def report_employee_history():
    uid       = request.args.get('user_id')
    date_from = request.args.get('from', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
    date_to   = request.args.get('to',   datetime.now().strftime('%Y-%m-%d'))
    if not uid:
        return err('user_id required')

    conn = get_db()
    user = conn.execute(
        'SELECT id,emp_id,name,department,designation,email,phone,photo_path,enrolled_at FROM users WHERE id=?', (uid,)
    ).fetchone()
    if not user:
        conn.close()
        return err('Employee not found', 404)

    records = conn.execute('''
        SELECT a.id, a.punch_type, a.punch_time, a.confidence, a.snapshot_path,
               c.name AS camera_name, c.location
        FROM attendance a
        LEFT JOIN cameras c ON c.id = a.camera_id
        WHERE a.user_id=? AND DATE(a.punch_time) BETWEEN ? AND ?
        ORDER BY a.punch_time DESC
    ''', (uid, date_from, date_to)).fetchall()

    # Daily summary
    daily = conn.execute('''
        SELECT DATE(punch_time) AS date,
               MIN(CASE WHEN punch_type='IN'  THEN punch_time END) AS first_in,
               MAX(CASE WHEN punch_type='OUT' THEN punch_time END) AS last_out,
               COUNT(*) AS total_punches
        FROM attendance WHERE user_id=? AND DATE(punch_time) BETWEEN ? AND ?
        GROUP BY DATE(punch_time) ORDER BY date DESC
    ''', (uid, date_from, date_to)).fetchall()

    conn.close()
    return ok({
        'employee': dict(user),
        'records':  [dict(r) for r in records],
        'daily':    [dict(r) for r in daily],
        'total_days_present': len(daily),
    })


# ══════════════════════════════════════════════════════════════════════════
#  HOLIDAYS
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/holidays', methods=['GET'])
@login_required
def list_holidays():
    year = request.args.get('year', datetime.now().strftime('%Y'))
    conn = get_db()
    rows = conn.execute(
        "SELECT id, date, name, type FROM holidays WHERE strftime('%Y', date)=? ORDER BY date",
        (year,)
    ).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@app.route('/api/holidays', methods=['POST'])
@role_required('admin', 'manager')
def add_holiday():
    data = request.get_json() or {}
    date = data.get('date', '').strip()
    name = data.get('name', '').strip()
    htype = data.get('type', 'national')
    if not date or not name:
        return err('date and name required')
    conn = get_db()
    try:
        conn.execute('INSERT INTO holidays (date, name, type) VALUES (?,?,?)', (date, name, htype))
        conn.commit()
    except Exception as e:
        conn.close()
        return err('Holiday on this date already exists')
    conn.close()
    audit('ADD_HOLIDAY', date, name)
    return ok(msg='Holiday added')


@app.route('/api/holidays/<int:hid>', methods=['PUT'])
@role_required('admin', 'manager')
def update_holiday(hid):
    data = request.get_json() or {}
    conn = get_db()
    conn.execute('UPDATE holidays SET date=?, name=?, type=? WHERE id=?',
                 (data.get('date'), data.get('name'), data.get('type', 'national'), hid))
    conn.commit()
    conn.close()
    audit('EDIT_HOLIDAY', str(hid), data.get('name', ''))
    return ok(msg='Holiday updated')


@app.route('/api/holidays/<int:hid>', methods=['DELETE'])
@role_required('admin', 'manager')
def delete_holiday(hid):
    conn = get_db()
    conn.execute('DELETE FROM holidays WHERE id=?', (hid,))
    conn.commit()
    conn.close()
    audit('DELETE_HOLIDAY', str(hid))
    return ok(msg='Holiday deleted')


# ══════════════════════════════════════════════════════════════════════════
#  AUDIT LOG
# ══════════════════════════════════════════════════════════════════════════

@app.route('/api/audit-log', methods=['GET'])
@role_required('admin')
def get_audit_log():
    date_from = request.args.get('from', (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'))
    date_to   = request.args.get('to',   datetime.now().strftime('%Y-%m-%d'))
    username  = request.args.get('username', '')
    page      = int(request.args.get('page', 1))
    per_page  = int(request.args.get('per_page', 50))
    offset    = (page - 1) * per_page

    sql  = "SELECT * FROM audit_log WHERE DATE(created_at) BETWEEN ? AND ?"
    args = [date_from, date_to]
    if username:
        sql += ' AND username=?'; args.append(username)
    sql += ' ORDER BY created_at DESC'

    conn  = get_db()
    total = conn.execute(f"SELECT COUNT(*) FROM ({sql})", args).fetchone()[0]
    rows  = conn.execute(sql + f' LIMIT {per_page} OFFSET {offset}', args).fetchall()

    # Distinct usernames for filter dropdown
    users = conn.execute(
        "SELECT DISTINCT username FROM audit_log ORDER BY username"
    ).fetchall()
    conn.close()

    return ok({
        'records': [dict(r) for r in rows],
        'total':   total,
        'pages':   max(1, (total + per_page - 1) // per_page),
        'page':    page,
        'users':   [r['username'] for r in users],
    })


# ══════════════════════════════════════════════════════════════════════════
#  THEME / FAVICON
# ══════════════════════════════════════════════════════════════════════════

FAVICON_DIR = os.path.join(os.path.dirname(__file__), 'uploads', 'logo')

@app.route('/api/settings/favicon', methods=['POST'])
@role_required('admin')
def upload_favicon():
    if 'favicon' not in request.files:
        return err('No favicon file')
    f = request.files['favicon']
    if not f.filename:
        return err('Empty filename')
    os.makedirs(FAVICON_DIR, exist_ok=True)
    ext  = f.filename.rsplit('.', 1)[-1].lower()
    name = f'favicon.{ext}'
    path = os.path.join(FAVICON_DIR, name)
    f.save(path)
    rel  = f'uploads/logo/{name}'
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('favicon_path', ?)", (rel,))
    conn.commit(); conn.close()
    audit('UPLOAD_FAVICON', rel)
    return ok({'favicon_path': rel}, msg='Favicon uploaded')


@app.route('/api/settings/theme', methods=['PUT'])
@role_required('admin')
def save_theme():
    data = request.get_json() or {}
    allowed = {'primary_color', 'accent_color', 'org_name', 'org_title'}
    conn = get_db()
    for k, v in data.items():
        if k in allowed:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, str(v)))
    conn.commit(); conn.close()
    audit('SAVE_THEME', '', str(data))
    return ok(msg='Theme saved')


# ══════════════════════════════════════════════════════════════════════════
#  CLOUD SYNC  — ingestion endpoints (cloud portal only)
#  All endpoints require X-API-Key header matching the 'sync_api_key' setting.
# ══════════════════════════════════════════════════════════════════════════

def _verify_sync_key():
    """Return True if the request carries a valid sync API key."""
    expected = get_setting('sync_api_key', '')
    if not expected:
        return False   # key not configured → reject everything
    return request.headers.get('X-API-Key', '') == expected


@app.route('/api/sync/attendance', methods=['POST'])
def sync_receive_attendance():
    """Receive attendance records pushed by a local site."""
    if not _verify_sync_key():
        return jsonify({'status': 'error', 'message': 'Unauthorised'}), 401
    body    = request.get_json() or {}
    site_id = body.get('site_id', 'unknown')
    records = body.get('records', [])
    if not records:
        return ok(msg='No records')

    conn    = get_db()
    inserted = 0

    # Cloud uses PostgreSQL — detect placeholder style
    _pg = CLOUD_MODE

    for r in records:
        try:
            # Resolve user by emp_id (cloud DB may have different internal IDs)
            emp_id = r.get('emp_id')
            if _pg:
                cur = conn.cursor()
                cur.execute('SELECT id FROM users WHERE emp_id=%s', (emp_id,))
                user_row = cur.fetchone()
            else:
                user_row = conn.execute('SELECT id FROM users WHERE emp_id=?', (emp_id,)).fetchone()

            if not user_row:
                continue   # employee not yet synced to cloud
            uid = user_row['id']

            if _pg:
                cur.execute('''
                    INSERT INTO attendance
                        (user_id, camera_id, site_id, punch_type, confidence, snapshot_path, punch_time)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                ''', (uid, r.get('camera_id'), site_id, r['punch_type'],
                      r.get('confidence', 0), r.get('snapshot_path'), r.get('punch_time')))
            else:
                conn.execute('''
                    INSERT INTO attendance
                        (user_id, camera_id, punch_type, confidence, snapshot_path, punch_time, synced)
                    VALUES (?,?,?,?,?,?,1)
                ''', (uid, r.get('camera_id'), r['punch_type'],
                      r.get('confidence', 0), r.get('snapshot_path'), r.get('punch_time')))
            inserted += 1
        except Exception as e:
            log.warning(f"[Sync] Attendance insert error: {e}")

    conn.commit()
    if _pg:
        cur.close()
    conn.close()
    log.info(f"[Sync] Received {inserted}/{len(records)} attendance records from {site_id}.")
    return ok({'inserted': inserted}, f'Received {inserted} records')


@app.route('/api/sync/unknown-faces', methods=['POST'])
def sync_receive_unknown_faces():
    """Receive unknown-face records pushed by a local site."""
    if not _verify_sync_key():
        return jsonify({'status': 'error', 'message': 'Unauthorised'}), 401
    body    = request.get_json() or {}
    site_id = body.get('site_id', 'unknown')
    records = body.get('records', [])
    if not records:
        return ok(msg='No records')

    conn     = get_db()
    _pg      = CLOUD_MODE
    inserted = 0
    for r in records:
        try:
            if _pg:
                cur = conn.cursor()
                cur.execute('''
                    INSERT INTO unknown_faces
                        (camera_id, site_id, snapshot_path, reviewed, notes, detected_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                ''', (r.get('camera_id'), site_id, r.get('snapshot_path'),
                      r.get('reviewed', 0), r.get('notes'), r.get('detected_at')))
            else:
                conn.execute('''
                    INSERT INTO unknown_faces
                        (camera_id, snapshot_path, reviewed, notes, detected_at, synced)
                    VALUES (?,?,?,?,?,1)
                ''', (r.get('camera_id'), r.get('snapshot_path'),
                      r.get('reviewed', 0), r.get('notes'), r.get('detected_at')))
            inserted += 1
        except Exception as e:
            log.warning(f"[Sync] Unknown-face insert error: {e}")

    conn.commit()
    if _pg:
        cur.close()
    conn.close()
    return ok({'inserted': inserted}, f'Received {inserted} unknown-face records')


@app.route('/api/sync/employees', methods=['GET'])
def sync_get_employees():
    """
    Return employees modified since ?since=<datetime> so local sites can
    pull new enrollments made on the cloud portal.
    """
    if not _verify_sync_key():
        return jsonify({'status': 'error', 'message': 'Unauthorised'}), 401
    since = request.args.get('since', '1970-01-01 00:00:00')
    _pg   = CLOUD_MODE

    conn = get_db()
    if _pg:
        cur = conn.cursor()
        cur.execute('''
            SELECT emp_id, name, department, designation, email, phone,
                   face_encoding, active, enrolled_at
            FROM users
            WHERE active=1 AND enrolled_at > %s
            ORDER BY enrolled_at
        ''', (since,))
        rows = cur.fetchall()
        cur.close()
    else:
        rows = conn.execute('''
            SELECT emp_id, name, department, designation, email, phone,
                   face_encoding, active, enrolled_at
            FROM users
            WHERE active=1 AND enrolled_at > ?
            ORDER BY enrolled_at
        ''', (since,)).fetchall()
    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        enc = d.pop('face_encoding', None)
        d['face_encoding_hex'] = enc.hex() if enc else None
        result.append(d)

    return ok(result)


@app.route('/api/sync/status', methods=['GET'])
def sync_status():
    """Return recent sync log entries (admin dashboard use). Requires API key."""
    if not _verify_sync_key():
        return jsonify({'status': 'error', 'message': 'Unauthorised'}), 401
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        'SELECT * FROM sync_log ORDER BY id DESC LIMIT 100'
    ).fetchall()]
    conn.close()
    return ok(rows)


@app.route('/api/sync/local-status', methods=['GET'])
@login_required
def sync_local_status():
    """Return count of unsynced records for the UI status panel (local mode only)."""
    conn = get_db()
    try:
        pa = conn.execute('SELECT COUNT(*) FROM attendance WHERE synced=0').fetchone()[0]
        pu = conn.execute('SELECT COUNT(*) FROM unknown_faces WHERE synced=0').fetchone()[0]
    except Exception:
        pa = pu = 0
    conn.close()
    return ok({'pending_attendance': pa, 'pending_unknown': pu})


@app.route('/api/sync/log', methods=['GET'])
@login_required
def sync_log_local():
    """Return local sync_log entries for the UI."""
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        'SELECT * FROM sync_log ORDER BY id DESC LIMIT 50'
    ).fetchall()]
    conn.close()
    return ok(rows)


@app.route('/api/sync/test-connection', methods=['POST'])
@login_required
def sync_test_connection():
    """Test reachability of the configured cloud portal."""
    url = get_setting('cloud_api_url', '').rstrip('/')
    key = get_setting('cloud_api_key', '')
    if not url or not key:
        return err('Cloud URL and API Key must be configured first')
    try:
        import requests as req
        resp = req.get(
            f"{url}/api/sync/status",
            headers={'X-API-Key': key},
            timeout=10
        )
        if resp.status_code == 200:
            return ok(msg=f'Connected to {url} ✓')
        elif resp.status_code == 401:
            return err('Reached cloud portal but API key was rejected — check the key')
        else:
            return err(f'Cloud portal responded with HTTP {resp.status_code}')
    except Exception as e:
        return err(f'Connection failed: {e}')


# ══════════════════════════════════════════════════════════════════════════
#  STARTUP
# ══════════════════════════════════════════════════════════════════════════

def create_app():
    init_db()
    # Seed default admin user into admin_users table
    conn = get_db()
    if CLOUD_MODE:
        # PostgreSQL
        cur = conn.cursor()
        cur.execute(
            '''INSERT INTO admin_users (username, password_hash, role, full_name)
               VALUES (%s,%s,%s,%s) ON CONFLICT(username) DO NOTHING''',
            ('admin', hash_password('admin123'), 'admin', 'System Administrator')
        )
        conn.commit(); cur.close()
    else:
        conn.execute(
            '''INSERT OR IGNORE INTO admin_users (username, password_hash, role, full_name)
               VALUES (?,?,?,?)''',
            ('admin', hash_password('admin123'), 'admin', 'System Administrator')
        )
        conn.commit()
    conn.close()
    app.before_request(_auth_guard)

    if not CLOUD_MODE:
        # Local mode: start face cache + camera workers
        face_cache.reload()
        camera_manager.start_all_active()
        # Start cloud sync agent if configured
        try:
            if int(get_setting('sync_enabled', '0')):
                from sync_agent import sync_agent
                sync_agent.start()
                log.info("[Sync] Cloud sync agent started.")
        except Exception as e:
            log.warning(f"[Sync] Could not start sync agent: {e}")
    else:
        log.info("[CLOUD] Cloud mode — face engine and cameras disabled.")

    return app


if __name__ == '__main__':
    create_app()

    # ── HTTPS via self-signed certificate ──────────────────────
    try:
        from ssl_gen import ensure_certificates
        cert_file, key_file = ensure_certificates()
        ssl_ctx = (cert_file, key_file)
        port    = 5443
        scheme  = 'https'
    except Exception as e:
        log.warning(f"[SSL] Could not load certificates ({e}). Falling back to HTTP.")
        ssl_ctx = None
        port    = 5000
        scheme  = 'http'

    host = '0.0.0.0'
    log.info(f"[FaceAttend] Starting on {scheme}://localhost:{port}")
    log.info(f"[FaceAttend] Also reachable on your LAN — check your IP address.")
    if scheme == 'https':
        log.info("[FaceAttend] ⚠  First visit: click 'Advanced → Proceed' to trust the self-signed cert.")
        log.info("[FaceAttend]    iOS/macOS: install & trust the cert — see INSTALL_CERT.md")

    # ── Production WSGI server (500+ users) ────────────────────
    # Prefer waitress (cross-platform) or gunicorn (Linux/Mac).
    # Falls back to Flask dev server only if neither is installed.
    import platform
    _is_windows = platform.system() == 'Windows'

    if not _is_windows:
        try:
            import gunicorn  # noqa: F401
            # gunicorn is launched via run.sh; if called directly, warn and fallback
            log.info("[FaceAttend] Tip: for production, launch via run.sh (uses gunicorn).")
        except ImportError:
            pass

    try:
        from waitress import serve
        if ssl_ctx:
            # Waitress has no native SSL support. For HTTPS, fall back to the
            # Flask dev server here; for production HTTPS, launch via run.sh
            # (gunicorn terminates SSL via --certfile/--keyfile).
            log.warning("[FaceAttend] Waitress has no SSL support — using Flask dev server for HTTPS. "
                        "For production, launch via run.sh (gunicorn).")
            app.run(host=host, port=port, debug=False, threaded=True, ssl_context=ssl_ctx)
        else:
            log.info(f"[FaceAttend] Using Waitress WSGI server — production ready for 500+ users.")
            serve(app, host=host, port=port, _quiet=True, threads=16)
    except ImportError:
        # Last resort: Flask dev server (fine for testing, not for production)
        log.warning("[FaceAttend] waitress not found — using Flask dev server. Install waitress for production.")
        app.run(host=host, port=port, debug=False, threaded=True, ssl_context=ssl_ctx)
