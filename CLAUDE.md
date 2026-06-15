# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

FaceAttend — a face-recognition attendance system for a Government Department.
Flask backend, SQLite (local) or PostgreSQL (cloud), InsightFace/ONNX Runtime
for face detection & recognition, OpenCV/RTSP for CCTV camera ingestion, and a
single-page frontend (`templates/index.html`).

It runs in one of two modes, selected entirely by environment variables:

- **Local mode** (default): SQLite (`database.py`), face engine + camera workers
  (`face_engine.py`) are active, optional cloud sync agent pushes/pulls data.
- **Cloud mode**: set `CLOUD_MODE=1` and `DATABASE_URL=postgresql://...`. Uses
  PostgreSQL (`database_pg.py`), face engine/cameras are replaced with no-op
  stubs (web portal only — receives sync pushes from local sites).

## CRITICAL: two parallel implementations of the same app

This repo contains **two complete, independently-maintained implementations**
of the entire backend, with the exact same 66 routes in both:

- **`app.py`** — a single ~2000-line monolithic file. Defines `app = Flask(...)`
  at module scope, every `@app.route(...)` inline, its own inline RBAC
  (`login_required`, `role_required`, `_auth_guard`, `ROLE_LEVEL`), and its own
  no-arg `create_app()`.
- **`app/` package** — an Application Factory + Blueprints split: `app/__init__.py`
  (`create_app(config_class=None)`), routes in `app/routes/{auth,users,attendance,cameras,system}.py`,
  data-access in `app/models/{users,attendance,cameras,settings}.py`, and
  shared security/RBAC in `app/middleware.py`.

**Both must be kept in sync.** When adding or changing a route, model, or
business rule, apply the change to **both** `app.py` and the corresponding
`app/routes/*.py` + `app/models/*.py` files — they are not automatically
shared, and a previous commit (`417e4e9`) already updated both in lockstep.

### Which one actually runs depends on how the process is started

`import app` resolves to the **`app/` package** (`app/__init__.py`), because in
Python a package shadows a same-named module file. This means:

- `gunicorn "app:create_app()"` (used by `run.sh` / `run_cloud.sh` when gunicorn
  is available) and `python run.py` → run the **`app/` package** (modular,
  blueprints).
- `python app.py` directly (the `run.sh`/`run_cloud.sh` fallback when gunicorn
  is not installed) → runs **`app.py`** as `__main__`, using its own inline
  `create_app()` and monolithic routes — `app/` is never imported in this case.

When debugging a behavior discrepancy, check **which of these two launch paths
is actually running** before assuming a fix in one file took effect.

## Running the app

```bash
# Local mode (SQLite + face engine + cameras), via the setup script:
./run.sh             # creates venv, installs requirements, generates cert.pem/key.pem,
                      # sets OMP_NUM_THREADS/LD_LIBRARY_PATH, then launches gunicorn
                      # (app/ package) on :5443, or falls back to `python app.py`

# Equivalent direct entry points:
python run.py        # -> app/ package via create_app(), waitress/gunicorn/Flask dev server
python app.py        # -> monolithic app.py, its own create_app()

# Cloud portal mode (PostgreSQL, no cameras/face engine):
export DATABASE_URL="postgresql://user:pw@host:5432/dbname"
export CLOUD_MODE=1
export SECRET_KEY="$(openssl rand -hex 32)"
./run_cloud.sh        # or `python run.py` with FLASK_ENV=production
```

Other relevant env vars: `FACE_MODEL` (`buffalo_l` default vs `buffalo_sc`),
`FACE_DET_SIZE`, `FLASK_ENV` (`development` → `DevelopmentConfig`, allows HTTP;
otherwise `ProductionConfig`, HTTPS-only cookies).

Database schema is created automatically on startup (`init_db()`), or manually:

```bash
python database.py     # SQLite: creates data/attendance.db and seeds settings/departments
```

There is no test suite, linter, or build step configured in this repo.

## Settings system

Almost all tunable behavior (recognition thresholds, attendance rules,
compliance, sync) lives in the `settings` SQL table, seeded by
`database.py`'s `default_settings` list (local) / the PostgreSQL equivalent
(cloud). `get_setting(key, default)` does a **fresh DB read on every call —
there is no caching**, so changes via the Settings UI apply immediately without
a restart. Exceptions that DO require a restart: `face_model` and `det_size`
(read once when `face_engine.get_insight_app()` builds the InsightFace model).

## Face recognition pipeline (`face_engine.py`, local mode only)

- `get_insight_app()` loads the InsightFace model (`face_model`/`det_size`
  settings, or `FACE_MODEL`/`FACE_DET_SIZE` env vars). **`buffalo_l` and
  `buffalo_sc` produce incompatible 512-dim embedding spaces** — changing
  `face_model` requires re-encoding every enrolled user's `face_encoding` and
  any rows in `face_templates`, or matching will silently fail.
- `encode_face_detailed()` applies enrollment quality gates (`ENROLL_MIN_FACE_PX`,
  `ENROLL_MIN_DET_SCORE`, `ENROLL_MIN_SHARPNESS`).
- `FaceCache` holds a single in-memory normalized `(T, 512)` matrix combining
  every active user's `users.face_encoding` plus all `face_templates` rows, for
  one matrix-vector match per frame. Call `face_cache.reload()` after any
  enrollment/template change.
- `face_templates` is a per-user multi-pose gallery (`source` =
  `'enroll'` | `'merge'` | `'auto'`). `_maybe_auto_learn()` automatically adds
  high-confidence live captures (similarity between `auto_learn_threshold` and
  `AUTO_LEARN_MAX_SIM`) subject to quality gates, capped/rotated per user by the
  `max_auto_templates` setting.
- `CameraManager`/`CameraWorker` run one thread per active RTSP camera (OpenCV,
  forced TCP transport), doing detection, recognition, attendance punching, and
  unknown-face clustering/dedup (`_is_duplicate_unknown`, `_assign_unknown_cluster`).
- The User Enrollment "Enroll Face" webcam modal (`templates/index.html`) runs a
  5-step guided pose wizard (`ENROLL_STEPS`/`renderEnrollStep`) to help build a
  good multi-angle gallery for `face_templates`.

## Security / RBAC

Role hierarchy is `guard` (1) < `manager` (2) < `admin` (3), defined as
`ROLE_LEVEL` in both `app.py` and `app/middleware.py`. In the `app/` package,
`app/middleware.py` provides `register_security_headers` (CSP/HSTS/etc.),
`register_auth_guard` (global `/api/` session check + guard-role allowlist
`_GUARD_ALLOWED_PREFIXES`), and the `@login_required` / `@role_required(*roles)`
decorators. `app.py` re-implements the same guard/decorators inline rather than
importing from `app/middleware.py`.

## Cloud sync (`sync_agent.py`)

A daemon thread started from `create_app()` when the `sync_enabled` setting is
`1` (local mode only). Pushes unsynced `attendance`/`unknown_faces` rows to the
cloud portal (`POST /api/sync/...`) and pulls employee records from
`GET /api/sync/employees?since=...`, then calls `face_cache.reload()`.
