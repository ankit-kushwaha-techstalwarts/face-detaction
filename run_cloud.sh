#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  FaceAttend — Cloud Portal Startup (Linux / macOS)
#  Run on your cloud server (DigitalOcean, AWS EC2, Azure VM, etc.)
#
#  Prerequisites
#  ─────────────────────────────────────────────────────────────────────────────
#  1. PostgreSQL database created and accessible
#  2. psycopg2-binary installed:  pip install psycopg2-binary
#  3. All other deps:             pip install -r requirements.txt
#  4. SSL certificate via Let's Encrypt (recommended) or your CA
#
#  Usage
#  ─────────────────────────────────────────────────────────────────────────────
#  export DATABASE_URL="postgresql://user:password@localhost:5432/faceattend"
#  export SECRET_KEY="$(openssl rand -hex 32)"
#  export SYNC_API_KEY="$(openssl rand -hex 32)"   # set same on local sites
#  bash run_cloud.sh
# ─────────────────────────────────────────────────────────────────────────────

set -e

# ── Mandatory environment variables ──────────────────────────────────────────
if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL is not set."
    echo "  export DATABASE_URL='postgresql://user:password@host:5432/dbname'"
    exit 1
fi

export CLOUD_MODE=1
export SECRET_KEY="${SECRET_KEY:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  FaceAttend Cloud Portal"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  DATABASE : $DATABASE_URL"
echo "  CLOUD    : $CLOUD_MODE"
echo ""

# ── Initialise PostgreSQL schema ──────────────────────────────────────────────
echo "[DB] Initialising PostgreSQL schema…"
python3 -c "from database_pg import init_db; init_db()"
echo "[DB] Schema ready."

# ── Set sync API key in DB if provided via env ────────────────────────────────
if [ -n "$SYNC_API_KEY" ]; then
    python3 -c "
from database_pg import set_setting
set_setting('sync_api_key', '${SYNC_API_KEY}')
print('[DB] Sync API key stored in settings.')
"
fi

# ── Start production server (gunicorn recommended on Linux) ───────────────────
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-4}"

echo ""
echo "[APP] Starting on http://0.0.0.0:${PORT}  (workers=${WORKERS})"
echo "[APP] Reverse-proxy via Nginx with SSL (Let's Encrypt) recommended."
echo ""

if command -v gunicorn &>/dev/null; then
    exec gunicorn "app:create_app()" \
        --bind "0.0.0.0:${PORT}" \
        --workers "${WORKERS}" \
        --threads 2 \
        --worker-class sync \
        --timeout 120 \
        --keep-alive 5 \
        --access-logfile - \
        --error-logfile -
else
    echo "[WARN] gunicorn not found — using waitress (install gunicorn for production)"
    python3 -c "
from waitress import serve
from app import create_app
serve(create_app(), host='0.0.0.0', port=${PORT}, threads=8)
"
fi
