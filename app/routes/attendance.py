"""
app/routes/attendance.py — Attendance & Reports Blueprint
=========================================================
Endpoints
---------
GET    /api/attendance               — Paginated attendance log
POST   /api/attendance/manual        — Manual punch entry
DELETE /api/attendance/<aid>         — Delete a record
GET    /api/attendance/today-summary — Per-employee today summary

GET    /api/unknown-faces            — Unknown face list
PUT    /api/unknown-faces/<fid>/review — Mark as reviewed
DELETE /api/unknown-faces/<fid>       — Delete unknown face
POST   /api/unknown-faces/bulk-delete — Bulk delete

GET    /api/reports/attendance       — Attendance detail report (JSON/CSV)
GET    /api/reports/monthly-summary  — Monthly summary (JSON/CSV)
GET    /api/reports/late-arrivals    — Late arrival report (JSON/CSV)
GET    /api/reports/absentees        — Absentee report (JSON/CSV)
GET    /api/reports/working-hours    — Working hours report (JSON/CSV)
GET    /api/reports/employee-history — Single employee history
"""

import logging
from datetime import datetime

from flask import Blueprint, request, Response, current_app

from app.middleware import login_required, role_required, safe_page
from app.models import ok, err, audit, get_setting
from app.models import attendance as att_dao
from app.models import users as user_dao

log = logging.getLogger('faceattend.routes.attendance')
attendance_bp = Blueprint('attendance', __name__)

_TODAY = lambda: datetime.now().strftime('%Y-%m-%d')


# ── Attendance log ─────────────────────────────────────────────────────────────

@attendance_bp.route('/api/attendance', methods=['GET'])
@login_required
def list_attendance():
    page, per_page = safe_page(
        request.args.get('page', 1),
        request.args.get('per_page', 50),
    )
    result = att_dao.list_attendance(
        date_from  = request.args.get('from', _TODAY()),
        date_to    = request.args.get('to',   _TODAY()),
        user_id    = request.args.get('user_id'),
        dept       = request.args.get('department'),
        punch_type = request.args.get('punch_type'),
        page       = page,
        per_page   = per_page,
    )
    return ok(result)


@attendance_bp.route('/api/attendance/today-summary', methods=['GET'])
@login_required
def today_summary():
    rows = att_dao.today_summary(dept=request.args.get('department'))
    return ok(rows)


@attendance_bp.route('/api/attendance/manual', methods=['POST'])
@role_required('admin', 'manager')
def manual_attendance():
    data       = request.get_json() or {}
    user_id    = data.get('user_id')
    punch_type = data.get('punch_type', 'IN')

    if not user_id:
        return err('user_id is required')
    if punch_type not in ('IN', 'OUT'):
        return err('punch_type must be IN or OUT')

    att_dao.manual_punch(int(user_id), punch_type)
    audit('MANUAL_PUNCH', str(user_id), punch_type)
    return ok(msg=f'Manual {punch_type} logged')


@attendance_bp.route('/api/attendance/<int:aid>', methods=['DELETE'])
@role_required('admin', 'manager')
def delete_attendance(aid):
    att_dao.delete_record(aid)
    audit('DELETE_ATTENDANCE', str(aid))
    return ok(msg='Record deleted')


# ── Unknown faces ──────────────────────────────────────────────────────────────

@attendance_bp.route('/api/unknown-faces', methods=['GET'])
@login_required
def list_unknown():
    page, per_page = safe_page(
        request.args.get('page', 1),
        request.args.get('per_page', 24),
        max_per_page=200,
    )
    result = att_dao.list_unknown_faces(
        page     = page,
        per_page = per_page,
        reviewed = request.args.get('reviewed'),
    )
    return ok(result)


@attendance_bp.route('/api/unknown-faces/<int:fid>/review', methods=['PUT'])
@role_required('admin', 'manager')
def review_unknown(fid):
    data  = request.get_json() or {}
    notes = data.get('notes', '')
    att_dao.review_unknown(fid, notes)
    audit('REVIEW_UNKNOWN', str(fid), notes)
    return ok(msg='Marked as reviewed')


@attendance_bp.route('/api/unknown-faces/<int:fid>', methods=['DELETE'])
@role_required('admin', 'manager')
def delete_unknown(fid):
    base_dir = current_app.config['BASE_DIR']
    att_dao.delete_unknown(fid, base_dir)
    audit('DELETE_UNKNOWN', str(fid))
    return ok(msg='Deleted')


@attendance_bp.route('/api/unknown-faces/bulk-delete', methods=['POST'])
@role_required('admin', 'manager')
def bulk_delete_unknown():
    data = request.get_json() or {}
    ids  = data.get('ids', [])
    if not ids:
        return err('No IDs provided')
    base_dir = current_app.config['BASE_DIR']
    deleted  = att_dao.bulk_delete_unknown(ids, base_dir)
    audit('BULK_DELETE_UNKNOWN', '', f'ids={ids}')
    return ok({'deleted': deleted}, f'{deleted} record(s) deleted')


# ── Reports ────────────────────────────────────────────────────────────────────

@attendance_bp.route('/api/reports/attendance', methods=['GET'])
@login_required
def report_attendance():
    date_from = request.args.get('from', _TODAY())
    date_to   = request.args.get('to',   _TODAY())
    dept      = request.args.get('department')
    fmt       = request.args.get('format', 'json')

    data = att_dao.report_attendance(date_from, date_to, dept)

    if fmt == 'csv':
        work_start = get_setting('work_start', '09:00')
        late_mins  = int(get_setting('late_threshold', '15'))
        from datetime import timedelta

        def gen_csv():
            yield 'Emp ID,Name,Department,Designation,Date,First IN,Last OUT,IN Count,Status\n'
            for r in data:
                if r['first_in']:
                    try:
                        fi     = datetime.strptime(r['first_in'], '%Y-%m-%d %H:%M:%S')
                        ws     = (datetime.strptime(f"{r['date']} {work_start}", '%Y-%m-%d %H:%M')
                                  + timedelta(minutes=late_mins))
                        status = 'Late' if fi > ws else 'On Time'
                    except Exception:
                        status = 'Present'
                elif r.get('date'):
                    status = 'Absent'
                else:
                    status = '-'
                row = [
                    r['emp_id'] or '', r['name'], r['department'] or '',
                    r['designation'] or '', r['date'] or '',
                    r['first_in'] or '', r['last_out'] or '',
                    str(r['in_count'] or 0), status,
                ]
                yield ','.join(f'"{v}"' for v in row) + '\n'

        return Response(
            gen_csv(), mimetype='text/csv',
            headers={'Content-Disposition':
                     f'attachment; filename=attendance_{date_from}_{date_to}.csv'},
        )
    return ok(data)


@attendance_bp.route('/api/reports/monthly-summary', methods=['GET'])
@login_required
def monthly_summary():
    month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    dept  = request.args.get('department', '')
    fmt   = request.args.get('format', 'json')

    result = att_dao.report_monthly_summary(month, dept or None)

    if fmt == 'csv':
        def gen():
            yield 'Emp ID,Name,Department,Designation,Working Days,Days Present,Days Absent,Days Late,Attendance %\n'
            for r in result['rows']:
                yield (f'"{r["emp_id"]}","{r["name"]}","{r["department"]}",'
                       f'"{r["designation"]}",{r["working_days"]},{r["days_present"]},'
                       f'{r["days_absent"]},{r["days_late"]},{r["attendance_pct"]}\n')
        return Response(
            gen(), mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=monthly_{month}.csv'},
        )
    return ok(result)


@attendance_bp.route('/api/reports/late-arrivals', methods=['GET'])
@login_required
def report_late_arrivals():
    date_from = request.args.get('from', _TODAY())
    date_to   = request.args.get('to',   _TODAY())
    dept      = request.args.get('department')
    fmt       = request.args.get('format', 'json')

    late = att_dao.report_late_arrivals(date_from, date_to, dept)

    if fmt == 'csv':
        def gen():
            yield 'Emp ID,Name,Department,Designation,Date,First IN,Deadline,Minutes Late\n'
            for r in late:
                yield (f'"{r["emp_id"]}","{r["name"]}",'
                       f'"{r.get("department") or ""}","{r.get("designation") or ""}",'
                       f'"{r["date"]}","{r["first_in"]}","{r["deadline"]}",'
                       f'{r["minutes_late"]}\n')
        return Response(
            gen(), mimetype='text/csv',
            headers={'Content-Disposition':
                     f'attachment; filename=late_arrivals_{date_from}_{date_to}.csv'},
        )
    return ok(late)


@attendance_bp.route('/api/reports/absentees', methods=['GET'])
@login_required
def report_absentees():
    date_from = request.args.get('from', _TODAY())
    date_to   = request.args.get('to',   _TODAY())
    dept      = request.args.get('department')
    fmt       = request.args.get('format', 'json')

    users   = user_dao.get_active_users(dept)
    absent  = att_dao.report_absentees(date_from, date_to, users)

    if fmt == 'csv':
        def gen():
            yield 'Date,Emp ID,Name,Department,Designation\n'
            for r in absent:
                yield (f'"{r["date"]}","{r["emp_id"]}","{r["name"]}",'
                       f'"{r.get("department") or ""}","{r.get("designation") or ""}"\n')
        return Response(
            gen(), mimetype='text/csv',
            headers={'Content-Disposition':
                     f'attachment; filename=absentees_{date_from}_{date_to}.csv'},
        )
    return ok(absent)


@attendance_bp.route('/api/reports/working-hours', methods=['GET'])
@login_required
def report_working_hours():
    date_from = request.args.get('from', _TODAY())
    date_to   = request.args.get('to',   _TODAY())
    dept      = request.args.get('department')
    fmt       = request.args.get('format', 'json')

    result = att_dao.report_working_hours(date_from, date_to, dept)

    if fmt == 'csv':
        def gen():
            yield 'Emp ID,Name,Department,Date,First IN,Last OUT,Hours Worked,Overtime,Status\n'
            for r in result:
                hw = r['hours_worked']
                ot = r['overtime_hours']
                st = ('Overtime' if (ot or 0) > 0 else 'Normal') if hw else 'No OUT punch'
                yield (f'"{r["emp_id"]}","{r["name"]}","{r.get("department") or ""}",'
                       f'"{r["date"]}","{r.get("first_in") or ""}","{r.get("last_out") or ""}",'
                       f'{hw or ""},{ot or ""},"{st}"\n')
        return Response(
            gen(), mimetype='text/csv',
            headers={'Content-Disposition':
                     f'attachment; filename=working_hours_{date_from}_{date_to}.csv'},
        )
    return ok(result)


@attendance_bp.route('/api/reports/employee-history', methods=['GET'])
@login_required
def report_employee_history():
    uid       = request.args.get('user_id')
    date_from = request.args.get('from', (datetime.now() - __import__('datetime').timedelta(days=30)).strftime('%Y-%m-%d'))
    date_to   = request.args.get('to',   _TODAY())

    if not uid:
        return err('user_id is required')

    result = att_dao.report_employee_history(int(uid), date_from, date_to)
    if result is None:
        return err('Employee not found', 404)
    return ok(result)
