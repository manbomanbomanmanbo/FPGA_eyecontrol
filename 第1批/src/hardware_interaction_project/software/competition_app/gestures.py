"""Numerical mouth gestures for the native two-key eye-control program.

These thresholds are engineering defaults, not a claim of live recognition
accuracy. Side labels describe the mirrored preview. The detector emits only
scroll requests; the session must still enforce camera, FPGA and mouse gates.
"""
import math

from .vision import FEATURE_SOURCE, MAX_FRAME_AGE_NS


class MouthGestureDetector:
    """Stable closed-mouth baseline, direction selection and bounded scrolling.

    Three fresh, distinct frames establish the baseline, select a direction or
    start scrolling. A missing face, old/out-of-order data or an invalid mouth
    cancels pending actions. Duplicate frames never count as observations.
    """
    def __init__(self, stable_frames=3, direction_threshold=.1,
                 open_ratio=.4, scroll_interval_ms=150):
        if (type(stable_frames) is not int or stable_frames < 3 or
                not 0 < direction_threshold < 1 or not 0 < open_ratio < 1 or
                scroll_interval_ms < 150):
            raise ValueError('invalid mouth gesture thresholds')
        self.stable_frames = stable_frames
        self.direction_threshold = direction_threshold
        self.open_ratio = open_ratio
        self.scroll_interval_ns = int(scroll_interval_ms * 1_000_000)
        self.reset()

    def reset(self):
        self.direction = 'scroll_down'
        self.baseline = None
        self._neutral = []
        self._direction_pending = None
        self._direction_frames = 0
        self._open_frames = 0
        self._last_event_ns = None
        self._last_frame_id = None
        self._last_capture_ns = None

    def _cancel_pending(self):
        self._direction_pending = None
        self._direction_frames = 0
        self._open_frames = 0
        self._neutral = []

    def _invalidate(self):
        # Reacquiring a moved face requires a new neutral reference. Keeping
        # the selected direction is harmless; without a baseline no scroll emits.
        self._cancel_pending()
        self.baseline = None

    def update(self, observation):
        """Return None, scroll_up or scroll_down from one camera observation.

        Freshness is supplied by CameraWorker.latest(); the numerical check
        also rejects stale processing and gaps in the capture timeline. No
        wall-clock calls, camera accesses or OS input occur here.
        """
        frame = observation.frame_id
        capture = observation.capture_monotonic_ns
        processed = observation.processed_monotonic_ns
        if (type(frame) is not int or frame < 0 or type(capture) is not int or
                capture < 0 or type(processed) is not int or
                not 0 <= processed - capture <= MAX_FRAME_AGE_NS):
            self._invalidate()
            return None
        values = (observation.mouth_stretch_left, observation.mouth_stretch_right,
                  observation.mouth_open_ratio)
        if (not observation.valid or observation.reason != 'ok' or
                observation.feature_source != FEATURE_SOURCE or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in values) or
                not all(0 <= v <= 2 for v in values[:2]) or
                not 0 <= values[2] <= 1.2):
            self._invalidate()
            return None
        if self._last_frame_id is not None:
            if frame == self._last_frame_id:
                self._cancel_pending()
                return None
            if frame < self._last_frame_id or capture <= self._last_capture_ns:
                self._invalidate()
                return None
            if capture - self._last_capture_ns > MAX_FRAME_AGE_NS:
                self._invalidate()
        self._last_frame_id, self._last_capture_ns = frame, capture
        left, right, opening = values
        opened = opening > self.open_ratio
        if self.baseline is None:
            if opened:
                self._cancel_pending()
                return None
            if self._neutral and (abs(left - self._neutral[0][0]) > .05 or
                                  abs(right - self._neutral[0][1]) > .05):
                self._neutral = []
            self._neutral.append((left, right))
            if len(self._neutral) >= self.stable_frames:
                self.baseline = tuple(sum(p[i] for p in self._neutral) / len(self._neutral)
                                      for i in range(2))
                self._neutral = []
            return None
        if opened:
            self._direction_pending, self._direction_frames = None, 0
            self._open_frames = min(self._open_frames + 1, self.stable_frames)
            if (self._open_frames >= self.stable_frames and
                    (self._last_event_ns is None or
                     capture - self._last_event_ns >= self.scroll_interval_ns)):
                self._last_event_ns = capture
                return self.direction
            return None
        # Closed lips stop scrolling immediately, even when the direction is
        # still being selected. No selected direction implies an action alone.
        self._open_frames = 0
        dl, dr = left - self.baseline[0], right - self.baseline[1]
        candidate = None
        if dl > self.direction_threshold and dr <= self.direction_threshold:
            candidate = 'scroll_up'
        elif dr > self.direction_threshold and dl <= self.direction_threshold:
            candidate = 'scroll_down'
        if candidate is None:
            self._direction_pending, self._direction_frames = None, 0
            if abs(dl) < .05 and abs(dr) < .05:
                self.baseline = (self.baseline[0] * .98 + left * .02,
                                 self.baseline[1] * .98 + right * .02)
        else:
            if candidate == self._direction_pending:
                self._direction_frames += 1
            else:
                self._direction_pending, self._direction_frames = candidate, 1
            if self._direction_frames >= self.stable_frames:
                self.direction = candidate
        return None
