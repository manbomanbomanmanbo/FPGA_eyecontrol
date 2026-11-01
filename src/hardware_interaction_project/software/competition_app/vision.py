"""Camera measurements for the competition app; never controls the OS mouse.

Feature contract iris_eye_axes_v1 uses the unmirrored camera frame. Each eye's
horizontal axis points toward image right; its perpendicular points down. The
iris horizontal fraction and vertical displacement from the corner midpoint
are divided by eye width, then averaged across the two eyes. Calibration owns
normalisation and fixed-point conversion. Preview mirroring does not alter it.

Landmark geometry / blink thresholds are engineering defaults, not measured
accuracy claims. Host read-start times are not camera hardware timestamps.
"""

from dataclasses import dataclass, replace
import math
import threading
import time
from typing import Optional, Tuple


FEATURE_SOURCE = "iris_eye_axes_v1"
MAX_FRAME_AGE_NS = 250_000_000


@dataclass(frozen=True)
class FrameObservation:
    frame_id: int
    capture_monotonic_ns: int
    processed_monotonic_ns: int
    valid: bool
    reason: str
    raw_gx: Optional[float]
    raw_gy: Optional[float]
    eye_open_l: Optional[float]
    eye_open_r: Optional[float]
    blink_event_id: int
    blink_event: Optional[str]
    width: int
    height: int
    feature_source: str = FEATURE_SOURCE
    blink_duration_ms: Optional[float] = None
    # Pixel center in the unmirrored source image; presentation may mirror it.
    eye_position: Optional[Tuple[float, float]] = None
    # Side names refer to the horizontally mirrored camera preview.
    mouth_stretch_left: Optional[float] = None
    mouth_stretch_right: Optional[float] = None
    mouth_open_ratio: Optional[float] = None


@dataclass(frozen=True)
class FeatureMeasurement:
    valid: bool
    reason: str
    raw_gx: Optional[float] = None
    raw_gy: Optional[float] = None
    eye_open_l: Optional[float] = None
    eye_open_r: Optional[float] = None
    # Preview-only coordinates in the unmirrored source image.
    markers: Tuple[Tuple[float, float], ...] = ()
    eye_position: Optional[Tuple[float, float]] = None
    mouth_stretch_left: Optional[float] = None
    mouth_stretch_right: Optional[float] = None
    mouth_open_ratio: Optional[float] = None


@dataclass(frozen=True)
class BlinkEvent:
    event_id: int
    monotonic_ns: int
    duration_ms: float
    kind: str = "bilateral_blink"


def _point(landmarks, index, width, height):
    item = landmarks[index]
    if hasattr(item, "x"):
        x, y = float(item.x), float(item.y)
    elif isinstance(item, dict):
        x, y = float(item["x"]), float(item["y"])
    else:
        x, y = float(item[0]), float(item[1])
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("nonfinite_landmark")
    if not (-0.02 <= x <= 1.02 and -0.02 <= y <= 1.02):
        raise ValueError("eyes_outside_frame")
    return x * width, y * height


def _average(points):
    return (sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points))


def _eye(landmarks, width, height, corners, upper, lower, iris):
    p, q = sorted((_point(landmarks, i, width, height) for i in corners))
    dx, dy = q[0] - p[0], q[1] - p[1]
    eye_width = math.hypot(dx, dy)
    if eye_width < 12.0 or dx <= 0:
        raise ValueError("eye_too_small")
    ux, uy = dx / eye_width, dy / eye_width
    vx, vy = -uy, ux  # vy is positive because corners are ordered by image x.
    if abs(uy) > math.sin(math.radians(40)):
        raise ValueError("head_roll_excessive")
    center = _average([_point(landmarks, i, width, height) for i in iris])
    top = _average([_point(landmarks, i, width, height) for i in upper])
    bottom = _average([_point(landmarks, i, width, height) for i in lower])
    mid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
    gx = ((center[0] - p[0]) * ux + (center[1] - p[1]) * uy) / eye_width
    gy = ((center[0] - mid[0]) * vx + (center[1] - mid[1]) * vy) / eye_width
    aperture = ((bottom[0] - top[0]) * vx + (bottom[1] - top[1]) * vy) / eye_width
    if not (-0.15 <= gx <= 1.15 and abs(gy) <= 0.45 and 0 <= aperture <= 0.7):
        raise ValueError("eye_geometry_invalid")
    return gx, gy, aperture, eye_width, (p, q, center)


def _mouth_measurements(landmarks, width, height):
    """Same-face measurements, labelled by mirrored-preview left and right.

    Sorting original pixel x makes the label independent of anatomical names:
    preview left is original-image right. Degenerate/malformed mouth landmarks
    do not invalidate otherwise usable iris measurements; they disable gestures.
    """
    try:
        nose = _point(landmarks, 1, width, height)
        eye_left, eye_right = sorted(_point(landmarks, i, width, height) for i in (33, 263))
        mouth_left, mouth_right = sorted(_point(landmarks, i, width, height) for i in (61, 291))
        upper, lower = (_point(landmarks, i, width, height) for i in (13, 14))
        base_left, base_right = nose[0] - eye_left[0], eye_right[0] - nose[0]
        mouth_width = math.hypot(mouth_right[0] - mouth_left[0], mouth_right[1] - mouth_left[1])
        if (min(base_left, base_right, mouth_width) < 12 or
                not mouth_left[0] < nose[0] < mouth_right[0]):
            return None, None, None
        # Mirror swaps the two image sides. Ratios retain the old nose/eye
        # normalisation, while using pixel units for nonsquare source images.
        preview_left = (mouth_right[0] - nose[0]) / base_right
        preview_right = (nose[0] - mouth_left[0]) / base_left
        opened = (lower[1] - upper[1]) / mouth_width
        if not (0 <= preview_left <= 2 and 0 <= preview_right <= 2 and 0 <= opened <= 1.2):
            return None, None, None
        return preview_left, preview_right, opened
    except (ValueError, TypeError, IndexError, KeyError, AttributeError):
        return None, None, None


def extract_features(landmarks, width, height, min_open_ratio=0.12):
    """Pure numerical geometry; no MediaPipe, camera, image or NumPy import.

    Eye l/r names follow MediaPipe anatomical naming, not preview sides. A
    missing/invalid sample contains no substitute gaze coordinates.
    """
    if not isinstance(width, int) or not isinstance(height, int) or min(width, height) <= 0:
        return FeatureMeasurement(False, "invalid_frame_size")
    landmarks = getattr(landmarks, "landmark", landmarks)
    try:
        if len(landmarks) < 478:
            return FeatureMeasurement(False, "iris_landmarks_missing")
        left = _eye(landmarks, width, height, (362, 263), (386, 385),
                    (374, 380), (474, 475, 476, 477))
        right = _eye(landmarks, width, height, (33, 133), (159, 158),
                     (145, 153), (469, 470, 471, 472))
        if max(left[3], right[3]) / min(left[3], right[3]) > 2.0:
            return FeatureMeasurement(False, "eye_scale_mismatch")
        markers = left[4] + right[4]
        eye_position = _average((left[4][2], right[4][2]))
        if min(left[2], right[2]) < min_open_ratio:
            return FeatureMeasurement(False, "eyes_closed", eye_open_l=left[2],
                                      eye_open_r=right[2], markers=markers,
                                      eye_position=eye_position)
        mouth_left, mouth_right, mouth_open = _mouth_measurements(landmarks, width, height)
        return FeatureMeasurement(True, "ok", (left[0] + right[0]) / 2,
                                  (left[1] + right[1]) / 2, left[2], right[2], markers,
                                  eye_position, mouth_left, mouth_right, mouth_open)
    except (ValueError, TypeError, IndexError, KeyError, AttributeError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "landmark_format_invalid"
        return FeatureMeasurement(False, reason)


def features_from_faces(faces, width, height):
    if not faces:
        return FeatureMeasurement(False, "no_face")
    if len(faces) != 1:
        return FeatureMeasurement(False, "multiple_faces")
    return extract_features(faces[0], width, height)


class BlinkDetector:
    """Bilateral close/reopen event detector; events do not mean mouse clicks.

    Hysteresis, stable-open arming, bounded closure, reopen confirmation and
    a cooldown reject single noisy frames and long closures. A dropped frame,
    face loss or invalid geometry cancels the pending event. Intentional and
    natural blinks cannot be reliably distinguished by this state machine.
    """

    def __init__(self, close_ratio=0.12, open_ratio=0.17, min_closed_ms=100,
                 max_closed_ms=800, arm_ms=200, reopen_ms=80, cooldown_ms=600):
        if not 0 < close_ratio < open_ratio < 1:
            raise ValueError("invalid blink aperture thresholds")
        if not 0 < min_closed_ms < max_closed_ms or min(arm_ms, reopen_ms, cooldown_ms) < 0:
            raise ValueError("invalid blink timing thresholds")
        self.close_ratio, self.open_ratio = close_ratio, open_ratio
        self.min_closed_ns = int(min_closed_ms * 1_000_000)
        self.max_closed_ns = int(max_closed_ms * 1_000_000)
        self.arm_ns = int(arm_ms * 1_000_000)
        self.reopen_ns = int(reopen_ms * 1_000_000)
        self.cooldown_ns = int(cooldown_ms * 1_000_000)
        self.event_id = 0
        self.last_event_ns = None
        self.reset()

    def reset(self):
        self.state = "waiting_open"
        self.open_since = None
        self.closed_since = None
        self.reopen_since = None
        self.last_time_ns = None
        self.closed_duration_ns = None

    def update(self, left, right, now_ns, usable=True):
        if (not usable or left is None or right is None or
                not all(math.isfinite(v) and 0 <= v <= 0.7 for v in (left, right))):
            self.reset()
            return None
        if self.last_time_ns is not None and (now_ns <= self.last_time_ns or
                                              now_ns - self.last_time_ns > MAX_FRAME_AGE_NS):
            self.reset()
        self.last_time_ns = now_ns
        opened = min(left, right) >= self.open_ratio
        closed = max(left, right) <= self.close_ratio
        if self.state in ("waiting_open", "armed"):
            if opened:
                if self.open_since is None:
                    self.open_since = now_ns
                if now_ns - self.open_since >= self.arm_ns:
                    self.state = "armed"
            elif closed and self.state == "armed":
                if self.last_event_ns is None or now_ns - self.last_event_ns >= self.cooldown_ns:
                    self.state = "closed"
                    self.closed_since = now_ns
                else:
                    self.state = "waiting_open"
                self.open_since = None
            else:
                self.state = "waiting_open"
                self.open_since = None
        elif self.state == "closed":
            duration = now_ns - self.closed_since
            if duration > self.max_closed_ns:
                self.reset()
                self.last_time_ns = now_ns
            elif opened:
                if duration < self.min_closed_ns:
                    self.reset()
                    self.last_time_ns = now_ns
                    self.open_since = now_ns
                else:
                    self.state = "reopening"
                    self.reopen_since = now_ns
                    self.closed_duration_ns = duration
            elif not closed:
                # Transition aperture alone does not create a blink event.
                pass
        elif self.state == "reopening":
            if not opened:
                self.reset()
                self.last_time_ns = now_ns
            elif now_ns - self.reopen_since >= self.reopen_ns:
                self.event_id += 1
                self.last_event_ns = now_ns
                event = BlinkEvent(self.event_id, now_ns, self.closed_duration_ns / 1_000_000)
                self.reset()
                self.last_time_ns = now_ns
                self.open_since = now_ns
                return event
        return None


def _empty_observation(reason, frame_id=0, capture_ns=0, width=0, height=0, event_id=0):
    return FrameObservation(frame_id, capture_ns, time.monotonic_ns(), False, reason,
                            None, None, None, None, event_id, None, width, height)


class CameraWorker:
    """One background capture thread, one latest result, no image persistence.

    start() starts the worker; status()/latest() report device-open failure.
    stop() returns False if an OS camera read has not yet returned, and never
    claims such a thread has stopped. The consumer must deduplicate frame IDs
    and blink_event_id, and explicitly choose whether to act on any event.
    """

    def __init__(self, camera_index=0, width=640, height=480, preview_mirror=True):
        self.camera_index = camera_index
        self.requested_width, self.requested_height = width, height
        self.preview_mirror = preview_mirror
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._observation = _empty_observation("camera_not_started")
        self._preview = None
        self._preview_capture_ns = 0
        self._state = "stopped"
        self._error = None
        self._frame_id = 0
        self._blink = BlinkDetector()

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._blink.reset()
            self._state, self._error = "starting", None
            self._observation = _empty_observation("camera_starting", self._frame_id,
                                                   event_id=self._blink.event_id)
            self._thread = threading.Thread(target=self._run, name="competition-camera", daemon=True)
            self._thread.start()
        return True

    def stop(self, timeout_s=2.0):
        self._stop.set()
        with self._lock:
            thread = self._thread
            self._state = "stopping" if thread and thread.is_alive() else "stopped"
            self._observation = replace(self._observation, valid=False, reason="camera_stopped",
                                        raw_gx=None, raw_gy=None, blink_event=None,
                                        blink_duration_ms=None, eye_position=None,
                                        mouth_stretch_left=None, mouth_stretch_right=None,
                                        mouth_open_ratio=None)
            self._preview = None
        if thread and thread is not threading.current_thread():
            thread.join(max(0, timeout_s))
        return thread is None or not thread.is_alive()

    def latest(self):
        with self._lock:
            observation = self._observation
        age = time.monotonic_ns() - observation.capture_monotonic_ns
        if observation.capture_monotonic_ns and (age < 0 or age > MAX_FRAME_AGE_NS):
            return replace(observation, valid=False, reason="stale_frame", raw_gx=None,
                           raw_gy=None, blink_event=None, blink_duration_ms=None,
                           eye_position=None, mouth_stretch_left=None,
                           mouth_stretch_right=None, mouth_open_ratio=None)
        return observation

    def preview_jpeg(self):
        with self._lock:
            if self._stop.is_set() or time.monotonic_ns() - self._preview_capture_ns > MAX_FRAME_AGE_NS:
                return None
            return self._preview

    def status(self):
        with self._lock:
            return {"state": self._state, "error": self._error,
                    "running": bool(self._thread and self._thread.is_alive()),
                    "camera_index": self.camera_index,
                    "feature_source": FEATURE_SOURCE,
                    "blink_events": self._blink.event_id}

    def _run(self):
        cap = face_mesh = None
        try:
            import cv2
            import mediapipe as mp

            cap = cv2.VideoCapture(self.camera_index)
            if not cap.isOpened():
                raise RuntimeError("camera_open_failed")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.requested_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.requested_height)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            face_mesh = mp.solutions.face_mesh.FaceMesh(
                max_num_faces=2, refine_landmarks=True,
                min_detection_confidence=0.7, min_tracking_confidence=0.7)
            with self._lock:
                self._state = "running"
            while not self._stop.is_set():
                capture_ns = time.monotonic_ns()
                ok, frame = cap.read()
                if not ok or frame is None:
                    raise RuntimeError("camera_read_failed")
                self._frame_id += 1
                height, width = frame.shape[:2]
                # Feature extraction always receives the unmirrored original.
                result = face_mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                measurement = features_from_faces(result.multi_face_landmarks, width, height)
                processed_ns = time.monotonic_ns()
                fresh = processed_ns - capture_ns <= MAX_FRAME_AGE_NS
                event = self._blink.update(measurement.eye_open_l, measurement.eye_open_r,
                                           capture_ns, usable=fresh and measurement.reason in ("ok", "eyes_closed"))
                observation = FrameObservation(
                    self._frame_id, capture_ns, processed_ns,
                    measurement.valid and fresh, measurement.reason if fresh else "stale_frame",
                    measurement.raw_gx if fresh else None, measurement.raw_gy if fresh else None,
                    measurement.eye_open_l, measurement.eye_open_r, self._blink.event_id,
                    event.kind if event and fresh else None, width, height,
                    blink_duration_ms=event.duration_ms if event and fresh else None,
                    eye_position=measurement.eye_position if fresh else None,
                    mouth_stretch_left=measurement.mouth_stretch_left if fresh and measurement.valid else None,
                    mouth_stretch_right=measurement.mouth_stretch_right if fresh and measurement.valid else None,
                    mouth_open_ratio=measurement.mouth_open_ratio if fresh and measurement.valid else None)
                preview = frame.copy()
                if self.preview_mirror:
                    preview = cv2.flip(preview, 1)
                if width > 640:
                    preview = cv2.resize(preview, (640, max(1, round(height * 640 / width))))
                encoded, jpeg = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 75])
                with self._lock:
                    if not self._stop.is_set():
                        self._observation = observation
                        self._preview = jpeg.tobytes() if encoded else None
                        self._preview_capture_ns = capture_ns
        except Exception as exc:
            with self._lock:
                self._state, self._error = "error", str(exc)
                self._observation = _empty_observation("camera_error", self._frame_id,
                                                       event_id=self._blink.event_id)
                self._preview = None
        finally:
            self._blink.reset()
            # Release the camera even if a runtime's model cleanup fails.
            try:
                if face_mesh is not None:
                    face_mesh.close()
            finally:
                try:
                    if cap is not None:
                        cap.release()
                finally:
                    with self._lock:
                        if self._state != "error":
                            self._state = "stopped"
