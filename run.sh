#!/bin/bash
# FaceAttend — macOS / Linux launcher
# Uses InsightFace + ONNX Runtime (no dlib, no C++ compilation)

echo "============================================"
echo "  FaceAttend — Govt Attendance System"
echo "============================================"

# ── Create venv if needed ─────────────────────────────────────
if [ ! -d "venv" ]; then
  echo "[*] Creating virtual environment…"
  python3 -m venv venv
fi

source venv/bin/activate

# ── Upgrade pip ───────────────────────────────────────────────
pip install --upgrade pip -q

# ── Install all dependencies (includes cryptography for SSL) ──
echo "[*] Installing dependencies…"
pip install -r requirements.txt

# ── Create directories ────────────────────────────────────────
mkdir -p data uploads/faces uploads/snapshots uploads/unknown uploads/logo exports

# ── Generate SSL certificate (first run only) ─────────────────
if [ ! -f "cert.pem" ] || [ ! -f "key.pem" ]; then
  echo "[*] Generating self-signed SSL certificate…"
  python ssl_gen.py
fi

echo ""
echo "[*] Starting HTTPS server → https://localhost:5443"
echo "    For iPhone/iPad camera: visit https://<your-IP>:5443"
echo "    First visit: click 'Advanced → Proceed' to trust the cert."
echo "    iOS users: see INSTALL_CERT.md to trust on device."
echo "--------------------------------------------"

# ── Production server selection ───────────────────────────────
# Use gunicorn with 4 workers for 500+ user deployments.
# gunicorn does NOT support SSL directly with Flask; we terminate SSL
# at gunicorn level using --certfile/--keyfile flags.
if python -c "import gunicorn" 2>/dev/null; then
  WORKERS=${GUNICORN_WORKERS:-4}
  echo "[*] Using gunicorn ($WORKERS workers) — production mode"
  exec gunicorn \
    --workers "$WORKERS" \
    --threads 4 \
    --worker-class gthread \
    --bind "0.0.0.0:5443" \
    --certfile cert.pem \
    --keyfile  key.pem \
    --timeout 120 \
    --access-logfile - \
    --error-logfile  - \
    "app:create_app()"
else
  echo "[*] gunicorn not found — falling back to waitress/Flask dev server"
  python app.py
fi
