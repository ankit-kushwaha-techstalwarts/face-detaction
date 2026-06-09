"""
app/routes/auth.py — Authentication Blueprint
=============================================
Endpoints
---------
GET  /login                  — Serve login HTML page
POST /api/auth/login         — Authenticate and create session
POST /api/auth/logout        — Destroy session
GET  /api/auth/me            — Return current user info
POST /api/auth/change-password — Change own password
GET  /                       — Serve single-page app (requires login)
"""

import logging

from flask import (
    Blueprint, request, session, redirect,
    send_from_directory, current_app,
)

from app.models import ok, err, audit, hash_password, validate_password
from app.models.settings import (
    get_admin_user_by_username,
    update_last_login,
    update_own_password,
    verify_sync_key,
)
from app.middleware import login_required

log = logging.getLogger('faceattend.routes.auth')
auth_bp = Blueprint('auth', __name__)


# ── Login page & SPA ──────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET'])
def login_page():
    if session.get('logged_in'):
        return redirect('/')
    import os
    tmpl = os.path.join(current_app.template_folder, 'login.html')
    return send_from_directory(current_app.template_folder, 'login.html')


@auth_bp.route('/')
@login_required
def index():
    return send_from_directory(current_app.template_folder, 'index.html')


# ── Auth API ──────────────────────────────────────────────────────────────────

@auth_bp.route('/api/auth/login', methods=['POST'])
def do_login():
    data     = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password =  data.get('password') or ''

    if not username or not password:
        return err('Username and password required')

    user_row = get_admin_user_by_username(username)

    if user_row and user_row['password_hash'] == hash_password(password):
        update_last_login(username)
        role      = user_row['role']
        full_name = user_row['full_name'] or username

        session['logged_in'] = True
        session['username']  = username
        session['role']      = role
        session['full_name'] = full_name
        session.permanent    = True

        audit('LOGIN', username, f'Role: {role}')
        log.info("[Auth] Login: %s (%s) from %s", username, role, request.remote_addr)
        return ok({'username': username, 'role': role, 'full_name': full_name}, 'Login successful')

    # Failed login — write audit record without calling audit() to avoid
    # using session.get (session is not yet valid)
    try:
        from app.models import get_db, PH
        with get_db() as conn:
            conn.execute(
                f'INSERT INTO audit_log (username, role, action, target, detail, ip_address) '
                f'VALUES ({PH},{PH},{PH},{PH},{PH},{PH})',
                (username, '', 'LOGIN_FAILED', username, 'Invalid credentials',
                 request.remote_addr),
            )
    except Exception:
        pass

    log.warning("[Auth] Failed login for '%s' from %s", username, request.remote_addr)
    return err('Invalid username or password', 401)


@auth_bp.route('/api/auth/logout', methods=['POST'])
def do_logout():
    audit('LOGOUT', session.get('username', ''))
    session.clear()
    return ok(msg='Logged out')


@auth_bp.route('/api/auth/me', methods=['GET'])
@login_required
def auth_me():
    return ok({
        'username':  session.get('username', ''),
        'role':      session.get('role', ''),
        'full_name': session.get('full_name', ''),
    })


@auth_bp.route('/api/auth/change-password', methods=['POST'])
@login_required
def change_password():
    data     = request.get_json() or {}
    current  = data.get('current_password', '')
    new_pw   = data.get('new_password', '')
    username = session.get('username', '')

    # Verify current password
    user_row = get_admin_user_by_username(username)
    if not user_row or user_row['password_hash'] != hash_password(current):
        return err('Current password is incorrect', 401)

    pw_err = validate_password(new_pw)
    if pw_err:
        return err(pw_err)

    update_own_password(username, hash_password(new_pw))
    audit('CHANGE_PASSWORD', username)
    return ok(msg='Password changed successfully')
