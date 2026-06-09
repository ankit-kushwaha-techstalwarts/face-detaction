"""
app/models/__init__.py — Data Access Layer Foundation
======================================================
Exports
-------
- get_db()          context-manager: yields an open DB connection, auto-commits,
                    auto-closes, rolls back on exception.
- init_db()         initialise schema (delegates to the right DB module).
- get_setting()     read a setting value.
- set_setting()     write a setting value.
- CLOUD_MODE        bool flag, used by models to build PG vs SQLite SQL.
- audit()           write an immutable record to audit_log.
- ok() / err()      uniform JSON response helpers (imported by routes).

Security guarantee
------------------
Every query that flows through these models uses parameterised placeholders
(?  for SQLite,  %s  for PostgreSQL) — never string interpolation.
SQL injection is therefore structurally impossible at the DAO layer.
"""

import logging
import os
from contextlib import contextmanager
from datetime import datetime
from functools import wraps

from flask import jsonify, request, session

log = logging.getLogger('faceattend.models')

# ── Select DB adapter based on environment ────────────────────────────────────
_DATABASE_URL = os.environ.get('DATABASE_URL', '')
CLOUD_MODE    = bool(os.environ.get('CLOUD_MODE', ''))

if _DATABASE_URL and CLOUD_MODE:
    from database_pg import (
        get_db as _raw_get_db,
        init_db,
        get_setting,
        set_setting,
    )
    # PostgreSQL placeholder character
    PH = '%s'
else:
    from database import (
        get_db as _raw_get_db,
        init_db,
        get_setting,
        set_setting,
    )
    PH = '?'


# ── Connection context manager ────────────────────────────────────────────────

@contextmanager
def get_db():
    """
    Yield an open database connection.

    Usage::

        with get_db() as conn:
            row = conn.execute('SELECT …').fetchone()
            conn.execute('INSERT …', (…,))
        # auto-committed and closed here

    On exception the transaction is rolled back and the exception re-raised.
    """
    conn = _raw_get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Audit logging ─────────────────────────────────────────────────────────────

def audit(action: str, target: str = '', detail: str = '') -> None:
    """
    Write an immutable record to audit_log.

    Safe to call from anywhere — catches and logs its own exceptions so it
    never cascades into the main request flow.

    Captures: username, role, action, target, detail, ip_address, timestamp.
    """
    try:
        username = session.get('username', 'system')
        role     = session.get('role', '')
        ip       = request.remote_addr

        with get_db() as conn:
            conn.execute(
                f'INSERT INTO audit_log '
                f'(username, role, action, target, detail, ip_address) '
                f'VALUES ({PH},{PH},{PH},{PH},{PH},{PH})',
                (username, role, action, target, detail, ip),
            )
    except Exception as exc:
        log.error("[Audit] Failed to write audit record: %s", exc)


# ── Uniform HTTP response helpers ─────────────────────────────────────────────

def ok(data=None, msg: str = 'success', **extra):
    """Return a 200 JSON envelope: {'status':'ok', 'message':…, 'data':…}."""
    return jsonify({'status': 'ok', 'message': msg, 'data': data, **extra})


def err(msg: str, code: int = 400):
    """Return an error JSON envelope: {'status':'error', 'message':…}."""
    return jsonify({'status': 'error', 'message': msg}), code


# ── Password utilities ────────────────────────────────────────────────────────

import hashlib


def hash_password(pw: str) -> str:
    """SHA-256 hash of a password string."""
    return hashlib.sha256(pw.encode()).hexdigest()


def validate_password(pw: str):
    """
    Enforce the password policy configured in settings.
    Returns an error string or None if the password is acceptable.
    """
    try:
        min_len = int(get_setting('min_pw_length', '8'))
    except (TypeError, ValueError):
        min_len = 8
    require_strong = get_setting('require_strong_pw', '1') == '1'

    if len(pw) < min_len:
        return f'Password must be at least {min_len} characters'
    if require_strong:
        if not any(c.isupper() for c in pw):
            return 'Password must contain at least one uppercase letter'
        if not any(c.isdigit() for c in pw):
            return 'Password must contain at least one digit'
    return None


# ── File upload validation ────────────────────────────────────────────────────

_ALLOWED_EXTENSIONS = frozenset({'png', 'jpg', 'jpeg', 'webp', 'ico'})


def allowed_image(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in _ALLOWED_EXTENSIONS


# ── Working-day helpers ───────────────────────────────────────────────────────
# Used by attendance reports; kept here so both models and routes can import.

from datetime import timedelta


def get_holidays_set(date_from: str, date_to: str) -> set:
    """Return a set of holiday date strings in YYYY-MM-DD format."""
    with get_db() as conn:
        rows = conn.execute(
            f'SELECT date FROM holidays WHERE date BETWEEN {PH} AND {PH}',
            (date_from, date_to),
        ).fetchall()
    return {r['date'] for r in rows}


def working_days_in_range(date_from: str, date_to: str) -> list:
    """
    Return a list of working date strings (YYYY-MM-DD) in the given range,
    excluding weekends and public holidays from the holidays table.
    """
    work_day_nums = {
        int(d)
        for d in get_setting('work_days', '1,2,3,4,5').split(',')
        if d.strip().isdigit()
    }
    holidays = get_holidays_set(date_from, date_to)
    start = datetime.strptime(date_from, '%Y-%m-%d')
    end   = datetime.strptime(date_to,   '%Y-%m-%d')
    days, cur = [], start
    while cur <= end:
        ds = cur.strftime('%Y-%m-%d')
        if cur.isoweekday() in work_day_nums and ds not in holidays:
            days.append(ds)
        cur += timedelta(days=1)
    return days
