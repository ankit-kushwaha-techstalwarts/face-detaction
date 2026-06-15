"""
app/models/users.py — User & Face-Encoding DAO
===============================================
All SQL is 100 % parameterised.
No Flask imports — this layer is framework-agnostic.
Routes call these functions; no raw SQL exists in route handlers.
"""

import csv
import io
import logging
from typing import Optional

from app.models import get_db, PH, CLOUD_MODE

log = logging.getLogger('faceattend.models.users')


# ── Read ──────────────────────────────────────────────────────────────────────

def list_users(
    dept:     Optional[str] = None,
    search:   str           = '',
    active:   str           = '1',
    page:     int           = 1,
    per_page: int           = 50,
) -> dict:
    """
    Paginated employee list with optional filters.

    Returns
    -------
    dict with keys: users, total, page, per_page, pages
    """
    if CLOUD_MODE:
        # face_templates doesn't exist in the cloud (PostgreSQL) schema.
        sql = (
            'SELECT id, emp_id, name, department, designation, '
            '       email, phone, photo_path, active, enrolled_at, '
            '       0 AS template_count '
            'FROM users WHERE 1=1'
        )
    else:
        sql = (
            'SELECT u.id, u.emp_id, u.name, u.department, u.designation, '
            '       u.email, u.phone, u.photo_path, u.active, u.enrolled_at, '
            '       COALESCE(ft.cnt, 0) AS template_count '
            'FROM users u '
            'LEFT JOIN (SELECT user_id, COUNT(*) AS cnt FROM face_templates GROUP BY user_id) ft '
            '       ON ft.user_id = u.id '
            'WHERE 1=1'
        )
    args: list = []

    if active != 'all':
        sql += f' AND active={PH}'
        args.append(int(active))
    if dept:
        sql += f' AND department={PH}'
        args.append(dept)
    if search:
        sql += f' AND (name LIKE {PH} OR emp_id LIKE {PH} OR designation LIKE {PH})'
        pattern = f'%{search}%'
        args += [pattern, pattern, pattern]

    sql += ' ORDER BY name'

    with get_db() as conn:
        total = conn.execute(f'SELECT COUNT(*) FROM ({sql})', args).fetchone()[0]
        paged = sql + f' LIMIT {per_page} OFFSET {(page - 1) * per_page}'
        rows  = conn.execute(paged, args).fetchall()

    return {
        'users':    [dict(r) for r in rows],
        'total':    total,
        'page':     page,
        'per_page': per_page,
        'pages':    max(1, (total + per_page - 1) // per_page),
    }


def get_user(uid: int) -> Optional[dict]:
    """Fetch a single user by primary key. Returns None if not found."""
    with get_db() as conn:
        row = conn.execute(
            'SELECT id, emp_id, name, department, designation, '
            '       email, phone, photo_path, active, enrolled_at '
            f'FROM users WHERE id={PH}',
            (uid,),
        ).fetchone()
    return dict(row) if row else None


def get_user_with_encoding(uid: int) -> Optional[dict]:
    """Fetch a user's id, name and raw face_encoding blob (or None)."""
    with get_db() as conn:
        row = conn.execute(
            f'SELECT id, name, face_encoding FROM users WHERE id={PH}', (uid,)
        ).fetchone()
    return dict(row) if row else None


def update_user_face_encoding(uid: int, blob: bytes) -> None:
    """Replace a user's face encoding (e.g. after blending in a new sample)."""
    with get_db() as conn:
        conn.execute(
            f'UPDATE users SET face_encoding={PH}, '
            f'enrolled_at=datetime("now","localtime") WHERE id={PH}',
            (blob, uid),
        )


def get_user_photo_path(uid: int) -> Optional[str]:
    """Return the photo_path for a user or None."""
    with get_db() as conn:
        row = conn.execute(
            f'SELECT photo_path FROM users WHERE id={PH}', (uid,)
        ).fetchone()
    return row['photo_path'] if row else None


def get_active_users(dept: Optional[str] = None) -> list:
    """Return all active users (used in absentee report)."""
    sql  = 'SELECT id, emp_id, name, department, designation FROM users WHERE active=1'
    args: list = []
    if dept:
        sql += f' AND department={PH}'
        args.append(dept)
    with get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


# ── Write ─────────────────────────────────────────────────────────────────────

def create_user(data: dict) -> int:
    """
    Insert a new user. Returns the new row ID.
    Raises Exception on duplicate emp_id or other DB error.
    """
    with get_db() as conn:
        conn.execute(
            f'INSERT INTO users (emp_id, name, department, designation, email, phone) '
            f'VALUES ({PH},{PH},{PH},{PH},{PH},{PH})',
            (
                data['emp_id'],
                data['name'],
                data.get('department'),
                data.get('designation'),
                data.get('email'),
                data.get('phone'),
            ),
        )
        uid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    return uid


def update_user(uid: int, data: dict) -> bool:
    """
    Partial update: only fields present in `data` are changed.
    Allowed fields: name, department, designation, email, phone, active.
    Returns True on success, False if nothing to update.
    """
    allowed = ['name', 'department', 'designation', 'email', 'phone', 'active']
    sets    = [f'{f}={PH}' for f in allowed if f in data]
    vals    = [data[f]     for f in allowed if f in data]
    if not sets:
        return False
    with get_db() as conn:
        conn.execute(
            f'UPDATE users SET {",".join(sets)} WHERE id={PH}',
            vals + [uid],
        )
    return True


def deactivate_user(uid: int) -> None:
    """Soft-delete: set active=0."""
    with get_db() as conn:
        conn.execute(f'UPDATE users SET active=0 WHERE id={PH}', (uid,))


def update_user_photo(uid: int, rel_path: str) -> None:
    """Update photo_path for a user."""
    with get_db() as conn:
        conn.execute(
            f'UPDATE users SET photo_path={PH} WHERE id={PH}',
            (rel_path, uid),
        )


def update_user_encoding(uid: int, blob: bytes, rel_path: str) -> None:
    """Store a new face encoding blob and update photo_path + enrolled_at."""
    with get_db() as conn:
        conn.execute(
            f'UPDATE users SET face_encoding={PH}, photo_path={PH}, '
            f'enrolled_at=datetime("now","localtime") WHERE id={PH}',
            (blob, rel_path, uid),
        )


# ── Bulk CSV import ───────────────────────────────────────────────────────────

def import_users_from_csv(file_bytes: bytes) -> dict:
    """
    Parse a CSV byte string and bulk-insert users.
    Expected columns: emp_id, name, department, designation, email, phone.
    Duplicate emp_ids are silently skipped (INSERT OR IGNORE).

    Returns
    -------
    dict with keys: inserted, skipped, errors
    """
    content = file_bytes.decode('utf-8-sig')
    reader  = csv.DictReader(io.StringIO(content))

    inserted, skipped, errors = 0, 0, []

    with get_db() as conn:
        for i, row in enumerate(reader, 1):
            emp_id = row.get('emp_id', '').strip()
            name   = row.get('name',   '').strip()
            if not emp_id or not name:
                errors.append(f'Row {i}: emp_id and name are required')
                skipped += 1
                continue
            try:
                conn.execute(
                    f'INSERT OR IGNORE INTO users '
                    f'(emp_id, name, department, designation, email, phone) '
                    f'VALUES ({PH},{PH},{PH},{PH},{PH},{PH})',
                    (
                        emp_id, name,
                        row.get('department', ''),
                        row.get('designation', ''),
                        row.get('email', ''),
                        row.get('phone', ''),
                    ),
                )
                inserted += 1
            except Exception as exc:
                errors.append(f'Row {i}: {exc}')
                skipped += 1

    return {'inserted': inserted, 'skipped': skipped, 'errors': errors}
