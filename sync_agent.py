"""
Face Recognition Attendance System — Cloud Sync Agent
======================================================
Runs as a daemon thread alongside the local Flask app.

Responsibilities
----------------
PUSH (local → cloud):
  • attendance rows   (unsynced=0) → POST /api/sync/attendance
  • unknown_faces rows (unsynced=0) → POST /api/sync/unknown-faces

PULL (cloud → local):
  • employees enrolled on cloud portal → GET /api/sync/employees?since=<ts>
    Upserts into local users table + rebuilds face cache.

All operations are idempotent and safe to retry.
If the internet is down the cycle is silently skipped and retried
after sync_interval seconds.

Activation
----------
Call `sync_agent.start()` from app.py at startup when sync_enabled=1.
The thread is daemon=True so it dies with the main process.
"""

import threading
import time
import logging
import json
from datetime import datetime

log = logging.getLogger(__name__)

try:
    import requests as _requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False
    log.warning("[Sync] 'requests' library not installed — run: pip install requests")


# ─────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────

def _write_sync_log(conn, direction: str, entity: str, records: int,
                    status: str = 'ok', detail: str = ''):
    try:
        conn.execute(
            'INSERT INTO sync_log (direction, entity, records, status, detail) VALUES (?,?,?,?,?)',
            (direction, entity, records, status, detail)
        )
        conn.commit()
    except Exception:
        pass  # never let logging break the sync cycle


def _placeholders(n: int) -> str:
    return ','.join('?' * n)


# ─────────────────────────────────────────────────────────────
#  Sync Agent
# ─────────────────────────────────────────────────────────────

class SyncAgent(threading.Thread):
    """Background thread that keeps local SQLite in sync with the cloud portal."""

    BATCH_SIZE_ATTENDANCE = 500
    BATCH_SIZE_UNKNOWN    = 200
    TIMEOUT_SEC           = 20

    def __init__(self):
        super().__init__(daemon=True, name='SyncAgent')
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        log.info("[Sync] Agent started.")
        while not self._stop_event.is_set():
            try:
                from database import get_db, get_setting, set_setting
                enabled = int(get_setting('sync_enabled', '0'))
                if enabled:
                    self._cycle(get_db, get_setting, set_setting)
                interval = int(get_setting('sync_interval', '30'))
            except Exception as e:
                log.error(f"[Sync] Unexpected error in cycle: {e}")
                interval = 30

            self._stop_event.wait(interval)

        log.info("[Sync] Agent stopped.")

    # ── Main cycle ────────────────────────────────────────────

    def _cycle(self, get_db, get_setting, set_setting):
        if not _REQUESTS_OK:
            return

        base_url = (get_setting('cloud_api_url') or '').rstrip('/')
        api_key  = get_setting('cloud_api_key', '')
        site_id  = get_setting('site_id', 'site1')

        if not base_url or not api_key:
            return  # not configured yet

        headers = {
            'X-API-Key':    api_key,
            'X-Site-ID':    site_id,
            'Content-Type': 'application/json',
        }

        conn = get_db()
        try:
            self._push_attendance(conn, base_url, headers, site_id)
            self._push_unknown_faces(conn, base_url, headers, site_id)
            self._pull_employees(conn, base_url, headers, get_setting, set_setting)
        finally:
            conn.close()

    # ── PUSH: attendance ──────────────────────────────────────

    def _push_attendance(self, conn, base_url, headers, site_id):
        rows = conn.execute('''
            SELECT a.id, a.user_id, a.camera_id, a.punch_type,
                   a.confidence, a.punch_time, a.created_at,
                   u.emp_id, u.name AS employee_name, u.department
            FROM   attendance a
            JOIN   users u ON u.id = a.user_id
            WHERE  a.synced = 0
            ORDER  BY a.id
            LIMIT  ?
        ''', (self.BATCH_SIZE_ATTENDANCE,)).fetchall()

        if not rows:
            return

        payload = [dict(r) for r in rows]
        try:
            resp = _requests.post(
                f"{base_url}/api/sync/attendance",
                json={'site_id': site_id, 'records': payload},
                headers=headers,
                timeout=self.TIMEOUT_SEC,
            )
            resp.raise_for_status()
            ids = [r['id'] for r in payload]
            conn.execute(
                f'UPDATE attendance SET synced=1 WHERE id IN ({_placeholders(len(ids))})',
                ids
            )
            conn.commit()
            _write_sync_log(conn, 'PUSH', 'attendance', len(ids))
            log.info(f"[Sync] Pushed {len(ids)} attendance records.")
        except Exception as e:
            _write_sync_log(conn, 'PUSH', 'attendance', 0, 'error', str(e))
            log.warning(f"[Sync] Attendance push failed: {e}")

    # ── PUSH: unknown faces ───────────────────────────────────

    def _push_unknown_faces(self, conn, base_url, headers, site_id):
        rows = conn.execute('''
            SELECT id, camera_id, snapshot_path, reviewed, notes, detected_at
            FROM   unknown_faces
            WHERE  synced = 0
            ORDER  BY id
            LIMIT  ?
        ''', (self.BATCH_SIZE_UNKNOWN,)).fetchall()

        if not rows:
            return

        payload = [dict(r) for r in rows]
        try:
            resp = _requests.post(
                f"{base_url}/api/sync/unknown-faces",
                json={'site_id': site_id, 'records': payload},
                headers=headers,
                timeout=self.TIMEOUT_SEC,
            )
            resp.raise_for_status()
            ids = [r['id'] for r in payload]
            conn.execute(
                f'UPDATE unknown_faces SET synced=1 WHERE id IN ({_placeholders(len(ids))})',
                ids
            )
            conn.commit()
            _write_sync_log(conn, 'PUSH', 'unknown_faces', len(ids))
            log.info(f"[Sync] Pushed {len(ids)} unknown face records.")
        except Exception as e:
            _write_sync_log(conn, 'PUSH', 'unknown_faces', 0, 'error', str(e))
            log.warning(f"[Sync] Unknown-faces push failed: {e}")

    # ── PULL: employees ───────────────────────────────────────

    def _pull_employees(self, conn, base_url, headers, get_setting, set_setting):
        last_sync = get_setting('last_employee_sync', '1970-01-01 00:00:00')
        try:
            resp = _requests.get(
                f"{base_url}/api/sync/employees",
                params={'since': last_sync},
                headers=headers,
                timeout=self.TIMEOUT_SEC,
            )
            resp.raise_for_status()
            data = resp.json().get('data', [])
        except Exception as e:
            log.warning(f"[Sync] Employee pull failed: {e}")
            return

        if not data:
            return

        for emp in data:
            enc_hex = emp.get('face_encoding_hex')
            enc_bytes = bytes.fromhex(enc_hex) if enc_hex else None
            conn.execute('''
                INSERT INTO users
                    (emp_id, name, department, designation, email, phone, face_encoding, active)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(emp_id) DO UPDATE SET
                    name        = excluded.name,
                    department  = excluded.department,
                    designation = excluded.designation,
                    email       = excluded.email,
                    phone       = excluded.phone,
                    face_encoding = excluded.face_encoding,
                    active      = excluded.active
            ''', (
                emp['emp_id'], emp['name'],
                emp.get('department', ''), emp.get('designation', ''),
                emp.get('email', ''),      emp.get('phone', ''),
                enc_bytes, emp.get('active', 1),
            ))

        conn.commit()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        set_setting('last_employee_sync', now_str)
        _write_sync_log(conn, 'PULL', 'employees', len(data))
        log.info(f"[Sync] Pulled {len(data)} employees from cloud.")

        # Rebuild face recognition cache so new enrollees are recognised immediately
        try:
            from face_engine import face_cache
            face_cache.reload()
            log.info("[Sync] Face cache reloaded after employee pull.")
        except Exception as e:
            log.warning(f"[Sync] Face cache reload failed: {e}")


# ── Singleton ──────────────────────────────────────────────────
sync_agent = SyncAgent()
