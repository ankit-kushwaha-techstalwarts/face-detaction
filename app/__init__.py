"""
app/__init__.py — Application Factory
======================================
Single entry point for creating the Flask application.
All extensions, blueprints, error handlers, and background services
are wired here so unit tests can call create_app() with a custom config
and get a clean, isolated instance each time.
"""

import logging
import os

from flask import Flask, jsonify
from flask_jwt_extended import JWTManager

log = logging.getLogger('faceattend')


# ── Face-engine stubs (cloud mode or missing library) ─────────────────────────

class _FakeCache:
    def reload(self): pass


class _FakeCameraManager:
    def status(self):        return {}
    def start_all_active(self): pass
    def start_camera(self, *a, **kw): pass
    def stop_camera(self, *a, **kw):  pass


def _stub_gen_mjpeg(_cid):
    return iter([])


# ─────────────────────────────────────────────────────────────────────────────

def create_app(config_class=None):
    """
    Application Factory.

    Parameters
    ----------
    config_class : config.Config subclass (optional)
        Defaults to config.ProductionConfig when not specified.
        Pass config.DevelopmentConfig for local development,
        or a custom class in tests.

    Returns
    -------
    flask.Flask
    """
    if config_class is None:
        from config import ProductionConfig
        config_class = ProductionConfig

    # ── Core app object ───────────────────────────────────────────────────────
    app = Flask(
        __name__,
        static_folder=os.path.join(os.path.dirname(__file__), '..', 'static'),
    )
    app.config.from_object(config_class)

    # ── JWT Configuration ─────────────────────────────────────────────────────
    app.config['JWT_SECRET_KEY'] = app.config.get('SECRET_KEY', 'change-me-in-production')
    app.config['JWT_ACCESS_TOKEN_EXPIRES'] = False
    jwt = JWTManager(app)

    # ── Logging ───────────────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s — %(message)s',
    )

    # ── Ensure required upload directories exist ──────────────────────────────
    for d in (app.config['UPLOAD_DIR'], app.config['LOGO_DIR'], app.config['FACE_DIR']):
        os.makedirs(d, exist_ok=True)

    # ── Attach face-engine components to app (accessible via current_app) ─────
    _attach_face_engine(app)

    # ── Initialise database schema ────────────────────────────────────────────
    _bootstrap_database(app)

    # ── Register middleware (security headers + CORS + auth guard) ────────────
    from app.middleware import register_security_headers, register_cors, register_auth_guard
    register_security_headers(app)
    register_cors(app)
    register_auth_guard(app)

    # ── Register blueprints ───────────────────────────────────────────────────
    from app.routes.auth       import auth_bp
    from app.routes.users      import users_bp
    from app.routes.attendance import attendance_bp
    from app.routes.cameras    import cameras_bp
    from app.routes.system     import system_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(cameras_bp)
    app.register_blueprint(system_bp)

    # ── Global error handlers ─────────────────────────────────────────────────
    _register_error_handlers(app)

    # ── Start background threads (local mode only) ────────────────────────────
    _start_background_services(app)

    # ── Graceful shutdown hook ────────────────────────────────────────────────
    _register_teardown(app)

    log.info(
        "[FaceAttend] App created — mode: %s",
        "CLOUD (PostgreSQL, no cameras)" if app.config['CLOUD_MODE'] else "LOCAL (SQLite + face engine)",
    )
    return app


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _attach_face_engine(app: Flask) -> None:
    """
    Attach face-recognition components to the app object.

    In cloud mode, lightweight stubs replace the real engine so
    all import paths remain identical in route handlers.
    Access in routes via: current_app.face_cache, current_app.camera_manager, …
    """
    if app.config.get('CLOUD_MODE'):
        app.face_cache      = _FakeCache()
        app.camera_manager  = _FakeCameraManager()
        app.gen_mjpeg       = _stub_gen_mjpeg
        app.encode_face     = lambda path: None
        app.encode_face_detailed = lambda path: (None, 0.0, 0.0, 'Face engine unavailable in cloud mode')
        app.encoding_to_blob = lambda enc: None
        app.blob_to_encoding = lambda blob: None
        app.add_face_template    = lambda *a, **k: None
        app.clear_face_templates = lambda *a, **k: 0
        app.get_template_stats   = lambda uid: {}
        app.FACE_DIR         = ''
        app.SNAPSHOT_DIR     = ''
        app.UNKNOWN_DIR      = ''
        return

    try:
        from face_engine import (
            encode_face_from_image, encode_face_detailed,
            encoding_to_blob, blob_to_encoding,
            face_cache, camera_manager, gen_mjpeg,
            add_face_template, clear_face_templates, get_template_stats,
            FACE_DIR, SNAPSHOT_DIR, UNKNOWN_DIR,
        )
        app.face_cache       = face_cache
        app.camera_manager   = camera_manager
        app.gen_mjpeg        = gen_mjpeg
        app.encode_face      = encode_face_from_image
        app.encode_face_detailed = encode_face_detailed
        app.encoding_to_blob = encoding_to_blob
        app.blob_to_encoding = blob_to_encoding
        app.add_face_template    = add_face_template
        app.clear_face_templates = clear_face_templates
        app.get_template_stats   = get_template_stats
        app.FACE_DIR         = FACE_DIR
        app.SNAPSHOT_DIR     = SNAPSHOT_DIR
        app.UNKNOWN_DIR      = UNKNOWN_DIR
        log.info("[FaceEngine] Loaded successfully.")
    except ImportError as exc:
        log.warning("[FaceEngine] Could not import face_engine (%s). Using stubs.", exc)
        app.face_cache       = _FakeCache()
        app.camera_manager   = _FakeCameraManager()
        app.gen_mjpeg        = _stub_gen_mjpeg
        app.encode_face      = lambda path: None
        app.encode_face_detailed = lambda path: (None, 0.0, 0.0, 'Face engine not available')
        app.add_face_template    = lambda *a, **k: None
        app.clear_face_templates = lambda *a, **k: 0
        app.get_template_stats   = lambda uid: {}
        app.encoding_to_blob = lambda enc: None
        app.blob_to_encoding = lambda blob: None
        app.FACE_DIR         = ''
        app.SNAPSHOT_DIR     = ''
        app.UNKNOWN_DIR      = ''


def _bootstrap_database(app: Flask) -> None:
    """Initialise DB schema and seed the default admin account."""
    from app.models import init_db, get_setting
    from app.models.settings import seed_default_admin

    with app.app_context():
        init_db()
        seed_default_admin()
        log.info("[DB] Schema ready.")

    # Honour session_timeout setting
    try:
        timeout_mins = int(get_setting('session_timeout', '30'))
        if timeout_mins > 0:
            from datetime import timedelta
            app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=timeout_mins)
    except Exception:
        pass

    # If CORS is configured for a cross-origin frontend, the session cookie
    # must use SameSite=None (requires Secure, i.e. HTTPS) so the browser
    # will send it on cross-site XHR/fetch requests with credentials.
    if get_setting('cors_allowed_origins', '').strip():
        app.config['SESSION_COOKIE_SAMESITE'] = 'None'


def _start_background_services(app: Flask) -> None:
    """Start camera workers and the cloud-sync agent (local mode only)."""
    if app.config.get('CLOUD_MODE'):
        log.info("[FaceAttend] Cloud mode — cameras and sync agent not started.")
        return

    with app.app_context():
        # Warm face-encoding cache
        try:
            app.face_cache.reload()
            log.info("[FaceCache] Loaded.")
        except Exception as exc:
            log.warning("[FaceCache] Could not load: %s", exc)

        # Start all active RTSP camera workers
        try:
            app.camera_manager.start_all_active()
            log.info("[CameraManager] Active cameras started.")
        except Exception as exc:
            log.warning("[CameraManager] Could not start cameras: %s", exc)

        # Start cloud sync agent if enabled in settings
        try:
            from app.models import get_setting
            if int(get_setting('sync_enabled', '0')):
                from sync_agent import sync_agent
                sync_agent.start()
                app._sync_agent = sync_agent
                log.info("[SyncAgent] Cloud sync agent started.")
        except Exception as exc:
            log.warning("[SyncAgent] Could not start: %s", exc)

        # Start periodic cleanup of old unknown-face records/snapshots
        try:
            _start_unknown_face_cleanup(app)
            log.info("[Cleanup] Unknown-face retention cleanup started.")
        except Exception as exc:
            log.warning("[Cleanup] Could not start unknown-face cleanup: %s", exc)


def _start_unknown_face_cleanup(app: Flask) -> None:
    """
    Background thread: periodically purges unknown_faces rows (and their
    snapshot images) older than the 'unknown_retention_days' setting, so
    continuous camera capture doesn't grow the gallery/disk unbounded.
    """
    import threading
    import time

    from app.models import get_setting
    from app.models.attendance import purge_old_unknown_faces

    CHECK_INTERVAL_SEC = 3600  # hourly check is plenty for day-granularity retention

    def _loop():
        while True:
            try:
                with app.app_context():
                    days = int(get_setting('unknown_retention_days', '1'))
                    if days > 0:
                        deleted = purge_old_unknown_faces(days, app.config['BASE_DIR'])
                        if deleted:
                            log.info(
                                "[Cleanup] Purged %d unknown-face record(s) older than %d day(s).",
                                deleted, days,
                            )
            except Exception as exc:
                log.warning("[Cleanup] Unknown-face purge failed: %s", exc)
            time.sleep(CHECK_INTERVAL_SEC)

    threading.Thread(target=_loop, daemon=True, name='unknown-face-cleanup').start()


def _register_teardown(app: Flask) -> None:
    """Register graceful shutdown: stop camera threads and sync agent."""
    import atexit

    def _shutdown():
        log.info("[FaceAttend] Shutting down background threads…")
        try:
            if hasattr(app, 'camera_manager') and app.camera_manager:
                # Stop every running worker
                for cid in list(getattr(app.camera_manager, '_workers', {}).keys()):
                    app.camera_manager.stop_camera(cid)
        except Exception as exc:
            log.warning("[Shutdown] Camera shutdown error: %s", exc)

        try:
            agent = getattr(app, '_sync_agent', None)
            if agent and hasattr(agent, 'stop'):
                agent.stop()
        except Exception as exc:
            log.warning("[Shutdown] Sync agent shutdown error: %s", exc)

    atexit.register(_shutdown)


def _register_error_handlers(app: Flask) -> None:
    """Uniform JSON error responses — never expose tracebacks to the client."""

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({'status': 'error', 'message': 'Bad request'}), 400

    @app.errorhandler(401)
    def unauthorised(e):
        return jsonify({'status': 'error', 'message': 'Authentication required'}), 401

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({'status': 'error', 'message': 'Access denied'}), 403

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({'status': 'error', 'message': 'Resource not found'}), 404

    @app.errorhandler(413)
    def too_large(e):
        return jsonify({'status': 'error', 'message': 'File too large (max 16 MB)'}), 413

    @app.errorhandler(500)
    def internal_error(e):
        log.exception("[500] Unhandled exception")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
