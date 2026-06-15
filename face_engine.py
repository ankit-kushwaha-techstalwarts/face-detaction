"""
Face Recognition Attendance System - Face Engine
Uses InsightFace + ONNX Runtime (no dlib, no C++ compilation).
pip install insightface onnxruntime opencv-python
"""

import os
import cv2
import numpy as np
import pickle
import threading
import time
import uuid
import logging
from datetime import datetime

# InsightFace — pure pip install, no compilation needed
try:
    from insightface.app import FaceAnalysis
    FACE_LIB_AVAILABLE = True
except ImportError:
    FACE_LIB_AVAILABLE = False
    logging.warning("insightface not installed. Run: pip install insightface onnxruntime")

from database import get_db, get_setting

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

# ── Force RTSP over TCP globally (fixes "453 Not Enough Bandwidth") ─────────
# Many IP cameras send this error when the client negotiates UDP transport and
# the camera cannot sustain the required bitrate over UDP.  Forcing TCP makes
# the camera stream over a reliable connection with no bandwidth negotiation.
os.environ.setdefault(
    'OPENCV_FFMPEG_CAPTURE_OPTIONS',
    'rtsp_transport;tcp|buffer_size;2097152|max_delay;500000|fflags;nobuffer|flags;low_delay'
)

# OpenCV's internal parallel_for_ thread pool defaults to one thread per CPU
# core and is used by every cv2.resize/cvtColor/etc. call. Each CameraWorker
# already runs on its own thread, so that per-call fan-out is redundant nested
# parallelism: with multiple cameras processing frames concurrently, each one
# repeatedly spins up a 20-thread pool, saturating every core. Cap it to 1.
cv2.setNumThreads(1)

BASE_DIR     = os.path.dirname(__file__)
SNAPSHOT_DIR = os.path.join(BASE_DIR, 'uploads', 'snapshots')
UNKNOWN_DIR  = os.path.join(BASE_DIR, 'uploads', 'unknown')
FACE_DIR     = os.path.join(BASE_DIR, 'uploads', 'faces')

for _d in (SNAPSHOT_DIR, UNKNOWN_DIR, FACE_DIR):
    os.makedirs(_d, exist_ok=True)


# ─────────────────────────────────────────────────────────────
#  InsightFace app (singleton, lazy-loaded)
# ─────────────────────────────────────────────────────────────

_insight_app = None
_insight_lock = threading.Lock()


def get_insight_app():
    global _insight_app
    if _insight_app is None:
        with _insight_lock:
            if _insight_app is None:
                # Limit ONNX Runtime / OpenMP thread pools before loading the model.
                # Default is one thread per CPU core; on a 20-core machine with 4 cameras
                # running simultaneous inference that creates 80 hot threads (load > 28).
                # These env vars must be set before onnxruntime initialises its thread pool.
                import os as _os
                for _k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
                           'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
                    _os.environ.setdefault(_k, '4')
                _os.environ.setdefault('OMP_WAIT_POLICY', 'PASSIVE')

                # Model pack is selectable: buffalo_sc (~100 MB, light) or
                # buffalo_l (~300 MB, SCRFD-10G detector + ResNet50 ArcFace —
                # noticeably better on small/angled/dim faces, ~3x CPU cost).
                # Requires app restart to take effect.
                model_name = (_os.environ.get('FACE_MODEL')
                              or get_setting('face_model', 'buffalo_l')).strip()
                det_size = int(_os.environ.get('FACE_DET_SIZE')
                               or get_setting('det_size', '640'))

                log.info(f"[InsightFace] Loading model '{model_name}' "
                         f"det_size={det_size} (first run downloads weights)…")

                # insightface's model_zoo.get_model() only forwards 'providers'/
                # 'provider_options' to onnxruntime.InferenceSession, so a
                # sess_options kwarg can't reach it through FaceAnalysis(...).
                # Temporarily wrap InferenceSession.__init__ to inject a capped
                # thread pool for the detection + recognition sessions it
                # creates — without this each session defaults to one intra-op
                # thread per CPU core, and with several cameras running
                # inference concurrently that's a second source (alongside
                # cv2's pool above) of all-core saturation.
                import onnxruntime as _ort
                _orig_ort_init = _ort.InferenceSession.__init__

                def _capped_ort_init(self, *a, **kw):
                    if kw.get('sess_options') is None:
                        so = _ort.SessionOptions()
                        so.intra_op_num_threads = 2
                        so.inter_op_num_threads = 1
                        kw['sess_options'] = so
                    return _orig_ort_init(self, *a, **kw)

                _ort.InferenceSession.__init__ = _capped_ort_init
                try:
                    app = FaceAnalysis(
                        name=model_name,
                        allowed_modules=['detection', 'recognition'],
                        providers=['CPUExecutionProvider']
                    )
                    # det_thresh below the 0.5 library default: keep recall high
                    # here, then filter weak hits per-frame with the runtime
                    # 'det_score_thresh' setting (adjustable without restart).
                    app.prepare(ctx_id=0, det_size=(det_size, det_size), det_thresh=0.40)
                finally:
                    _ort.InferenceSession.__init__ = _orig_ort_init

                _insight_app = app
                log.info("[InsightFace] Model ready.")
    return _insight_app


# ─────────────────────────────────────────────────────────────
#  Encoding helpers
# ─────────────────────────────────────────────────────────────

# Enrolment quality gates — stricter than live matching because one bad
# reference photo poisons every future match for that user.
ENROLL_MIN_FACE_PX   = 80    # min side of face bbox in the enrolment photo
ENROLL_MIN_DET_SCORE = 0.60  # detector confidence required at enrolment
ENROLL_MIN_SHARPNESS = 45.0  # Laplacian variance of the face crop


def _face_sharpness(img_bgr: np.ndarray, bbox) -> float:
    """Laplacian variance of the face crop — low values mean motion blur
    or out-of-focus, which produce unreliable embeddings."""
    x1, y1, x2, y2 = [int(v) for v in bbox]
    h, w = img_bgr.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return 0.0
    gray = cv2.cvtColor(img_bgr[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def encode_face_detailed(image_path: str):
    """
    Return (embedding, None) on success or (None, reason) on failure.
    Applies quality gates so weak reference photos are rejected with a
    human-readable explanation instead of silently degrading recognition.
    """
    if not FACE_LIB_AVAILABLE:
        return None, 'Face library not installed on this server'
    img = cv2.imread(image_path)
    if img is None:
        log.error(f"Cannot read image: {image_path}")
        return None, 'Image file could not be read'
    try:
        app   = get_insight_app()
        faces = app.get(img)
    except Exception as e:
        log.error(f"InsightFace error: {e}")
        return None, 'Face engine error — check server logs'
    if not faces:
        return None, 'No face detected — use a clear, well-lit frontal photo'

    # Pick the largest face if multiple detected
    face = max(faces, key=lambda f: (f.bbox[2]-f.bbox[0]) * (f.bbox[3]-f.bbox[1]))

    side = min(face.bbox[2] - face.bbox[0], face.bbox[3] - face.bbox[1])
    if side < ENROLL_MIN_FACE_PX:
        return None, (f'Face too small ({int(side)}px) — move closer to the '
                      f'camera or use a higher-resolution photo')
    score = float(getattr(face, 'det_score', 1.0))
    if score < ENROLL_MIN_DET_SCORE:
        return None, ('Face unclear (low detection confidence) — use a '
                      'frontal photo without occlusion')
    sharpness = _face_sharpness(img, face.bbox)
    if sharpness < ENROLL_MIN_SHARPNESS:
        return None, 'Photo is too blurry — retake without motion blur'

    return face.embedding.astype(np.float32), None


def encode_face_from_image(image_path: str):
    """Return 512-d face embedding from image file, or None if no usable face."""
    enc, reason = encode_face_detailed(image_path)
    if enc is None and reason:
        log.info(f"Enrolment rejected for {image_path}: {reason}")
    return enc


def _boost_low_light(frame_bgr: np.ndarray) -> np.ndarray:
    """Apply CLAHE on luma when the frame is genuinely dark (mean Y < 60).
    Recovers faces at dim entrances / night IR scenes that the detector
    otherwise misses. No-op on normally lit frames."""
    ycrcb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2YCrCb)
    y = ycrcb[:, :, 0]
    if y.mean() >= 60:
        return frame_bgr
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    ycrcb[:, :, 0] = clahe.apply(y)
    return cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)


def get_faces_from_frame(frame_bgr: np.ndarray):
    """Return list of (bbox, embedding, det_score) from a BGR OpenCV frame.

    Detections are filtered by the runtime settings 'det_score_thresh'
    (weak/partial detections produce garbage embeddings that cause false
    matches) and 'min_face_size' (tiny distant faces are unreliable).
    """
    if not FACE_LIB_AVAILABLE:
        return []
    try:
        if int(get_setting('low_light_boost', '1')):
            frame_bgr = _boost_low_light(frame_bgr)
        app   = get_insight_app()
        faces = app.get(frame_bgr)
    except Exception as e:
        log.error(f"Frame analysis error: {e}")
        return []

    min_score = float(get_setting('det_score_thresh', '0.50'))
    min_size  = int(get_setting('min_face_size', '40'))

    results = []
    for f in faces:
        score = float(getattr(f, 'det_score', 1.0))
        if score < min_score:
            continue
        if min(f.bbox[2] - f.bbox[0], f.bbox[3] - f.bbox[1]) < min_size:
            continue
        results.append((f.bbox.astype(int), f.embedding.astype(np.float32), score))
    return results


def encoding_to_blob(enc: np.ndarray) -> bytes:
    return pickle.dumps(enc)


def blob_to_encoding(blob: bytes) -> np.ndarray:
    return pickle.loads(blob)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity in [0, 1]. Higher = more similar."""
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ─────────────────────────────────────────────────────────────
#  Known-faces cache (in-memory)
# ─────────────────────────────────────────────────────────────

class FaceCache:
    """
    Thread-safe in-memory cache of enrolled face embeddings.

    Each user can have MULTIPLE templates: the anchor embedding in
    users.face_encoding (synced with the cloud portal) plus any rows in
    face_templates (extra enrolment angles, operator-assigned snapshots,
    auto-learned captures). All templates become rows of one pre-normalised
    (T, 512) matrix; a match is the best similarity across every template,
    mapped back to its user. 500 users x ~6 templates is still a single
    ~3000x512 matrix-vector multiply (~1 ms).
    """

    def __init__(self):
        self._lock     = threading.Lock()
        self._user_ids = []   # row -> user_id (users repeat, one row per template)
        self._names    = []   # row -> user name
        # Pre-normalised (T, 512) float32 matrix — rebuilt on every reload()
        self._matrix   = np.empty((0, 512), dtype=np.float32)

    def reload(self):
        conn = get_db()
        rows = conn.execute(
            'SELECT id AS user_id, name, face_encoding AS embedding '
            'FROM users WHERE active=1 AND face_encoding IS NOT NULL'
        ).fetchall()
        try:
            template_rows = conn.execute(
                'SELECT ft.user_id AS user_id, u.name AS name, ft.embedding AS embedding '
                'FROM face_templates ft JOIN users u ON u.id = ft.user_id '
                'WHERE u.active = 1'
            ).fetchall()
        except Exception as e:
            log.warning(f"[FaceCache] face_templates unavailable ({e}) — anchor encodings only.")
            template_rows = []
        conn.close()

        ids, names, encs = [], [], []
        for row in list(rows) + list(template_rows):
            try:
                enc  = blob_to_encoding(row['embedding'])
                norm = np.linalg.norm(enc)
                if norm == 0:
                    continue
                encs.append(enc / norm)          # pre-normalise once at load time
                ids.append(row['user_id'])
                names.append(row['name'])
            except Exception as e:
                log.warning(f"Bad encoding for user {row['user_id']}: {e}")

        matrix = (np.array(encs, dtype=np.float32)
                  if encs else np.empty((0, 512), dtype=np.float32))
        with self._lock:
            self._user_ids = ids
            self._names    = names
            self._matrix   = matrix
        log.info(f"[FaceCache] Loaded {len(ids)} template(s) for "
                 f"{len(set(ids))} user(s) into (T={len(ids)}, 512) matrix.")

    def match(self, unknown_enc: np.ndarray, threshold: float = 0.50):
        """
        Return (user_id, name, confidence_pct) or (None, 'Unknown', 0).
        Single matrix-vector multiply — O(1) regardless of enrolled user count.
        """
        with self._lock:
            if self._matrix.shape[0] == 0:
                return None, 'Unknown', 0.0
            norm = np.linalg.norm(unknown_enc)
            if norm == 0:
                return None, 'Unknown', 0.0
            q          = unknown_enc.astype(np.float32) / norm  # normalise query
            sims       = self._matrix @ q                       # (N,) — all cosine sims at once
            best_idx   = int(np.argmax(sims))
            best_sim   = float(sims[best_idx])
            confidence = round(best_sim * 100, 2)
            if best_sim >= threshold:
                return self._user_ids[best_idx], self._names[best_idx], confidence
            return None, 'Unknown', confidence


face_cache = FaceCache()


# ─────────────────────────────────────────────────────────────
#  Face template gallery helpers
# ─────────────────────────────────────────────────────────────

def add_face_template(user_id: int, enc: np.ndarray, source: str = 'enroll',
                      camera_id: int = None, reload_cache: bool = True):
    """Insert one embedding into the user's template gallery."""
    conn = get_db()
    conn.execute(
        'INSERT INTO face_templates (user_id, embedding, source, camera_id) VALUES (?,?,?,?)',
        (user_id, encoding_to_blob(enc.astype(np.float32)), source, camera_id)
    )
    conn.commit()
    conn.close()
    if reload_cache:
        face_cache.reload()


def clear_face_templates(user_id: int, source: str = None) -> int:
    """Delete a user's templates (optionally only one source). Returns count."""
    conn = get_db()
    if source:
        cur = conn.execute('DELETE FROM face_templates WHERE user_id=? AND source=?',
                           (user_id, source))
    else:
        cur = conn.execute('DELETE FROM face_templates WHERE user_id=?', (user_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted:
        face_cache.reload()
    return deleted


def get_template_stats(user_id: int) -> dict:
    """Per-source template counts for a user, e.g. {'enroll': 2, 'auto': 3}."""
    conn = get_db()
    rows = conn.execute(
        'SELECT source, COUNT(*) AS n FROM face_templates WHERE user_id=? GROUP BY source',
        (user_id,)
    ).fetchall()
    conn.close()
    return {row['source']: row['n'] for row in rows}


# ─────────────────────────────────────────────────────────────
#  Snapshot & drawing helpers
# ─────────────────────────────────────────────────────────────

def save_snapshot(frame: np.ndarray, prefix: str, directory: str) -> str:
    ts   = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    name = f"{prefix}_{ts}.jpg"
    path = os.path.join(directory, name)
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return os.path.relpath(path, BASE_DIR)


def draw_boxes(frame, detections):
    """detections: list of (bbox, label, is_known)"""
    for bbox, label, is_known in detections:
        x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        color = (0, 200, 0) if is_known else (0, 0, 220)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.rectangle(frame, (x1, y2 - 22), (x2, y2), color, cv2.FILLED)
        cv2.putText(frame, label, (x1 + 4, y2 - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1)
    return frame


# ─────────────────────────────────────────────────────────────
#  Camera stream worker
# ─────────────────────────────────────────────────────────────

class CameraWorker(threading.Thread):
    """Reads RTSP stream, runs face recognition, logs attendance."""

    DEBOUNCE_SEC         = 60    # seconds between re-punching same known person
    UNKNOWN_COOLDOWN_SEC = 300   # seconds before same unknown face saved again
    UNKNOWN_SIM_THRESH   = 0.40  # cosine similarity to consider "same unknown person"
                                  # InsightFace ArcFace: same person ~ 0.5-0.95, diff person ~ 0.0-0.3
    HIT_WINDOW_SEC       = 5.0   # consecutive-match streak resets after this gap
    AUTO_LEARN_GAP_SEC   = 1800  # min seconds between auto-learned templates per user
    AUTO_LEARN_MAX_SIM   = 0.88  # above this the gallery already covers this look —
                                  # learning near-duplicates adds nothing
    AUTO_LEARN_MIN_FACE  = 60    # px — live captures may be smaller than enrol photos
    AUTO_LEARN_MIN_SCORE = 0.60  # detector confidence gate for learning

    def __init__(self, camera_id: int, rtsp_url: str, direction: str = 'BOTH'):
        super().__init__(daemon=True)
        self.camera_id   = camera_id
        self.rtsp_url    = rtsp_url
        self.direction   = direction
        self.running     = False
        self._frame      = None
        self._frame_lock = threading.Lock()
        self._connected  = False   # True once first real frame received
        self._last_seen  = {}   # user_id  -> epoch of last punch (known faces)
        self._hit_streak = {}   # user_id  -> {'count': int, 'last': epoch} match streaks
        self._last_learn = {}   # user_id  -> epoch of last auto-learned template
        # Rolling buffer of recent unknown face embeddings:
        # list of {'enc': np.ndarray, 'ts': float}
        self._unknown_buf = []

    def has_frames(self) -> bool:
        """True once at least one real frame has been decoded from the stream."""
        return self._connected and self._frame is not None

    def stop(self):
        self.running = False

    def get_latest_frame(self):
        with self._frame_lock:
            return self._frame.copy() if self._frame is not None else None

    def run(self):
        self.running = True
        frame_skip   = int(get_setting('frame_skip', '5'))

        log.info(f"[Camera {self.camera_id}] Connecting → {self.rtsp_url}")
        cap = self._open_capture(self.rtsp_url)
        if cap is None or not cap.isOpened():
            log.error(f"[Camera {self.camera_id}] Cannot open stream — all transports failed.")
            self._update_ping(False)
            return

        frame_count = 0
        self._update_ping(True)

        while self.running:
            ret, frame = cap.read()
            if not ret:
                log.warning(f"[Camera {self.camera_id}] Read fail — reconnecting in 5 s…")
                cap.release()
                self._connected = False
                time.sleep(5)
                cap = self._open_capture(self.rtsp_url)
                if cap is None:
                    break
                continue

            # Mark as connected (frames flowing) after first successful read
            if not self._connected:
                self._connected = True
                log.info(f"[Camera {self.camera_id}] Frames flowing — connected.")

            with self._frame_lock:
                self._frame = frame.copy()

            frame_count += 1
            if frame_count % frame_skip != 0:
                continue

            if not FACE_LIB_AVAILABLE:
                continue

            # Re-read tunables on every detection cycle so Settings changes
            # apply to already-running cameras without an app restart.
            tol        = float(get_setting('recognition_tol', '0.50'))
            frame_skip = int(get_setting('frame_skip',        '5'))
            save_snap  = int(get_setting('snapshot_save',     '1'))
            unk_detect = int(get_setting('unknown_detection', '1'))  # master ON/OFF
            save_unk   = int(get_setting('unknown_save',      '1'))

            faces      = get_faces_from_frame(frame)
            detections = []

            for bbox, enc, det_score in faces:
                user_id, name, confidence = face_cache.match(enc, tol)
                if user_id:
                    detections.append((bbox, f"{name} {confidence:.0f}%", True))
                    # Require N matches in a short window before punching so a
                    # single-frame false match never logs attendance.
                    if self._confirm_hit(user_id):
                        # Known face: debounce prevents re-punch AND duplicate snapshot
                        self._log_attendance(user_id, confidence, frame if save_snap else None)
                    self._maybe_auto_learn(user_id, enc, confidence / 100.0,
                                           frame, bbox, det_score)
                else:
                    detections.append((bbox, 'Unknown', False))
                    # Only process unknowns if detection is enabled globally
                    if unk_detect and save_unk:
                        if not self._is_duplicate_unknown(enc):
                            snap = save_snapshot(frame, f'unk_cam{self.camera_id}', UNKNOWN_DIR)
                            self._log_unknown(snap, enc)
                            log.info(f"[Camera {self.camera_id}] New unknown face saved.")

            annotated = draw_boxes(frame.copy(), detections)
            with self._frame_lock:
                self._frame = annotated

        cap.release()
        log.info(f"[Camera {self.camera_id}] Stream stopped.")

    # ── helpers ──────────────────────────────────────────────

    def _open_capture(self, url: str):
        """
        Open an RTSP (or webcam / HTTP) stream.

        TCP transport is already forced globally via the OPENCV_FFMPEG_CAPTURE_OPTIONS
        environment variable set at module load time, so a single VideoCapture call is
        all that is needed.  Opening multiple captures simultaneously (the old 3-strategy
        approach) exhausted connection slots on cameras that allow only 1-2 concurrent
        RTSP clients, which was the primary reason the feed never appeared in the UI.
        """
        try:
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if cap.isOpened():
                log.info(f"[Camera {self.camera_id}] Stream opened (TCP via env var).")
                return cap
            cap.release()
            log.error(f"[Camera {self.camera_id}] VideoCapture.isOpened() returned False.")
        except Exception as e:
            log.error(f"[Camera {self.camera_id}] Capture open error: {e}")
        return None

    def _confirm_hit(self, user_id: int) -> bool:
        """
        True once the same user has matched in 'match_consecutive' detection
        cycles within HIT_WINDOW_SEC of each other. A streak that goes stale
        restarts at 1, so a one-off false match on a passer-by never punches.
        """
        need = int(get_setting('match_consecutive', '2'))
        if need <= 1:
            return True
        now    = time.time()
        streak = self._hit_streak.get(user_id)
        if streak is None or now - streak['last'] > self.HIT_WINDOW_SEC:
            streak = {'count': 0, 'last': now}
        streak['count'] += 1
        streak['last']   = now
        self._hit_streak[user_id] = streak
        if streak['count'] >= need:
            self._hit_streak.pop(user_id, None)   # reset so next punch needs a fresh streak
            return True
        return False

    def _maybe_auto_learn(self, user_id: int, enc: np.ndarray, sim: float,
                          frame: np.ndarray, bbox, det_score: float):
        """
        Template adaptation: add this live embedding to the user's gallery so
        future matches see their real-world appearance (camera angle, lighting,
        beard, glasses) instead of only the enrolment photo.

        Poisoning safety: learns ONLY from matches well above the punch
        threshold ('auto_learn_threshold', default 0.65 vs 0.50), with quality
        gates on the capture, capped per user, never touching the anchor
        encoding. sim must also be BELOW AUTO_LEARN_MAX_SIM — a near-duplicate
        of an existing template teaches nothing.
        """
        try:
            if not int(get_setting('auto_learn', '1')):
                return
            learn_thr = float(get_setting('auto_learn_threshold', '0.65'))
            if not (learn_thr <= sim <= self.AUTO_LEARN_MAX_SIM):
                return
            now = time.time()
            if now - self._last_learn.get(user_id, 0) < self.AUTO_LEARN_GAP_SEC:
                return
            # Capture quality gates — a blurry/small face makes a bad template
            if det_score < self.AUTO_LEARN_MIN_SCORE:
                return
            if min(bbox[2] - bbox[0], bbox[3] - bbox[1]) < self.AUTO_LEARN_MIN_FACE:
                return
            if _face_sharpness(frame, bbox) < ENROLL_MIN_SHARPNESS:
                return

            cap = int(get_setting('max_auto_templates', '5'))
            conn = get_db()
            # Rolling window: evict oldest auto-learned templates to stay at cap.
            # 'enroll'/'merge' templates are operator-provided and never evicted.
            conn.execute('''
                DELETE FROM face_templates WHERE id IN (
                    SELECT id FROM face_templates
                    WHERE user_id=? AND source='auto'
                    ORDER BY id DESC LIMIT -1 OFFSET ?
                )
            ''', (user_id, max(cap - 1, 0)))
            conn.execute(
                'INSERT INTO face_templates (user_id, embedding, source, camera_id) VALUES (?,?,?,?)',
                (user_id, encoding_to_blob(enc.astype(np.float32)), 'auto', self.camera_id)
            )
            conn.commit()
            conn.close()
            self._last_learn[user_id] = now
            face_cache.reload()
            log.info(f"[Camera {self.camera_id}] Auto-learned template for "
                     f"user_id={user_id} (sim={sim:.2f}, det={det_score:.2f}).")
        except Exception as e:
            log.warning(f"[Camera {self.camera_id}] Auto-learn skipped: {e}")

    def _log_attendance(self, user_id: int, confidence: float, frame=None):
        """Log attendance punch. Debounce prevents duplicate entries AND snapshots."""
        now  = time.time()
        last = self._last_seen.get(user_id, 0)
        # Gate: same person within debounce window → skip entirely (no DB write, no snapshot)
        if now - last < self.DEBOUNCE_SEC:
            return
        self._last_seen[user_id] = now

        snap_path  = save_snapshot(frame, f'att_u{user_id}_cam{self.camera_id}', SNAPSHOT_DIR) if frame is not None else None
        punch_type = self._determine_punch(user_id)

        conn = get_db()
        conn.execute(
            'INSERT INTO attendance (user_id, camera_id, punch_type, confidence, snapshot_path) VALUES (?,?,?,?,?)',
            (user_id, self.camera_id, punch_type, confidence, snap_path)
        )
        conn.commit()
        conn.close()
        log.info(f"[Camera {self.camera_id}] {punch_type} logged for user_id={user_id} conf={confidence:.1f}%")

    def _is_duplicate_unknown(self, enc: np.ndarray) -> bool:
        """
        Returns True if this unknown face embedding is similar to one
        already saved within the cooldown window → skip saving duplicate.
        Returns False (and records the embedding) if it's a genuinely new face.
        """
        now      = time.time()
        cooldown = float(get_setting('unknown_debounce', str(self.UNKNOWN_COOLDOWN_SEC)))
        sim_thr  = float(get_setting('unknown_sim_threshold', str(self.UNKNOWN_SIM_THRESH)))

        # Drop entries older than the cooldown window
        self._unknown_buf = [e for e in self._unknown_buf if now - e['ts'] < cooldown]

        # Compare with every buffered embedding
        for entry in self._unknown_buf:
            sim = cosine_similarity(enc, entry['enc'])
            if sim >= sim_thr:
                # Same face seen recently — update its timestamp so the window rolls forward
                entry['ts'] = now
                log.debug(f"[Camera {self.camera_id}] Duplicate unknown (sim={sim:.3f}), skipping.")
                return True

        # New face → add to buffer
        self._unknown_buf.append({'enc': enc.copy(), 'ts': now})
        return False

    def _determine_punch(self, user_id: int) -> str:
        if self.direction == 'ENTRY':
            return 'IN'
        if self.direction == 'EXIT':
            return 'OUT'
        today = datetime.now().strftime('%Y-%m-%d')
        conn  = get_db()
        last  = conn.execute(
            "SELECT punch_type FROM attendance WHERE user_id=? AND DATE(punch_time)=? ORDER BY id DESC LIMIT 1",
            (user_id, today)
        ).fetchone()
        conn.close()
        if last is None or last['punch_type'] == 'OUT':
            return 'IN'
        return 'OUT'

    def _assign_unknown_cluster(self, enc: np.ndarray) -> str:
        """
        Group this unknown face with previous captures of the same person.
        Compares against recently-saved unknown-face embeddings; if one is
        similar enough, reuse its cluster_id (same unidentified visitor),
        otherwise start a new cluster.
        """
        sim_thr = float(get_setting('unknown_sim_threshold', str(self.UNKNOWN_SIM_THRESH)))
        conn = get_db()
        rows = conn.execute('''
            SELECT cluster_id, face_encoding FROM unknown_faces
            WHERE face_encoding IS NOT NULL AND cluster_id IS NOT NULL
            ORDER BY id DESC LIMIT 500
        ''').fetchall()
        conn.close()

        best_sim, best_cluster = 0.0, None
        for row in rows:
            try:
                other = pickle.loads(row['face_encoding'])
            except Exception:
                continue
            sim = cosine_similarity(enc, other)
            if sim > best_sim:
                best_sim, best_cluster = sim, row['cluster_id']

        if best_cluster and best_sim >= sim_thr:
            return best_cluster
        return uuid.uuid4().hex[:12]

    def _determine_unknown_punch(self, cluster_id: str) -> str:
        """Same IN/OUT alternation logic as _determine_punch, but for an
        unidentified visitor's cluster instead of an enrolled user_id."""
        if self.direction == 'ENTRY':
            return 'IN'
        if self.direction == 'EXIT':
            return 'OUT'
        today = datetime.now().strftime('%Y-%m-%d')
        conn  = get_db()
        last  = conn.execute(
            "SELECT punch_type FROM unknown_faces WHERE cluster_id=? AND DATE(detected_at)=? ORDER BY id DESC LIMIT 1",
            (cluster_id, today)
        ).fetchone()
        conn.close()
        if last is None or last['punch_type'] != 'IN':
            return 'IN'
        return 'OUT'

    def _log_unknown(self, snap_path: str, enc: np.ndarray):
        cluster_id = self._assign_unknown_cluster(enc)
        punch_type = self._determine_unknown_punch(cluster_id)
        conn = get_db()
        conn.execute(
            'INSERT INTO unknown_faces (camera_id, snapshot_path, face_encoding, cluster_id, punch_type) VALUES (?,?,?,?,?)',
            (self.camera_id, snap_path, encoding_to_blob(enc), cluster_id, punch_type)
        )
        conn.commit()
        conn.close()

    def _update_ping(self, ok: bool):
        conn = get_db()
        if ok:
            conn.execute(
                'UPDATE cameras SET last_ping=datetime("now","localtime") WHERE id=?',
                (self.camera_id,)
            )
        conn.commit()
        conn.close()


# ─────────────────────────────────────────────────────────────
#  Camera manager (global registry)
# ─────────────────────────────────────────────────────────────

class CameraManager:
    def __init__(self):
        self._workers: dict = {}

    def start_camera(self, camera_id: int, rtsp_url: str, direction: str = 'BOTH'):
        if camera_id in self._workers and self._workers[camera_id].is_alive():
            log.info(f"Camera {camera_id} already running.")
            return
        w = CameraWorker(camera_id, rtsp_url, direction)
        w.start()
        self._workers[camera_id] = w

    def stop_camera(self, camera_id: int):
        w = self._workers.pop(camera_id, None)
        if w:
            w.stop()

    def stop_all(self):
        for cid in list(self._workers):
            self.stop_camera(cid)

    def get_frame(self, camera_id: int):
        w = self._workers.get(camera_id)
        return w.get_latest_frame() if w else None

    def status(self):
        """Return per-camera status dict: alive = thread running, frames = video flowing."""
        return {
            cid: {'alive': w.is_alive(), 'frames': w.has_frames()}
            for cid, w in self._workers.items()
        }

    def start_all_active(self):
        conn    = get_db()
        cameras = conn.execute('SELECT id, rtsp_url, direction FROM cameras WHERE active=1').fetchall()
        conn.close()
        for cam in cameras:
            self.start_camera(cam['id'], cam['rtsp_url'], cam['direction'])


camera_manager = CameraManager()


# ─────────────────────────────────────────────────────────────
#  MJPEG frame generator
# ─────────────────────────────────────────────────────────────

def gen_mjpeg(camera_id: int):
    """Generator for Flask streaming response (MJPEG)."""
    while True:
        frame = camera_manager.get_frame(camera_id)
        if frame is None:
            ph = np.zeros((240, 320, 3), dtype=np.uint8)
            cv2.putText(ph, 'No Signal', (70, 120),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 180), 2)
            frame = ph
        _, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpg.tobytes() + b'\r\n')
        time.sleep(0.04)
