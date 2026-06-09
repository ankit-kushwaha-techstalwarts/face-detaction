"""
app/models/attendance.py — Attendance, Unknown-Faces & Reports DAO
==================================================================
Handles all SQL for:
  - attendance log (read/write/delete)
  - today's summary
  - unknown-face records
  - all report queries (attendance, monthly, late arrivals, absentees,
    working hours, employee history)
  - dashboard aggregate queries

All queries are 100 % parameterised. No business logic lives here
(business logic = the route handler decides what to do with the data).
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from app.models import get_db, get_setting, PH, working_days_in_range, get_holidays_set

log = logging.getLogger('faceattend.models.attendance')


# ── Attendance read ───────────────────────────────────────────────────────────

def list_attendance(
    date_from: str,
    date_to:   str,
    user_id:   Optional[str] = None,
    dept:      Optional[str] = None,
    punch_type: Optional[str] = None,
    page:      int = 1,
    per_page:  int = 50,
) -> dict:
    """Paginated attendance log with optional filters."""
    sql = (
        'SELECT a.id, a.punch_type, a.confidence, a.snapshot_path, a.punch_time, '
        '       u.emp_id, u.name, u.department, u.designation, '
        '       c.name AS camera_name, c.location '
        'FROM attendance a '
        'JOIN users u ON u.id = a.user_id '
        f'LEFT JOIN cameras c ON c.id = a.camera_id '
        f'WHERE DATE(a.punch_time) BETWEEN {PH} AND {PH}'
    )
    args: list = [date_from, date_to]

    if user_id:
        sql += f' AND a.user_id={PH}'; args.append(user_id)
    if dept:
        sql += f' AND u.department={PH}'; args.append(dept)
    if punch_type:
        sql += f' AND a.punch_type={PH}'; args.append(punch_type)

    sql += ' ORDER BY a.punch_time DESC'

    with get_db() as conn:
        total = conn.execute(f'SELECT COUNT(*) FROM ({sql})', args).fetchone()[0]
        rows  = conn.execute(
            sql + f' LIMIT {per_page} OFFSET {(page - 1) * per_page}', args
        ).fetchall()

    return {
        'records':  [dict(r) for r in rows],
        'total':    total,
        'page':     page,
        'per_page': per_page,
        'pages':    max(1, (total + per_page - 1) // per_page),
    }


def today_summary(dept: Optional[str] = None) -> list:
    """
    Per-employee today summary: first IN, last OUT, punch counts.
    Includes all active employees (absent rows have NULL punch times).
    """
    today = datetime.now().strftime('%Y-%m-%d')
    sql = (
        'SELECT u.id, u.emp_id, u.name, u.department, u.designation, '
        f"       MIN(CASE WHEN a.punch_type='IN'  THEN a.punch_time END) AS first_in, "
        f"       MAX(CASE WHEN a.punch_type='OUT' THEN a.punch_time END) AS last_out, "
        f"       COUNT(CASE WHEN a.punch_type='IN'  THEN 1 END) AS in_count, "
        f"       COUNT(CASE WHEN a.punch_type='OUT' THEN 1 END) AS out_count "
        f'FROM users u '
        f'LEFT JOIN attendance a ON a.user_id=u.id AND DATE(a.punch_time)={PH} '
        f'WHERE u.active=1'
    )
    args: list = [today]
    if dept:
        sql += f' AND u.department={PH}'; args.append(dept)
    sql += ' GROUP BY u.id ORDER BY u.name'

    with get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


# ── Attendance write ──────────────────────────────────────────────────────────

def manual_punch(user_id: int, punch_type: str) -> None:
    """Insert a manual attendance record with 100 % confidence."""
    with get_db() as conn:
        conn.execute(
            f'INSERT INTO attendance (user_id, punch_type, confidence) '
            f'VALUES ({PH},{PH},{PH})',
            (user_id, punch_type, 100.0),
        )


def delete_record(aid: int) -> None:
    with get_db() as conn:
        conn.execute(f'DELETE FROM attendance WHERE id={PH}', (aid,))


# ── Unknown faces ─────────────────────────────────────────────────────────────

def list_unknown_faces(
    page:     int           = 1,
    per_page: int           = 24,
    reviewed: Optional[str] = None,
) -> dict:
    sql  = (
        'SELECT uf.id, uf.snapshot_path, uf.detected_at, uf.reviewed, uf.notes, '
        '       c.name AS camera_name, c.location '
        'FROM unknown_faces uf '
        'LEFT JOIN cameras c ON c.id = uf.camera_id '
        'WHERE 1=1'
    )
    args: list = []
    if reviewed is not None:
        sql += f' AND uf.reviewed={PH}'; args.append(int(reviewed))
    sql += ' ORDER BY uf.detected_at DESC'

    with get_db() as conn:
        total = conn.execute(f'SELECT COUNT(*) FROM ({sql})', args).fetchone()[0]
        rows  = conn.execute(
            sql + f' LIMIT {per_page} OFFSET {(page - 1) * per_page}', args
        ).fetchall()

    return {'records': [dict(r) for r in rows], 'total': total}


def review_unknown(fid: int, notes: str) -> None:
    with get_db() as conn:
        conn.execute(
            f'UPDATE unknown_faces SET reviewed=1, notes={PH} WHERE id={PH}',
            (notes, fid),
        )


def delete_unknown(fid: int, base_dir: str) -> None:
    with get_db() as conn:
        row = conn.execute(
            f'SELECT snapshot_path FROM unknown_faces WHERE id={PH}', (fid,)
        ).fetchone()
        if row and row['snapshot_path']:
            _safe_remove(os.path.join(base_dir, row['snapshot_path']))
        conn.execute(f'DELETE FROM unknown_faces WHERE id={PH}', (fid,))


def bulk_delete_unknown(ids: list, base_dir: str) -> int:
    deleted = 0
    with get_db() as conn:
        for fid in ids:
            row = conn.execute(
                f'SELECT snapshot_path FROM unknown_faces WHERE id={PH}', (fid,)
            ).fetchone()
            if row:
                if row['snapshot_path']:
                    _safe_remove(os.path.join(base_dir, row['snapshot_path']))
                conn.execute(
                    f'DELETE FROM unknown_faces WHERE id={PH}', (fid,)
                )
                deleted += 1
    return deleted


def unsynced_counts() -> dict:
    """Return count of records not yet pushed to the cloud portal."""
    with get_db() as conn:
        try:
            pa = conn.execute(
                'SELECT COUNT(*) FROM attendance WHERE synced=0'
            ).fetchone()[0]
            pu = conn.execute(
                'SELECT COUNT(*) FROM unknown_faces WHERE synced=0'
            ).fetchone()[0]
        except Exception:
            pa = pu = 0
    return {'pending_attendance': pa, 'pending_unknown': pu}


# ── Dashboard ─────────────────────────────────────────────────────────────────

def dashboard_stats(camera_manager) -> dict:
    """
    Aggregate statistics for the main dashboard.
    camera_manager is injected from current_app to avoid circular imports.
    """
    today       = datetime.now().strftime('%Y-%m-%d')
    this_month  = datetime.now().strftime('%Y-%m')
    work_start  = get_setting('work_start', '09:00')
    late_thresh = int(get_setting('late_threshold', '15'))
    late_dt     = (
        datetime.strptime(f"{today} {work_start}", '%Y-%m-%d %H:%M')
        + timedelta(minutes=late_thresh)
    ).strftime('%Y-%m-%d %H:%M')

    with get_db() as conn:
        total_emp = conn.execute(
            'SELECT COUNT(*) FROM users WHERE active=1'
        ).fetchone()[0]

        present = conn.execute(
            f"SELECT COUNT(DISTINCT user_id) FROM attendance "
            f"WHERE DATE(punch_time)={PH} AND punch_type='IN'",
            (today,),
        ).fetchone()[0]

        unknown_cnt = conn.execute(
            f"SELECT COUNT(*) FROM unknown_faces WHERE DATE(detected_at)={PH}",
            (today,),
        ).fetchone()[0]

        late_count = conn.execute(
            f"SELECT COUNT(DISTINCT user_id) FROM attendance "
            f"WHERE DATE(punch_time)={PH} AND punch_type='IN' AND punch_time>{PH}",
            (today, late_dt),
        ).fetchone()[0]

        dept_stats = conn.execute(
            f'SELECT u.department, '
            f'       COUNT(DISTINCT u.id)      AS total, '
            f"       COUNT(DISTINCT a.user_id) AS present "
            f'FROM users u '
            f'LEFT JOIN attendance a ON a.user_id=u.id '
            f"  AND DATE(a.punch_time)={PH} AND a.punch_type='IN' "
            f'WHERE u.active=1 AND u.department IS NOT NULL '
            f'GROUP BY u.department ORDER BY u.department',
            (today,),
        ).fetchall()

        recent = conn.execute(
            'SELECT a.punch_type, a.punch_time, a.confidence, u.name, u.emp_id '
            'FROM attendance a JOIN users u ON u.id=a.user_id '
            'ORDER BY a.id DESC LIMIT 10'
        ).fetchall()

        # Weekly trend — last 7 days
        trend = []
        for i in range(6, -1, -1):
            d = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            p = conn.execute(
                f"SELECT COUNT(DISTINCT user_id) FROM attendance "
                f"WHERE DATE(punch_time)={PH} AND punch_type='IN'",
                (d,),
            ).fetchone()[0]
            trend.append({'date': d, 'present': p})

        # Hourly punch-in heatmap for today
        hourly = conn.execute(
            'SELECT CAST(strftime(\'%H\', punch_time) AS INTEGER) AS hour, '
            '       COUNT(*) AS count '
            f"FROM attendance WHERE DATE(punch_time)={PH} AND punch_type='IN' "
            'GROUP BY hour ORDER BY hour',
            (today,),
        ).fetchall()
        hourly_map  = {r['hour']: r['count'] for r in hourly}
        hourly_data = [{'hour': h, 'count': hourly_map.get(h, 0)} for h in range(6, 21)]

        # Top 5 late arrivals this month
        top_late = conn.execute(
            'SELECT u.name, u.emp_id, u.department, COUNT(*) AS late_days '
            'FROM attendance a JOIN users u ON u.id=a.user_id '
            f"WHERE a.punch_type='IN' "
            f"  AND strftime('%Y-%m', a.punch_time)={PH} "
            f"  AND TIME(a.punch_time) > TIME({PH}, '+' || {PH} || ' minutes') "
            'GROUP BY u.id ORDER BY late_days DESC LIMIT 5',
            (this_month, work_start + ':00', str(late_thresh)),
        ).fetchall()

        # Camera status
        all_cameras = conn.execute(
            'SELECT COUNT(*) FROM cameras WHERE active=1'
        ).fetchone()[0]

        # Unknown face alerts — last 5 unreviewed today
        unknown_alerts = conn.execute(
            'SELECT uf.id, uf.snapshot_path, uf.detected_at, c.name AS camera_name '
            'FROM unknown_faces uf '
            'LEFT JOIN cameras c ON c.id=uf.camera_id '
            f'WHERE DATE(uf.detected_at)={PH} AND uf.reviewed=0 '
            'ORDER BY uf.detected_at DESC LIMIT 5',
            (today,),
        ).fetchall()

    cam_status = camera_manager.status() if camera_manager else {}
    cams_live  = sum(1 for v in cam_status.values() if v.get('frames'))

    return {
        'total_employees': total_emp,
        'present_today':   present,
        'absent_today':    total_emp - present,
        'late_today':      late_count,
        'unknown_today':   unknown_cnt,
        'dept_stats':      [dict(r) for r in dept_stats],
        'recent_activity': [dict(r) for r in recent],
        'weekly_trend':    trend,
        'hourly_heatmap':  hourly_data,
        'top_late_month':  [dict(r) for r in top_late],
        'camera_status':   {
            'total':   all_cameras,
            'live':    cams_live,
            'offline': all_cameras - cams_live,
        },
        'unknown_alerts':  [dict(r) for r in unknown_alerts],
    }


# ── Reports ───────────────────────────────────────────────────────────────────

def report_attendance(date_from: str, date_to: str, dept: Optional[str] = None) -> list:
    sql = (
        'SELECT u.emp_id, u.name, u.department, u.designation, '
        '       DATE(a.punch_time) AS date, '
        f"       MIN(CASE WHEN a.punch_type='IN'  THEN a.punch_time END) AS first_in, "
        f"       MAX(CASE WHEN a.punch_type='OUT' THEN a.punch_time END) AS last_out, "
        f"       COUNT(CASE WHEN a.punch_type='IN' THEN 1 END) AS in_count "
        'FROM users u '
        f'LEFT JOIN attendance a ON a.user_id=u.id AND DATE(a.punch_time) BETWEEN {PH} AND {PH} '
        'WHERE u.active=1'
    )
    args: list = [date_from, date_to]
    if dept:
        sql += f' AND u.department={PH}'; args.append(dept)
    sql += ' GROUP BY u.id, DATE(a.punch_time) ORDER BY date DESC, u.name'

    with get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def report_monthly_summary(month: str, dept: Optional[str] = None) -> dict:
    year, mon = map(int, month.split('-'))
    date_from = f'{month}-01'
    last_day  = (
        datetime(year, mon, 1).replace(day=28) + timedelta(days=4)
    ).replace(day=1) - timedelta(days=1)
    date_to = last_day.strftime('%Y-%m-%d')

    working_days = working_days_in_range(date_from, date_to)
    wday_count   = len(working_days)
    holidays_set = get_holidays_set(date_from, date_to)

    work_start = get_setting('work_start', '09:00')
    late_mins  = int(get_setting('late_threshold', '15'))

    sql  = (
        'SELECT u.id, u.emp_id, u.name, u.department, u.designation, '
        f"       COUNT(DISTINCT CASE WHEN a.punch_type='IN' THEN DATE(a.punch_time) END) "
        f'       AS days_present '
        'FROM users u '
        f"LEFT JOIN attendance a ON a.user_id=u.id AND strftime('%Y-%m', a.punch_time)={PH} "
        'WHERE u.active=1'
    )
    args: list = [month]
    if dept:
        sql += f' AND u.department={PH}'; args.append(dept)
    sql += ' GROUP BY u.id ORDER BY u.name'

    with get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
        late_rows = conn.execute(
            'SELECT u.id, '
            '       COUNT(DISTINCT CASE '
            f"           WHEN a.punch_type='IN' "
            f"                AND time(a.punch_time) > time({PH}, '+'||{PH}||' minutes') "
            '           THEN DATE(a.punch_time) END) AS late_days '
            'FROM users u '
            f"LEFT JOIN attendance a ON a.user_id=u.id AND strftime('%Y-%m', a.punch_time)={PH} "
            'WHERE u.active=1 GROUP BY u.id',
            (work_start, late_mins, month),
        ).fetchall()

    late_map = {r['id']: r['late_days'] for r in late_rows}

    data = []
    for r in rows:
        pct = round((r['days_present'] / wday_count) * 100, 1) if wday_count else 0
        data.append({
            'emp_id':          r['emp_id'],
            'name':            r['name'],
            'department':      r['department'] or '',
            'designation':     r['designation'] or '',
            'days_present':    r['days_present'],
            'days_absent':     max(0, wday_count - r['days_present']),
            'days_late':       late_map.get(r['id'], 0),
            'working_days':    wday_count,
            'attendance_pct':  pct,
        })

    return {
        'rows':         data,
        'working_days': wday_count,
        'holidays':     sorted(holidays_set),
        'month':        month,
    }


def report_late_arrivals(
    date_from: str,
    date_to:   str,
    dept:      Optional[str] = None,
) -> list:
    work_start = get_setting('work_start', '09:00')
    late_mins  = int(get_setting('late_threshold', '15'))

    sql = (
        'SELECT u.emp_id, u.name, u.department, u.designation, '
        '       DATE(a.punch_time) AS date, MIN(a.punch_time) AS first_in '
        'FROM attendance a JOIN users u ON u.id = a.user_id '
        f"WHERE a.punch_type='IN' AND DATE(a.punch_time) BETWEEN {PH} AND {PH}"
    )
    args: list = [date_from, date_to]
    if dept:
        sql += f' AND u.department={PH}'; args.append(dept)
    sql += ' GROUP BY u.id, DATE(a.punch_time) ORDER BY date DESC, first_in DESC'

    with get_db() as conn:
        rows = conn.execute(sql, args).fetchall()

    late = []
    for r in rows:
        fi_str = r['first_in']
        if not fi_str:
            continue
        try:
            fmt = '%Y-%m-%d %H:%M:%S' if len(fi_str) > 16 else '%Y-%m-%d %H:%M'
            fi  = datetime.strptime(fi_str, fmt)
            deadline = (
                datetime.strptime(f"{r['date']} {work_start}", '%Y-%m-%d %H:%M')
                + timedelta(minutes=late_mins)
            )
            if fi > deadline:
                mins_late = int((fi - deadline).total_seconds() / 60)
                late.append({
                    **dict(r),
                    'first_in':    fi_str,
                    'minutes_late': mins_late,
                    'deadline':    deadline.strftime('%H:%M'),
                })
        except (ValueError, TypeError):
            continue
    return late


def report_absentees(
    date_from: str,
    date_to:   str,
    users:     list,        # list of user dicts (from models.users.get_active_users)
) -> list:
    """
    Cross-join users × working days to find absences.
    `users` is pre-fetched by the route so we can filter by dept there.
    """
    start = datetime.strptime(date_from, '%Y-%m-%d')
    end   = datetime.strptime(date_to,   '%Y-%m-%d')
    dates = [
        (start + timedelta(days=i)).strftime('%Y-%m-%d')
        for i in range((end - start).days + 1)
    ]

    absent = []
    with get_db() as conn:
        for d in dates:
            present_ids = {
                r[0] for r in conn.execute(
                    f"SELECT DISTINCT user_id FROM attendance "
                    f"WHERE DATE(punch_time)={PH} AND punch_type='IN'",
                    (d,),
                ).fetchall()
            }
            for u in users:
                if u['id'] not in present_ids:
                    absent.append({
                        'date':        d,
                        'emp_id':      u['emp_id'],
                        'name':        u['name'],
                        'department':  u['department'],
                        'designation': u['designation'],
                    })

    absent.sort(key=lambda x: (x['date'], x['name']))
    return absent


def report_working_hours(
    date_from: str,
    date_to:   str,
    dept:      Optional[str] = None,
) -> list:
    work_end_str   = get_setting('work_end',   '18:00')
    work_start_str = get_setting('work_start', '09:00')
    std_hours = (
        datetime.strptime(work_end_str,   '%H:%M') -
        datetime.strptime(work_start_str, '%H:%M')
    ).seconds / 3600

    sql = (
        'SELECT u.emp_id, u.name, u.department, u.designation, '
        '       DATE(a.punch_time) AS date, '
        f"       MIN(CASE WHEN a.punch_type='IN'  THEN a.punch_time END) AS first_in, "
        f"       MAX(CASE WHEN a.punch_type='OUT' THEN a.punch_time END) AS last_out "
        'FROM users u '
        f'JOIN attendance a ON a.user_id=u.id '
        f'WHERE DATE(a.punch_time) BETWEEN {PH} AND {PH} AND u.active=1'
    )
    args: list = [date_from, date_to]
    if dept:
        sql += f' AND u.department={PH}'; args.append(dept)
    sql += ' GROUP BY u.id, DATE(a.punch_time) ORDER BY date DESC, u.name'

    with get_db() as conn:
        rows = conn.execute(sql, args).fetchall()

    result = []
    for r in rows:
        fi, lo = r['first_in'], r['last_out']
        hours_worked = overtime = None
        if fi and lo:
            try:
                fmt_in  = '%Y-%m-%d %H:%M:%S' if len(fi) > 16 else '%Y-%m-%d %H:%M'
                fmt_out = '%Y-%m-%d %H:%M:%S' if len(lo) > 16 else '%Y-%m-%d %H:%M'
                delta   = datetime.strptime(lo, fmt_out) - datetime.strptime(fi, fmt_in)
                hours_worked = round(delta.total_seconds() / 3600, 2)
                overtime     = round(max(0.0, hours_worked - std_hours), 2)
            except (ValueError, TypeError):
                pass
        result.append({
            **dict(r),
            'hours_worked':  hours_worked,
            'overtime_hours': overtime,
            'std_hours':     std_hours,
        })
    return result


def report_employee_history(uid: int, date_from: str, date_to: str) -> Optional[dict]:
    with get_db() as conn:
        user = conn.execute(
            'SELECT id, emp_id, name, department, designation, email, phone, '
            f'       photo_path, enrolled_at FROM users WHERE id={PH}',
            (uid,),
        ).fetchone()
        if not user:
            return None

        records = conn.execute(
            'SELECT a.id, a.punch_type, a.punch_time, a.confidence, a.snapshot_path, '
            '       c.name AS camera_name, c.location '
            'FROM attendance a LEFT JOIN cameras c ON c.id=a.camera_id '
            f'WHERE a.user_id={PH} AND DATE(a.punch_time) BETWEEN {PH} AND {PH} '
            'ORDER BY a.punch_time DESC',
            (uid, date_from, date_to),
        ).fetchall()

        daily = conn.execute(
            "SELECT DATE(punch_time) AS date, "
            "       MIN(CASE WHEN punch_type='IN'  THEN punch_time END) AS first_in, "
            "       MAX(CASE WHEN punch_type='OUT' THEN punch_time END) AS last_out, "
            '       COUNT(*) AS total_punches '
            f'FROM attendance WHERE user_id={PH} AND DATE(punch_time) BETWEEN {PH} AND {PH} '
            'GROUP BY DATE(punch_time) ORDER BY date DESC',
            (uid, date_from, date_to),
        ).fetchall()

    return {
        'employee':           dict(user),
        'records':            [dict(r) for r in records],
        'daily':              [dict(r) for r in daily],
        'total_days_present': len(daily),
    }


# ── Cloud sync receive helpers ────────────────────────────────────────────────

def sync_receive_attendance(records: list, site_id: str, is_pg: bool) -> int:
    inserted = 0
    with get_db() as conn:
        for r in records:
            try:
                emp_id = r.get('emp_id')
                user_row = conn.execute(
                    f'SELECT id FROM users WHERE emp_id={PH}', (emp_id,)
                ).fetchone()
                if not user_row:
                    continue
                uid = user_row['id']

                if is_pg:
                    conn.execute(
                        'INSERT INTO attendance '
                        '(user_id, camera_id, site_id, punch_type, confidence, snapshot_path, punch_time) '
                        f'VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH})',
                        (uid, r.get('camera_id'), site_id, r['punch_type'],
                         r.get('confidence', 0), r.get('snapshot_path'), r.get('punch_time')),
                    )
                else:
                    conn.execute(
                        'INSERT INTO attendance '
                        '(user_id, camera_id, punch_type, confidence, snapshot_path, punch_time, synced) '
                        f'VALUES ({PH},{PH},{PH},{PH},{PH},{PH},1)',
                        (uid, r.get('camera_id'), r['punch_type'],
                         r.get('confidence', 0), r.get('snapshot_path'), r.get('punch_time')),
                    )
                inserted += 1
            except Exception as exc:
                log.warning("[Sync] Attendance insert error: %s", exc)
    return inserted


def sync_receive_unknown_faces(records: list, site_id: str, is_pg: bool) -> int:
    inserted = 0
    with get_db() as conn:
        for r in records:
            try:
                if is_pg:
                    conn.execute(
                        'INSERT INTO unknown_faces '
                        f'(camera_id, site_id, snapshot_path, reviewed, notes, detected_at) '
                        f'VALUES ({PH},{PH},{PH},{PH},{PH},{PH})',
                        (r.get('camera_id'), site_id, r.get('snapshot_path'),
                         r.get('reviewed', 0), r.get('notes'), r.get('detected_at')),
                    )
                else:
                    conn.execute(
                        'INSERT INTO unknown_faces '
                        f'(camera_id, snapshot_path, reviewed, notes, detected_at, synced) '
                        f'VALUES ({PH},{PH},{PH},{PH},{PH},1)',
                        (r.get('camera_id'), r.get('snapshot_path'),
                         r.get('reviewed', 0), r.get('notes'), r.get('detected_at')),
                    )
                inserted += 1
            except Exception as exc:
                log.warning("[Sync] Unknown-face insert error: %s", exc)
    return inserted


def sync_get_employees(since: str, is_pg: bool) -> list:
    with get_db() as conn:
        rows = conn.execute(
            'SELECT emp_id, name, department, designation, email, phone, '
            f'       face_encoding, active, enrolled_at '
            f'FROM users WHERE active=1 AND enrolled_at>{PH} ORDER BY enrolled_at',
            (since,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        enc = d.pop('face_encoding', None)
        d['face_encoding_hex'] = enc.hex() if enc else None
        result.append(d)
    return result


def sync_log_entries(limit: int = 50) -> list:
    with get_db() as conn:
        rows = conn.execute(
            f'SELECT * FROM sync_log ORDER BY id DESC LIMIT {limit}'
        ).fetchall()
    return [dict(r) for r in rows]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _safe_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as exc:
        log.warning("[File] Could not remove %s: %s", path, exc)
