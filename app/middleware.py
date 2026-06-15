"""
app/middleware.py — Security Middleware & RBAC
==============================================
Responsibilities
----------------
1. Inject hardened HTTP security headers on every response.
2. Global authentication guard: protect all /api/ routes.
3. @login_required and @role_required() RBAC decorators used by route handlers.
4. Input sanitisation helpers for route parameters.

OWASP compliance notes
-----------------------
- Content-Security-Policy blocks XSS by whitelisting sources.
- X-Frame-Options: DENY prevents clickjacking.
- X-Content-Type-Options: nosniff stops MIME-sniffing attacks.
- Referrer-Policy limits information leakage in the Referer header.
- Strict-Transport-Security enforces HTTPS for 1 year.
"""

import logging
from functools import wraps

from flask import Flask, request, jsonify, session, redirect

log = logging.getLogger('faceattend.middleware')

# ── Role hierarchy ────────────────────────────────────────────────────────────
# Higher number = more privilege.
ROLE_LEVEL: dict[str, int] = {'guard': 1, 'manager': 2, 'admin': 3}

# Paths that bypass authentication entirely (login page + login API)
_PUBLIC_PATHS: frozenset = frozenset({
    '/api/auth/login',
    '/api/settings',   # GET — needed for branding on login page
    '/login',
    '/static',
    '/uploads',
    '/cert.pem',
    '/favicon.ico',
})

# Paths that guards (lowest privilege) may access via GET
_GUARD_ALLOWED_PREFIXES: tuple = (
    '/api/dashboard',
    '/api/cameras',
    '/api/attendance/today-summary',
    '/api/settings',
    '/api/departments',
    '/api/holidays',
    '/video_feed/',
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Security Headers
# ─────────────────────────────────────────────────────────────────────────────

def register_security_headers(app: Flask) -> None:
    """Attach an after_request hook that injects hardened HTTP headers."""

    @app.after_request
    def _add_security_headers(response):
        # Content-Security-Policy — only allow resources from self and data URIs.
        # blob: is required for the MJPEG camera streams.
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "   # inline JS in single-page template
            "style-src  'self' 'unsafe-inline'; "   # inline CSS
            "img-src    'self' data: blob:; "
            "media-src  'self' blob:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        response.headers['X-Frame-Options']           = 'DENY'
        response.headers['X-Content-Type-Options']    = 'nosniff'
        response.headers['Referrer-Policy']           = 'strict-origin-when-cross-origin'
        response.headers['X-XSS-Protection']         = '1; mode=block'
        response.headers['Permissions-Policy']        = (
            'camera=(self), microphone=(), geolocation=()'
        )
        # Only send HSTS when TLS is confirmed (avoids stripping on plain HTTP dev)
        if request.is_secure:
            response.headers['Strict-Transport-Security'] = (
                'max-age=31536000; includeSubDomains; preload'
            )
        # Remove server banner to avoid fingerprinting
        response.headers.pop('Server', None)
        return response


# ─────────────────────────────────────────────────────────────────────────────
# 2. Authentication Guard (global before_request)
# ─────────────────────────────────────────────────────────────────────────────

def register_auth_guard(app: Flask) -> None:
    """
    Protect every /api/ route.
    - Public paths bypass the check.
    - Guard role is restricted to a small allowlist of GET endpoints.
    - All other roles pass freely once authenticated.
    """

    @app.before_request
    def _auth_guard():
        path = request.path

        # ── Static assets and public paths are always open ────────────────────
        if not path.startswith('/api/') and path not in ('/login',):
            return None   # let Flask serve the route normally

        # ── Exact public paths ────────────────────────────────────────────────
        if path in _PUBLIC_PATHS:
            return None

        # ── Prefix-match public paths (/static/, /uploads/, /cert.pem) ───────
        if any(path.startswith(p) for p in ('/static/', '/uploads/', '/cert.pem')):
            return None

        # ── All remaining /api/ routes require a valid session ────────────────
        if not session.get('logged_in'):
            return jsonify({'status': 'error', 'message': 'Not authenticated'}), 401

        # ── Guard role enforcement ────────────────────────────────────────────
        role = session.get('role', 'guard')
        if role == 'guard':
            allowed = (
                request.method == 'GET'
                and any(path.startswith(p) for p in _GUARD_ALLOWED_PREFIXES)
            ) or path.startswith('/video_feed/')
            if not allowed:
                return jsonify({'status': 'error',
                                'message': 'Access denied for your role'}), 403

        return None   # all checks passed


# ─────────────────────────────────────────────────────────────────────────────
# 3. RBAC Decorators
# ─────────────────────────────────────────────────────────────────────────────

def login_required(f):
    """
    Decorator: redirect unauthenticated browser requests to /login;
    return 401 JSON for API requests.
    """
    @wraps(f)
    def _decorated(*args, **kwargs):
        if not session.get('logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': 'Not authenticated'}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return _decorated


def role_required(*roles: str):
    """
    Decorator factory: restrict an endpoint to one or more named roles.

    Usage
    -----
    @role_required('admin')
    @role_required('admin', 'manager')

    The decorator also implies @login_required — no need to stack both.
    """
    def _decorator(f):
        @wraps(f)
        def _decorated(*args, **kwargs):
            if not session.get('logged_in'):
                return jsonify({'status': 'error', 'message': 'Not authenticated'}), 401
            user_role = session.get('role', 'guard')
            if user_role not in roles:
                log.warning(
                    "[RBAC] %s (role=%s) attempted %s — required: %s",
                    session.get('username'), user_role, request.path, roles,
                )
                return jsonify({
                    'status': 'error',
                    'message': f'Access denied. Required role: {" or ".join(roles)}',
                }), 403
            return f(*args, **kwargs)
        return _decorated
    return _decorator


# ─────────────────────────────────────────────────────────────────────────────
# 4. Input Sanitisation Helpers
# ─────────────────────────────────────────────────────────────────────────────

def safe_int(value, default: int = 1, min_val: int = 1, max_val: int = 10_000) -> int:
    """Parse and clamp an integer query parameter."""
    try:
        return max(min_val, min(max_val, int(value)))
    except (TypeError, ValueError):
        return default


def safe_page(page_str, per_page_str, max_per_page: int = 100):
    """Return clamped (page, per_page) tuple from query-string strings."""
    page     = safe_int(page_str,     default=1,  min_val=1)
    per_page = safe_int(per_page_str, default=50, min_val=10, max_val=max_per_page)
    return page, per_page


def sanitise_string(value: str, max_len: int = 255) -> str:
    """Strip whitespace and enforce a maximum length."""
    return (value or '').strip()[:max_len]
