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

# Limit CPU thread usage by ONNX Runtime / OpenMP.
# Without these, ONNX spawns one thread per core (20 on this machine)
# per inference call; with 4 cameras running simultaneously that creates
# 80 hot threads and drives load average above 28.
# Stub libGL.so.1 lives in venv/lib — needed by opencv-python on headless servers.
export LD_LIBRARY_PATH="$(dirname "$0")/venv/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
export MKL_NUM_THREADS=4
export NUMEXPR_NUM_THREADS=4
export OMP_WAIT_POLICY=PASSIVE   # threads sleep between tasks instead of spin-waiting

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
  # Camera workers (RTSP threads + ONNX inference) run inside the app process.
  # Multiple gunicorn workers would each start their own camera worker pool,
  # multiplying CPU usage by worker count.  Use 1 worker + many threads instead.
  echo "[*] Using gunicorn (1 worker, 8 threads) — camera-safe mode"
  exec gunicorn \
    --workers 1 \
    --threads 8 \
    --worker-class gthread \
    --bind "0.0.0.0:5443" \
    --certfile cert.pem \
    --keyfile  key.pem \
    --timeout 120 \
    --capture-output \
    --access-logfile - \
    --error-logfile  - \
    "app:create_app()"
else
  echo "[*] gunicorn not found — falling back to waitress/Flask dev server"
  python run.py
fi
