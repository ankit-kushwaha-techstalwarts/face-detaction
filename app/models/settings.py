"""
app/models/settings.py — Settings, Departments, Holidays, Admin Users & Audit Log DAO
======================================================================================
All global system configuration, compliance thresholds, access management,
and audit queries live here.

All SQL is 100 % parameterised.
"""

import logging
from typing import Optional

from app.models import get_db, get_setting, set_setting, PH, hash_password

log = logging.getLogger('faceattend.models.settings')

VALID_ROLES = frozenset({'admin', 'manager', 'guard'})


# ── Application Settings ──────────────────────────────────────────────────────

def get_all_settings() -> dict:
    """
    Return all settings rows and departments for the settings panel.
    Public endpoint (GET /api/settings) uses this so the login page
    can fetch branding before authentication.
    """
    with get_db() as conn:
        settings = conn.execute(
            'SELECT key, value, label, category FROM settings ORDER BY category, key'
        ).fetchall()
        depts = conn.execute(
            'SELECT id, name FROM departments ORDER BY name'
        ).fetchall()
    return {
        'settings':    [dict(r) for r in settings],
        'departments': [dict(r) for r in depts],
    }


def save_settings(data: dict) -> None:
    """
    Upsert a dict of {key: value} pairs into the settings table.
    Only updates value; label and category are preserved if the key exists.
    """
    with get_db() as conn:
        for key, value in data.items():
            # Sanitise key — allow only alphanumeric and underscore to prevent injection
            if not str(key).replace('_', '').isalnum():
                log.warning("[Settings] Skipping invalid key: %s", key)
                continue
            conn.execute(
                f'INSERT OR REPLACE INTO settings (key, value) VALUES ({PH},{PH}) '
                f'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (key, str(value)),
            )


def save_logo(rel_path: str) -> None:
    set_setting('org_logo', rel_path)


def save_favicon(rel_path: str) -> None:
    set_setting('favicon_path', rel_path)


def save_theme(data: dict) -> None:
    allowed = {'primary_color', 'accent_color', 'org_name', 'org_title'}
    with get_db() as conn:
        for k, v in data.items():
            if k in allowed:
                conn.execute(
                    f'INSERT OR REPLACE INTO settings (key, value) VALUES ({PH},{PH})',
                    (k, str(v)),
                )


# ── Departments ───────────────────────────────────────────────────────────────

def list_departments() -> list:
    with get_db() as conn:
        rows = conn.execute('SELECT name FROM departments ORDER BY name').fetchall()
    return [r['name'] for r in rows]


def add_department(name: str) -> None:
    with get_db() as conn:
        conn.execute(
            f'INSERT OR IGNORE INTO departments (name) VALUES ({PH})', (name,)
        )


# ── Holidays ──────────────────────────────────────────────────────────────────

def list_holidays(year: str) -> list:
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, date, name, type FROM holidays "
            f"WHERE strftime('%Y', date)={PH} ORDER BY date",
            (year,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_holiday(date: str, name: str, htype: str) -> None:
    """Raises Exception if a holiday for this date already exists."""
    with get_db() as conn:
        conn.execute(
            f'INSERT INTO holidays (date, name, type) VALUES ({PH},{PH},{PH})',
            (date, name, htype),
        )


def update_holiday(hid: int, data: dict) -> None:
    with get_db() as conn:
        conn.execute(
            f'UPDATE holidays SET date={PH}, name={PH}, type={PH} WHERE id={PH}',
            (data.get('date'), data.get('name'), data.get('type', 'national'), hid),
        )


def delete_holiday(hid: int) -> None:
    with get_db() as conn:
        conn.execute(f'DELETE FROM holidays WHERE id={PH}', (hid,))


# ── Admin (System) Users ──────────────────────────────────────────────────────

def list_admin_users() -> list:
    with get_db() as conn:
        rows = conn.execute(
            'SELECT id, username, role, full_name, department, active, last_login, created_at '
            'FROM admin_users ORDER BY role, username'
        ).fetchall()
    return [dict(r) for r in rows]


def get_admin_user_by_username(username: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            f'SELECT password_hash, role, full_name FROM admin_users '
            f'WHERE username={PH} AND active=1',
            (username,),
        ).fetchone()
    return dict(row) if row else None


def update_last_login(username: str) -> None:
    with get_db() as conn:
        conn.execute(
            f'UPDATE admin_users SET last_login=datetime("now","localtime") WHERE username={PH}',
            (username,),
        )


def create_admin_user(
    username:  str,
    pw_hash:   str,
    role:      str,
    full_name: str = '',
    dept:      str = '',
) -> int:
    """Create a new system user. Returns new ID. Raises on duplicate username."""
    if role not in VALID_ROLES:
        raise ValueError(f'Invalid role: {role}')
    with get_db() as conn:
        conn.execute(
            f'INSERT INTO admin_users (username, password_hash, role, full_name, department) '
            f'VALUES ({PH},{PH},{PH},{PH},{PH})',
            (username, pw_hash, role, full_name, dept),
        )
        uid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    return uid


def update_admin_user(uid: int, data: dict) -> bool:
    """
    Partial update of a system user record.
    If 'password' is in data it is expected to be already hashed
    (routes must hash before calling this).
    """
    allowed = ['full_name', 'role', 'department', 'active']
    sets    = [f'{f}={PH}' for f in allowed if f in data]
    vals    = [data[f]     for f in allowed if f in data]

    if 'password_hash' in data:
        sets.append(f'password_hash={PH}')
        vals.append(data['password_hash'])

    if not sets:
        return False

    with get_db() as conn:
        conn.execute(
            f'UPDATE admin_users SET {",".join(sets)} WHERE id={PH}',
            vals + [uid],
        )
    return True


def update_own_password(username: str, new_hash: str) -> None:
    with get_db() as conn:
        conn.execute(
            f'UPDATE admin_users SET password_hash={PH} WHERE username={PH}',
            (new_hash, username),
        )


def delete_admin_user(uid: int) -> None:
    with get_db() as conn:
        conn.execute(f'DELETE FROM admin_users WHERE id={PH}', (uid,))


def seed_default_admin() -> None:
    """
    Ensure at least one admin account exists.
    Uses INSERT OR IGNORE / ON CONFLICT so re-running is safe.
    Called once at startup from app/__init__.py.
    """
    from app.models import CLOUD_MODE
    default_hash = hash_password('admin123')

    with get_db() as conn:
        if CLOUD_MODE:
            conn.execute(
                f'INSERT INTO admin_users (username, password_hash, role, full_name) '
                f'VALUES ({PH},{PH},{PH},{PH}) ON CONFLICT(username) DO NOTHING',
                ('admin', default_hash, 'admin', 'System Administrator'),
            )
        else:
            conn.execute(
                'INSERT OR IGNORE INTO admin_users '
                f'(username, password_hash, role, full_name) VALUES ({PH},{PH},{PH},{PH})',
                ('admin', default_hash, 'admin', 'System Administrator'),
            )


# ── Audit Log ─────────────────────────────────────────────────────────────────

def get_audit_log(
    date_from: str,
    date_to:   str,
    username:  str = '',
    page:      int = 1,
    per_page:  int = 50,
) -> dict:
    sql  = f'SELECT * FROM audit_log WHERE DATE(created_at) BETWEEN {PH} AND {PH}'
    args: list = [date_from, date_to]
    if username:
        sql += f' AND username={PH}'; args.append(username)
    sql += ' ORDER BY created_at DESC'

    with get_db() as conn:
        total = conn.execute(f'SELECT COUNT(*) FROM ({sql})', args).fetchone()[0]
        rows  = conn.execute(
            sql + f' LIMIT {per_page} OFFSET {(page - 1) * per_page}', args
        ).fetchall()
        users = conn.execute(
            'SELECT DISTINCT username FROM audit_log ORDER BY username'
        ).fetchall()

    return {
        'records': [dict(r) for r in rows],
        'total':   total,
        'pages':   max(1, (total + per_page - 1) // per_page),
        'page':    page,
        'users':   [r['username'] for r in users],
    }


# ── Cloud Sync Settings ───────────────────────────────────────────────────────

def get_sync_log(limit: int = 50) -> list:
    with get_db() as conn:
        rows = conn.execute(
            f'SELECT * FROM sync_log ORDER BY id DESC LIMIT {limit}'
        ).fetchall()
    return [dict(r) for r in rows]


def get_full_sync_log(limit: int = 100) -> list:
    with get_db() as conn:
        rows = conn.execute(
            f'SELECT * FROM sync_log ORDER BY id DESC LIMIT {limit}'
        ).fetchall()
    return [dict(r) for r in rows]


def verify_sync_key(provided_key: str) -> bool:
    """Return True if the provided API key matches the configured sync_api_key."""
    expected = get_setting('sync_api_key', '')
    if not expected:
        return False   # key not configured — reject everything
    # Constant-time comparison to prevent timing attacks
    import hmac
    return hmac.compare_digest(expected, provided_key)
