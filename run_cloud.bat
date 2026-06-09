@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM  FaceAttend — Cloud Portal Startup (Windows Server)
REM  Edit the SET lines below before running.
REM ─────────────────────────────────────────────────────────────────────────

REM ── Set these before running ─────────────────────────────────────────────
SET DATABASE_URL=postgresql://user:password@localhost:5432/faceattend
SET SECRET_KEY=changeme-use-openssl-rand-hex-32
SET SYNC_API_KEY=changeme-use-openssl-rand-hex-32
SET CLOUD_MODE=1
SET PORT=8000

echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo   FaceAttend Cloud Portal (Windows)
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

echo [DB] Initialising PostgreSQL schema...
python database_pg.py

echo [DB] Storing sync API key...
python -c "from database_pg import set_setting; set_setting('sync_api_key', '%SYNC_API_KEY%')"

echo [APP] Starting on http://0.0.0.0:%PORT%
python -c "from waitress import serve; from app import create_app; serve(create_app(), host='0.0.0.0', port=%PORT%, threads=8)"
