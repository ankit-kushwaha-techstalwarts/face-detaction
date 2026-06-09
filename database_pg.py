"""
Face Recognition Attendance System — PostgreSQL Database Adapter
================================================================
Used by the cloud-hosted web portal (CLOUD_MODE=1).
Implements the same interface as database.py so app.py can switch
transparently based on the DATABASE_URL environment variable.

Required env var:
    DATABASE_URL=postgresql://user:password@host:5432/dbname

Install:
    pip install psycopg2-binary
"""

import os
import logging
from datetime import datetime

log = logging.getLogger(__name__)

try:
    import psycopg2
    import psycopg2.extras
    _PG_OK = True
except ImportError:
    _PG_OK = False
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

DATABASE_URL = os.environ['DATABASE_URL']  # mandatory in cloud mode


# ─────────────────────────────────────────────────────────────
#  Connection
# ─────────────────────────────────────────────────────────────

def get_db():
    """
    Return a psycopg2 connection using RealDictCursor so rows behave
    like sqlite3.Row (column access by name).
    """
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = False
    return conn


# ─────────────────────────────────────────────────────────────
#  Schema
# ─────────────────────────────────────────────────────────────

def init_db():
    """Create tables and seed defaults (idempotent — safe to run on every start)."""
    conn = get_db()
    cur  = conn.cursor()

    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id          SERIAL PRIMARY KEY,
            emp_id      TEXT UNIQUE NOT NULL,
            name        TEXT NOT NULL,
            department  TEXT,
            designation TEXT,
            email       TEXT,
            phone       TEXT,
            photo_path  TEXT,
            face_encoding BYTEA,
            active      INTEGER DEFAULT 1,
            enrolled_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            created_at  TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            camera_id   INTEGER,
            site_id     TEXT DEFAULT 'site1',
            punch_type  TEXT CHECK(punch_type IN (\'IN\',\'OUT\')) NOT NULL,
            confidence  REAL DEFAULT 0.0,
            snapshot_path TEXT,
            punch_time  TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            created_at  TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            synced      INTEGER DEFAULT 1
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS unknown_faces (
            id            SERIAL PRIMARY KEY,
            camera_id     INTEGER,
            site_id       TEXT DEFAULT 'site1',
            snapshot_path TEXT,
            reviewed      INTEGER DEFAULT 0,
            notes         TEXT,
            detected_at   TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
            synced        INTEGER DEFAULT 1
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS cameras (
            id          SERIAL PRIMARY KEY,
            name        TEXT NOT NULL,
            rtsp_url    TEXT NOT NULL,
            location    TEXT,
            active      INTEGER DEFAULT 1,
            direction   TEXT CHECK(direction IN (\'ENTRY\',\'EXIT\',\'BOTH\')) DEFAULT \'BOTH\',
            last_ping   TEXT,
            created_at  TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key      TEXT PRIMARY KEY,
            value    TEXT,
            label    TEXT,
            category TEXT DEFAULT 'general'
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS departments (
            id   SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS admin_users (
            id            SERIAL PRIMARY KEY,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT CHECK(role IN (\'admin\',\'manager\',\'guard\')) DEFAULT \'manager\',
            full_name     TEXT DEFAULT '',
            department    TEXT DEFAULT '',
            active        INTEGER DEFAULT 1,
            last_login    TEXT,
            created_at    TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS holidays (
            id         SERIAL PRIMARY KEY,
            date       TEXT UNIQUE NOT NULL,
            name       TEXT NOT NULL,
            type       TEXT CHECK(type IN (\'national\',\'state\',\'restricted\',\'office\')) DEFAULT \'national\',
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id         SERIAL PRIMARY KEY,
            username   TEXT NOT NULL,
            role       TEXT,
            action     TEXT NOT NULL,
            target     TEXT,
            detail     TEXT,
            ip_address TEXT,
            site_id    TEXT DEFAULT 'cloud',
            created_at TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS sync_log (
            id         SERIAL PRIMARY KEY,
            site_id    TEXT NOT NULL,
            direction  TEXT CHECK(direction IN (\'PUSH\',\'PULL\')) NOT NULL,
            entity     TEXT NOT NULL,
            records    INTEGER DEFAULT 0,
            status     TEXT CHECK(status IN (\'ok\',\'error\')) DEFAULT \'ok\',
            detail     TEXT,
            synced_at  TEXT DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        )
    ''')

    # ── Indexes ────────────────────────────────────────────────
    for ddl in [
        'CREATE INDEX IF NOT EXISTS idx_att_punch_time ON attendance(punch_time)',
        'CREATE INDEX IF NOT EXISTS idx_att_user_punch  ON attendance(user_id, punch_time)',
        'CREATE INDEX IF NOT EXISTS idx_att_site        ON attendance(site_id, punch_time)',
        'CREATE INDEX IF NOT EXISTS idx_users_emp_id    ON users(emp_id)',
        'CREATE INDEX IF NOT EXISTS idx_users_active    ON users(active, department)',
        'CREATE INDEX IF NOT EXISTS idx_unk_detected    ON unknown_faces(detected_at)',
        'CREATE INDEX IF NOT EXISTS idx_audit_created   ON audit_log(created_at, username)',
        'CREATE INDEX IF NOT EXISTS idx_holiday_date    ON holidays(date)',
    ]:
        cur.execute(ddl)

    # ── Seed defaults ──────────────────────────────────────────
    default_settings = [
        ('org_name',        'Government Department',     'Organisation Name',         'general'),
        ('org_title',       'FaceAttend Cloud',          'Browser / App Title',       'general'),
        ('org_logo',        '',                          'Organisation Logo Path',    'general'),
        ('primary_color',   '#1a56db',                   'Primary Colour',            'theme'),
        ('accent_color',    '#057a55',                   'Accent Colour',             'theme'),
        ('favicon_path',    '',                          'Favicon Path',              'theme'),
        ('work_start',      '09:00',                     'Work Start Time',           'attendance'),
        ('work_end',        '18:00',                     'Work End Time',             'attendance'),
        ('late_threshold',  '15',                        'Late Threshold (minutes)',  'attendance'),
        ('work_days',       '1,2,3,4,5',                 'Working Days',              'attendance'),
        ('auto_out',        '1',                         'Auto Punch-OUT at Day End', 'attendance'),
        ('timezone',        'Asia/Kolkata',               'Timezone',                  'general'),
        ('session_timeout', '30',   'Session Timeout (minutes)',                        'compliance'),
        ('data_retention',  '1095', 'Attendance Data Retention (days)',                 'compliance'),
        ('audit_retention', '1825', 'Audit Log Retention (days)',                       'compliance'),
        ('min_pw_length',   '8',    'Minimum Password Length',                          'compliance'),
        ('require_strong_pw','1',   'Enforce Strong Password',                         'compliance'),
        ('max_login_attempts','5',  'Max Failed Login Attempts',                        'compliance'),
        ('sync_api_key',    '',     'Sync API Key (set same on all local sites)',        'sync'),
    ]
    for row in default_settings:
        cur.execute(
            'INSERT INTO settings (key, value, label, category) VALUES (%s,%s,%s,%s) ON CONFLICT(key) DO NOTHING',
            row
        )

    for dept in ['Administration', 'Finance', 'IT', 'HR', 'Operations', 'Security']:
        cur.execute('INSERT INTO departments (name) VALUES (%s) ON CONFLICT(name) DO NOTHING', (dept,))

    conn.commit()
    cur.close()
    conn.close()
    log.info("[DB-PG] PostgreSQL database initialised.")


# ─────────────────────────────────────────────────────────────
#  Settings helpers
# ─────────────────────────────────────────────────────────────

def get_setting(key, default=None):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute('SELECT value FROM settings WHERE key=%s', (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row['value'] if row else default


def set_setting(key, value):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute(
        'INSERT INTO settings (key, value) VALUES (%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value',
        (key, value)
    )
    conn.commit()
    cur.close()
    conn.close()


if __name__ == '__main__':
    init_db()
