"""
Face Recognition Attendance System - Database Module
SQLite database setup and helper functions
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'attendance.db')


def get_db():
    """
    Get a database connection optimised for concurrent multi-camera writes.
    WAL mode + busy_timeout prevent 'database is locked' errors when
    multiple camera threads write attendance records simultaneously.
    """
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")    # safe with WAL, faster than FULL
    conn.execute("PRAGMA cache_size=-32000")     # 32 MB page cache for large user sets
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")    # wait up to 10 s on write contention
    return conn


def init_db():
    """Initialize database schema."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    cursor = conn.cursor()

    # Users / Employee table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id      TEXT UNIQUE NOT NULL,
            name        TEXT NOT NULL,
            department  TEXT,
            designation TEXT,
            email       TEXT,
            phone       TEXT,
            photo_path  TEXT,
            face_encoding BLOB,
            active      INTEGER DEFAULT 1,
            enrolled_at TEXT DEFAULT (datetime('now','localtime')),
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')

    # Attendance records
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            camera_id   INTEGER,
            punch_type  TEXT CHECK(punch_type IN ('IN','OUT')) NOT NULL,
            confidence  REAL DEFAULT 0.0,
            snapshot_path TEXT,
            punch_time  TEXT DEFAULT (datetime('now','localtime')),
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            synced      INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')

    # Unknown / unregistered faces
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS unknown_faces (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id     INTEGER,
            snapshot_path TEXT,
            reviewed      INTEGER DEFAULT 0,
            notes         TEXT,
            detected_at   TEXT DEFAULT (datetime('now','localtime')),
            synced        INTEGER DEFAULT 0
        )
    ''')

    # CCTV Cameras
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cameras (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            rtsp_url    TEXT NOT NULL,
            location    TEXT,
            active      INTEGER DEFAULT 1,
            direction   TEXT CHECK(direction IN ('ENTRY','EXIT','BOTH')) DEFAULT 'BOTH',
            last_ping   TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')

    # Application settings
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT,
            label TEXT,
            category TEXT DEFAULT 'general'
        )
    ''')

    # Departments lookup
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS departments (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    ''')

    # ── System / Admin Users (role-based access control) ───────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT CHECK(role IN ('admin','manager','guard')) DEFAULT 'manager',
            full_name     TEXT DEFAULT '',
            department    TEXT DEFAULT '',
            active        INTEGER DEFAULT 1,
            last_login    TEXT,
            created_at    TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')

    # ── Holidays table (national / state / restricted) ────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS holidays (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT UNIQUE NOT NULL,
            name        TEXT NOT NULL,
            type        TEXT CHECK(type IN ('national','state','restricted','office')) DEFAULT 'national',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')

    # ── Cloud Sync Log ────────────────────────────────────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            direction   TEXT CHECK(direction IN ('PUSH','PULL')) NOT NULL,
            entity      TEXT NOT NULL,
            records     INTEGER DEFAULT 0,
            status      TEXT CHECK(status IN ('ok','error')) DEFAULT 'ok',
            detail      TEXT,
            synced_at   TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')

    # ── Face template gallery ─────────────────────────────────
    # Multiple embeddings per user. users.face_encoding stays the trusted
    # "anchor" (synced from the cloud portal); rows here add extra enrolment
    # angles plus embeddings auto-learned from confident live matches.
    #   source: 'enroll' = extra enrolment photo
    #           'merge'  = operator assigned an unknown snapshot to this user
    #           'auto'   = self-learned from a high-confidence camera match
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS face_templates (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            embedding   BLOB NOT NULL,
            source      TEXT CHECK(source IN ('enroll','merge','auto')) DEFAULT 'enroll',
            camera_id   INTEGER,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_face_templates_user
            ON face_templates(user_id, source)
    ''')

    # ── Audit / Activity Log ──────────────────────────────────
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL,
            role        TEXT,
            action      TEXT NOT NULL,
            target      TEXT,
            detail      TEXT,
            ip_address  TEXT,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')

    # Index for audit log queries
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_audit_created
            ON audit_log(created_at, username)
    ''')

    # Index for holiday date lookups
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_holiday_date
            ON holidays(date)
    ''')

    # Seed default settings
    default_settings = [
        ('org_name',        'Government Department',     'Organisation Name',         'general'),
        ('org_title',       'FaceAttend',                'Browser / App Title',       'general'),
        ('org_logo',        '',                          'Organisation Logo Path',    'general'),
        ('primary_color',   '#1a56db',                   'Primary Colour (hex)',      'theme'),
        ('accent_color',    '#057a55',                   'Accent Colour (hex)',       'theme'),
        ('favicon_path',    '',                          'Favicon Path',              'theme'),
        ('work_start',      '09:00',                     'Work Start Time',           'attendance'),
        ('work_end',        '18:00',                     'Work End Time',             'attendance'),
        ('late_threshold',  '15',                        'Late Threshold (minutes)',  'attendance'),
        ('work_days',       '1,2,3,4,5',                 'Working Days (1=Mon…7=Sun)','attendance'),
        ('recognition_tol', '0.50',                      'Face Recognition Tolerance','recognition'),
        ('min_face_size',   '40',                        'Minimum Face Size (px)',    'recognition'),
        ('frame_skip',      '5',                         'Process Every N Frames',    'recognition'),
        ('det_score_thresh','0.50',  'Min Face Detection Confidence (0–1)',           'recognition'),
        ('match_consecutive','2',    'Consecutive Matches Required to Punch',         'recognition'),
        ('low_light_boost', '1',     'Low-Light Frame Enhancement (1=ON)',            'recognition'),
        ('face_model',      'buffalo_l', 'InsightFace Model (buffalo_l / buffalo_sc)','recognition'),
        ('det_size',        '640',   'Detector Input Size (px, restart required)',    'recognition'),
        ('auto_learn',           '1',    'Self-Learn From Confident Matches (1=ON)',  'recognition'),
        ('auto_learn_threshold', '0.65', 'Auto-Learn Min Similarity (0–1)',           'recognition'),
        ('max_auto_templates',   '8',    'Max Auto-Learned Templates per User',       'recognition'),
        ('snapshot_save',         '1',    'Save Attendance Snapshots',              'recognition'),
        ('unknown_detection',     '1',    'Unknown Face Detection (1=ON, 0=OFF)',   'recognition'),
        ('unknown_save',          '1',    'Save Unknown Face Records',              'recognition'),
        ('unknown_debounce',      '300',  'Unknown Face Cooldown (seconds)',        'recognition'),
        ('unknown_sim_threshold', '0.40', 'Unknown Face Similarity Threshold (0–1)','recognition'),
        ('unknown_retention_days','1',    'Unknown Face Retention (days, 0=keep forever)', 'recognition'),
        ('auto_out',        '1',                         'Auto Punch-OUT at Day End', 'attendance'),
        ('timezone',        'Asia/Kolkata',               'Timezone',                  'general'),
        # ── Compliance ──────────────────────────────────────────────
        ('session_timeout', '30',   'Session Timeout (minutes, 0=never)',           'compliance'),
        ('data_retention',  '1095', 'Attendance Data Retention (days)',             'compliance'),
        ('audit_retention', '1825', 'Audit Log Retention (days)',                   'compliance'),
        ('min_pw_length',   '8',    'Minimum Password Length',                      'compliance'),
        ('require_strong_pw','1',   'Enforce Strong Password (1=yes)',              'compliance'),
        ('max_login_attempts','5',  'Max Failed Login Attempts (0=unlimited)',       'compliance'),
        ('cors_allowed_origins', '', 'CORS Allowed Origins (comma-separated, e.g. http://localhost:5174; restart required)', 'compliance'),
        # ── Cloud Sync ──────────────────────────────────────────────
        ('cloud_api_url',    '',    'Cloud Portal URL (e.g. https://portal.example.com)', 'sync'),
        ('cloud_api_key',    '',    'Cloud API Key (shared secret)',                       'sync'),
        ('site_id',          'site1','Site / Branch Identifier',                           'sync'),
        ('sync_interval',    '30',  'Sync Interval (seconds)',                             'sync'),
        ('last_employee_sync','1970-01-01 00:00:00','Last Employee Pull Timestamp',        'sync'),
        ('sync_enabled',     '0',   'Enable Cloud Sync (1=yes)',                           'sync'),
    ]
    cursor.executemany(
        'INSERT OR IGNORE INTO settings (key, value, label, category) VALUES (?,?,?,?)',
        default_settings
    )

    # Seed default departments
    for dept in ['Administration', 'Finance', 'IT', 'HR', 'Operations', 'Security']:
        cursor.execute('INSERT OR IGNORE INTO departments (name) VALUES (?)', (dept,))

    # ── Schema migrations (safe for existing DBs) ──────────────────
    # ALTER TABLE ADD COLUMN is idempotent: we catch the error if the
    # column already exists so re-running init_db() is always safe.
    migrations = [
        # v2: cloud sync columns
        "ALTER TABLE attendance    ADD COLUMN synced INTEGER DEFAULT 0",
        "ALTER TABLE unknown_faces ADD COLUMN synced INTEGER DEFAULT 0",
        # v3: repeat-visitor tracking for unidentified faces
        "ALTER TABLE unknown_faces ADD COLUMN face_encoding BLOB",
        "ALTER TABLE unknown_faces ADD COLUMN cluster_id TEXT",
        "ALTER TABLE unknown_faces ADD COLUMN punch_type TEXT",
    ]
    for sql in migrations:
        try:
            cursor.execute(sql)
        except Exception:
            pass  # column already exists — ignore

    conn.commit()  # commit migrations before creating indexes that depend on them

    # ── Indexes for 500+ user performance ──────────────────────────
    # These make date-range attendance queries and dashboard aggregates
    # run in milliseconds instead of full-table scans.
    cursor.executescript('''
        CREATE INDEX IF NOT EXISTS idx_att_punch_time
            ON attendance(punch_time);

        CREATE INDEX IF NOT EXISTS idx_att_user_punch
            ON attendance(user_id, punch_time);

        CREATE INDEX IF NOT EXISTS idx_att_date_type
            ON attendance(DATE(punch_time), punch_type);

        CREATE INDEX IF NOT EXISTS idx_users_active_dept
            ON users(active, department);

        CREATE INDEX IF NOT EXISTS idx_users_emp_id
            ON users(emp_id);

        CREATE INDEX IF NOT EXISTS idx_unk_detected
            ON unknown_faces(detected_at, camera_id);

        CREATE INDEX IF NOT EXISTS idx_unk_reviewed
            ON unknown_faces(reviewed);

        CREATE INDEX IF NOT EXISTS idx_att_synced
            ON attendance(synced, id);

        CREATE INDEX IF NOT EXISTS idx_unk_synced
            ON unknown_faces(synced, id);

        CREATE INDEX IF NOT EXISTS idx_unk_cluster
            ON unknown_faces(cluster_id, detected_at);
    ''')

    conn.commit()
    conn.close()
    print("[DB] Database initialised successfully.")


def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    conn.close()
    return row['value'] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)', (key, value))
    conn.commit()
    conn.close()


if __name__ == '__main__':
    init_db()
