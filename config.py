"""
config.py — Centralised Configuration Manager
==============================================
Handles SQLite/PostgreSQL switching, environment detection,
security keys, and all tunable constants.

Usage
-----
  from config import Config          # production default
  from config import DevelopmentConfig   # local dev (HTTP allowed)
"""

import os
import secrets
from datetime import timedelta


class Config:
    # ── Security ───────────────────────────────────────────────────────────
    # Generate a cryptographically strong key if not set.
    # In production ALWAYS set SECRET_KEY via environment variable.
    SECRET_KEY: str = os.environ.get('SECRET_KEY', secrets.token_hex(32))

    SESSION_COOKIE_SECURE:   bool = True   # HTTPS only
    SESSION_COOKIE_HTTPONLY: bool = True   # No JS access to cookie
    SESSION_COOKIE_SAMESITE: str  = 'Lax' # CSRF protection
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)

    # Upload size guard — 64 MB (multi-photo enrollment uploads)
    MAX_CONTENT_LENGTH: int = 64 * 1024 * 1024

    # ── Database ───────────────────────────────────────────────────────────
    # If both DATABASE_URL and CLOUD_MODE are set, the app uses PostgreSQL
    # and disables the face engine / camera subsystem.
    DATABASE_URL: str = os.environ.get('DATABASE_URL', '')
    CLOUD_MODE:   bool = bool(os.environ.get('CLOUD_MODE', ''))

    # ── Filesystem paths ───────────────────────────────────────────────────
    # BASE_DIR is the face_attendance/ package root (one level above app/)
    BASE_DIR:   str = os.path.dirname(os.path.abspath(__file__))
    UPLOAD_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    LOGO_DIR:   str = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', 'logo')
    FACE_DIR:   str = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', 'faces')

    # ── Allowed upload extensions ──────────────────────────────────────────
    ALLOWED_IMAGE_EXTENSIONS: frozenset = frozenset({'png', 'jpg', 'jpeg', 'webp', 'ico'})

    # ── WSGI / SSL ─────────────────────────────────────────────────────────
    HOST:       str = '0.0.0.0'
    PORT:       int = int(os.environ.get('PORT', 5443))
    WSGI_THREADS: int = 16


class DevelopmentConfig(Config):
    """Local development — relaxes HTTPS requirement so plain HTTP works."""
    SESSION_COOKIE_SECURE: bool = False
    DEBUG: bool = True
    PORT:  int = 5000


class ProductionConfig(Config):
    """Production hardened defaults."""
    DEBUG: bool = False
