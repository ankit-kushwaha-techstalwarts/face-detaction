"""
app/models/cameras.py — Camera Configuration DAO
=================================================
Manages camera registration, configuration, and look-ups.
The actual RTSP streaming (CameraWorker threads) is handled by
face_engine.py and exposed via current_app.camera_manager in routes.

All queries are 100 % parameterised.
"""

import logging
from typing import Optional

from app.models import get_db, PH

log = logging.getLogger('faceattend.models.cameras')


def list_all() -> list:
    """Return all cameras ordered by ID."""
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM cameras ORDER BY id').fetchall()
    return [dict(r) for r in rows]


def get_camera(cid: int) -> Optional[dict]:
    """Fetch a single camera record by primary key."""
    with get_db() as conn:
        row = conn.execute(
            f'SELECT * FROM cameras WHERE id={PH}', (cid,)
        ).fetchone()
    return dict(row) if row else None


def get_rtsp(cid: int) -> Optional[dict]:
    """Return (rtsp_url, direction) for a camera, or None if not found."""
    with get_db() as conn:
        row = conn.execute(
            f'SELECT rtsp_url, direction FROM cameras WHERE id={PH}', (cid,)
        ).fetchone()
    return dict(row) if row else None


def add_camera(data: dict) -> int:
    """
    Insert a new camera. Returns the new row ID.
    Required keys in data: name, rtsp_url.
    Optional: location, direction.
    """
    with get_db() as conn:
        conn.execute(
            f'INSERT INTO cameras (name, rtsp_url, location, direction) '
            f'VALUES ({PH},{PH},{PH},{PH})',
            (
                data['name'],
                data['rtsp_url'],
                data.get('location', ''),
                data.get('direction', 'BOTH'),
            ),
        )
        cid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    return cid


def update_camera(cid: int, data: dict) -> bool:
    """
    Partial update for a camera row.
    Allowed fields: name, rtsp_url, location, direction, active.
    Returns True if update ran, False if nothing to update.
    """
    allowed = ['name', 'rtsp_url', 'location', 'direction', 'active']
    sets    = [f'{f}={PH}' for f in allowed if f in data]
    vals    = [data[f]     for f in allowed if f in data]
    if not sets:
        return False
    with get_db() as conn:
        conn.execute(
            f'UPDATE cameras SET {",".join(sets)} WHERE id={PH}',
            vals + [cid],
        )
    return True


def delete_camera(cid: int) -> None:
    """Hard-delete a camera record."""
    with get_db() as conn:
        conn.execute(f'DELETE FROM cameras WHERE id={PH}', (cid,))
