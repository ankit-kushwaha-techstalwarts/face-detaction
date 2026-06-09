"""
run.py — Thin WSGI entry point
===============================
Calls create_app() and launches the configured WSGI server.

Local (SQLite + face engine):
  python run.py

Cloud (PostgreSQL, no cameras):
  export DATABASE_URL="postgresql://user:pw@host:5432/dbname"
  export CLOUD_MODE=1
  export SECRET_KEY="$(openssl rand -hex 32)"
  python run.py

Production (gunicorn):
  gunicorn "run:wsgi_app" --bind 0.0.0.0:8000 --workers 4 --threads 2

Production (waitress, Windows):
  python run.py
"""

import os
import logging
import platform

log = logging.getLogger('faceattend')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s',
)

# ── Select config based on environment ────────────────────────────────────────
_env = os.environ.get('FLASK_ENV', 'production').lower()
if _env == 'development':
    from config import DevelopmentConfig as _Cfg
else:
    from config import ProductionConfig as _Cfg

from app import create_app  # noqa: E402

# wsgi_app is the callable gunicorn/waitress will use
wsgi_app = create_app(_Cfg)


# ── Development / direct-run entry point ──────────────────────────────────────
if __name__ == '__main__':
    host   = _Cfg.HOST
    port   = _Cfg.PORT
    scheme = 'http'
    ssl_ctx = None

    # ── Try to load TLS certificates ─────────────────────────────────────────
    if not _Cfg.CLOUD_MODE:
        try:
            from ssl_gen import ensure_certificates
            cert_file, key_file = ensure_certificates()
            ssl_ctx = (cert_file, key_file)
            scheme  = 'https'
            log.info("[SSL] Self-signed certificate loaded.")
            log.info("[SSL] ⚠  First visit: click 'Advanced → Proceed' in your browser.")
            log.info("[SSL]    iOS/macOS users: see INSTALL_CERT.md to trust the cert.")
        except Exception as exc:
            log.warning(f"[SSL] Could not load certificates ({exc}). Falling back to HTTP.")
            port = 5000

    log.info(f"[FaceAttend] Starting on {scheme}://{host}:{port}")
    log.info("[FaceAttend] Also reachable on your LAN — check your IP address.")

    # ── Production WSGI (waitress preferred, gunicorn on Linux/Mac) ───────────
    _is_windows = platform.system() == 'Windows'

    if not _is_windows:
        try:
            import gunicorn  # noqa: F401
            log.info("[FaceAttend] TIP: for full production use, launch via run.sh (gunicorn).")
        except ImportError:
            pass

    try:
        from waitress import serve
        log.info("[FaceAttend] Using Waitress WSGI — production ready.")

        if ssl_ctx:
            import ssl as _ssl
            ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(ssl_ctx[0], ssl_ctx[1])
            serve(wsgi_app, host=host, port=port, _quiet=True,
                  ssl_context=ctx, threads=_Cfg.WSGI_THREADS)
        else:
            serve(wsgi_app, host=host, port=port, _quiet=True,
                  threads=_Cfg.WSGI_THREADS)

    except ImportError:
        log.warning("[FaceAttend] waitress not found — using Flask dev server. "
                    "Run `pip install waitress` for production.")
        wsgi_app.run(host=host, port=port, debug=False,
                     threaded=True, ssl_context=ssl_ctx)
