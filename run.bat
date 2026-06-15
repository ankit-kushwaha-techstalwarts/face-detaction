@echo off
TITLE FaceAttend — Govt Attendance System
echo ============================================
echo   FaceAttend — Govt Attendance System
echo ============================================

IF NOT EXIST venv (
  echo [*] Creating virtual environment...
  python -m venv venv
)

call venv\Scripts\activate.bat

echo [*] Upgrading pip...
python -m pip install --upgrade pip -q

echo [*] Installing dependencies (includes cryptography for SSL)...
pip install -r requirements.txt

if not exist data mkdir data
if not exist uploads\faces mkdir uploads\faces
if not exist uploads\snapshots mkdir uploads\snapshots
if not exist uploads\unknown mkdir uploads\unknown
if not exist uploads\logo mkdir uploads\logo
if not exist exports mkdir exports

IF NOT EXIST cert.pem (
  echo [*] Generating self-signed SSL certificate...
  python ssl_gen.py
)

echo.
echo [*] Starting HTTPS server at https://localhost:5443
echo     For phone camera access: https://YOUR-PC-IP:5443
echo     First visit: click Advanced then Proceed to trust cert.
echo     iOS users: see INSTALL_CERT.md to install on device.
echo --------------------------------------------

rem ── Use Waitress for production (500+ users, Windows) ────────
python -c "import waitress" 2>NUL
IF %ERRORLEVEL% EQU 0 (
  echo [*] Using Waitress WSGI server -- production mode 16 threads
  python -c "from app import create_app; from waitress import serve; import ssl; ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain('cert.pem','key.pem'); serve(create_app(), host='0.0.0.0', port=5443, threads=16, _quiet=True)"
) ELSE (
  echo [*] Waitress not found -- using Flask dev server
  python run.py
)
pause
