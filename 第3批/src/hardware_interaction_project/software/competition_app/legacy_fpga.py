"""FPGA coordinate adapter for the original desktop eye-control program.

Vision, Min-Max normalization, ridge fitting and gestures remain in the
original program. Only its four-term coordinate evaluation runs on the board.
"""
import copy
import math
import threading
import time

from mock_link.calibration import (build_profile, q16_from_float, sha256_id,
    FEATURE_MIN_Q16, FEATURE_MAX_Q16, CalibrationError)
from mock_link.host_client import HostClient
from .calibration import integer_reference


class LegacyFpgaMapper:
    def __init__(self, host='192.168.2.99', port=8765, *,
                 client_factory=HostClient, clock=time.monotonic,
                 result_max_age_s=.25):
        self.host, self.port = host, port
        self.client_factory, self.clock = client_factory, clock
        self.result_max_age_s = result_max_age_s
        self.lock = threading.RLock()
        self.client = None
        self.profile = None
        self.contract = None
        self.input_scale = 1.0
        self.point = None
        self.mapping_count = 0
        self.mapping_failures = 0
        self.heartbeat_count = 0
        self.last_error = ''
        self.observation_reason = 'waiting_for_eye'
        self.last_observation = None
        self.calibration_diagnostics = None
        self._last_result_at = float('-inf')
        self._last_io_at = float('-inf')
        self._retry_at = float('-inf')
        self._started = False
        self._closed = False
        self._fatal_error = False
        self._stop = threading.Event()
        self._thread = None

    @property
    def connected(self):
        with self.lock:
            return bool(not self._closed and self.client and self.client.connected)

    @property
    def ready(self):
        with self.lock:
            return (self.connected and self.profile is not None and self.point is not None
                    and (self.result_max_age_s is None or
                         0 <= self.clock() - self._last_result_at <= self.result_max_age_s))

    def start(self, background=True):
        with self.lock:
            if self._closed:
                raise RuntimeError('FPGA adapter is closed')
            if self._started:
                return
            self._started = True
        self.maintain()
        if background:
            self._thread = threading.Thread(target=self._loop,
                name='legacy-fpga-link', daemon=True)
            self._thread.start()

    def _loop(self):
        while not self._stop.wait(.1):
            self.maintain()

    @staticmethod
    def _diagnostic_number(value):
        """Keep bad input inspectable without putting NaN/Inf into JSON."""
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None if value is None else repr(value)
        return number if math.isfinite(number) else repr(number)

    def _set_observation_reason(self, reason):
        self.observation_reason = reason
        if self.last_observation is not None:
            self.last_observation['reason'] = reason

    def _new_observation(self, gx=None, gy=None):
        """Record the encoding contract, never clamp or repair an input."""
        feature_limits = [FEATURE_MIN_Q16, FEATURE_MAX_Q16]
        self.last_observation = {
            'gx': self._diagnostic_number(gx),
            'gy': self._diagnostic_number(gy),
            'input_scale': self.input_scale,
            'encoded_features': None,
            'encoded_q': None,
            'feature_limits_q16': feature_limits,
            'normalized_limits': [
                (value / 65536.0 - .5) * self.input_scale + .5
                for value in feature_limits],
            'submission_attempted': False,
            'board_status': None,
            'output_point': None,
            'reason': self.observation_reason,
        }

    def _drop(self, reason, observation_reason='fpga_link_unavailable'):
        if self.client:
            self.client.disconnect()
        self.point = None
        self.last_error = str(reason)
        self._set_observation_reason(observation_reason)
        self._retry_at = self.clock() + 1

    def _connect(self):
        if self.client:
            self.client.disconnect()
        self.point = None
        self.client = self.client_factory(host=self.host, port=self.port,
            timeout_s=.3, result_ttl_s=.25, calibration=self.profile)
        self.client.connect()
        self._last_io_at = float('-inf')
        self._retry_at = float('-inf')

    def maintain(self):
        """Maintain the same connection even when no face is visible.

        valid=0 requests never ask PL for a screen coordinate. They prevent
        the board's five-second idle timeout during calibration or face loss.
        """
        with self.lock:
            if not self._started or self._closed or self._fatal_error:
                return
            now = self.clock()
            try:
                if not self.connected:
                    if now < self._retry_at:
                        return
                    self._connect()
                if now - self._last_io_at < 1:
                    return
                result = self.client.submit(0, 0, valid=0)
                if (result.status != 'invalid_input' or
                        result.screen_x is not None or result.screen_y is not None):
                    raise ValueError('FPGA heartbeat returned a coordinate')
                self.heartbeat_count += 1
                self._last_io_at = self.clock()
                self.last_error = ''
            except Exception as exc:
                self._drop(exc)

    def configure(self, poly_matrix, eye_min, eye_range, screen_size):
        """Transmit the original fitted coefficients; do not fit a new model."""
        matrix = [[float(value) for value in row] for row in poly_matrix]
        minimum = [float(value) for value in eye_min]
        scale = [float(value) for value in eye_range]
        if (len(matrix) != 4 or any(len(row) != 2 for row in matrix) or
                len(minimum) != 2 or len(scale) != 2 or
                any(not math.isfinite(value) for row in matrix for value in row) or
                any(not math.isfinite(value) for value in minimum + scale) or
                any(value <= 0 for value in scale)):
            raise ValueError('Invalid original calibration parameters')
        contract = {'feature_source': 'legacy_mirrored_iris_pixels_v1',
            'eye_min': minimum, 'eye_range': scale,
            'normalization': '(original_iris_position-eye_min)/eye_range',
            'coefficient_order': ['gx', 'gy', 'gx*gy', '1'],
            'screen_size': list(screen_size)}
        # Re-express the SAME fitted polynomial in the board's feature domain.
        # gx = scale*u + offset, gy = scale*v + offset. This is algebraic
        # input encoding, not a new fit, extrapolation clamp or gaze model.
        # Prefer a wider domain while retaining the existing coefficient ABI.
        for scale_factor in (4.0, 2.0, 1.0):
            offset = (1-scale_factor)/2
            encoded = [
                [scale_factor*(matrix[0][axis]+offset*matrix[2][axis]) for axis in range(2)],
                [scale_factor*(matrix[1][axis]+offset*matrix[2][axis]) for axis in range(2)],
                [scale_factor**2*matrix[2][axis] for axis in range(2)],
                [matrix[3][axis]+offset*(matrix[0][axis]+matrix[1][axis])+
                    offset**2*matrix[2][axis] for axis in range(2)]]
            contract['fpga_input_scale'] = scale_factor
            contract['fpga_encoding'] = '(original_minmax_feature-0.5)/scale+0.5'
            try:
                profile = build_profile(sha256_id(contract), int(screen_size[0]),
                    int(screen_size[1]), [q16_from_float(row[0]) for row in encoded],
                    [q16_from_float(row[1]) for row in encoded])
                break
            except CalibrationError:
                if scale_factor == 1:
                    raise
        with self.lock:
            if self._closed:
                raise RuntimeError('FPGA adapter is closed')
            if self.client:
                self.client.disconnect()
            self.profile, self.contract = profile, contract
            self.input_scale = scale_factor
            self.calibration_diagnostics = {
                'eye_min': minimum,
                'eye_range': scale,
                'poly_matrix': matrix,
                'screen_size': list(screen_size),
                'input_scale': scale_factor,
                'calibration_id': profile['calibration_id'],
                'a_q16': list(profile['a_q16']),
                'b_q16': list(profile['b_q16']),
            }
            self._fatal_error = False
            self.point = None
            self._retry_at = float('-inf')
            self.last_error = ''
            self._set_observation_reason('waiting_for_eye')
            self._new_observation()
        self.maintain()
        return profile

    def skip_frame(self, reason='no_eye'):
        # Losing one camera frame does not erase calibration or control intent.
        with self.lock:
            self.point = None
            self._new_observation()
            self._set_observation_reason(reason)

    def map_normalized(self, gx, gy):
        with self.lock:
            self.point = None
            self._new_observation(gx, gy)
            if self._closed:
                self._set_observation_reason('adapter_closed')
                return None
            if self._fatal_error:
                self._set_observation_reason('fpga_result_mismatch')
                return None
            if self.profile is None:
                self._set_observation_reason('calibration_required')
                return None
            try:
                encoded = ((float(gx)-.5)/self.input_scale+.5,
                           (float(gy)-.5)/self.input_scale+.5)
                self.last_observation['encoded_features'] = [
                    self._diagnostic_number(value) for value in encoded]
                q = tuple(q16_from_float(value) for value in encoded)
                self.last_observation['encoded_q'] = list(q)
                if not all(FEATURE_MIN_Q16 <= v <= FEATURE_MAX_Q16 for v in q):
                    self._set_observation_reason('eye_outside_calibrated_range')
                    return None
            except (ValueError, TypeError, OverflowError):
                self._set_observation_reason('invalid_eye_position')
                return None
            self.maintain()
            if not self.connected:
                self._set_observation_reason('fpga_link_unavailable')
                return None
            began = self.clock()
            failure_reason = 'fpga_link_unavailable'
            try:
                self.last_observation['submission_attempted'] = True
                result = self.client.submit(*q)
                self._last_io_at = self.clock()
                failure_reason = 'invalid_fpga_result'
                self.last_observation['board_status'] = (
                    result.status if isinstance(result.status, str) else repr(result.status))
                if result.status != 'ok':
                    failure_reason = 'fpga_rejected_input'
                    raise ValueError('Board rejected gaze: ' + result.status)
                point = (result.screen_x, result.screen_y)
                self.last_observation['output_point'] = [
                    self._diagnostic_number(value) for value in point]
                if point != integer_reference(*q, self.profile):
                    self._fatal_error = True
                    failure_reason = 'fpga_result_mismatch'
                    raise ValueError('FPGA integer result mismatch')
                self.mapping_count += 1
                if (self.result_max_age_s is not None and
                        not 0 <= self.clock() - began <= self.result_max_age_s):
                    self._set_observation_reason('late_fpga_result')
                    return None
                self.point = point
                self._last_result_at = self.clock()
                self._set_observation_reason('ok')
                self.last_error = ''
                return point
            except Exception as exc:
                self.mapping_failures += 1
                self._drop(exc, failure_reason)
                return None

    def snapshot(self):
        with self.lock:
            return {'connected': self.connected, 'mapping_ready': self.ready,
                'mapping_count': self.mapping_count,
                'mapping_failures': self.mapping_failures,
                'heartbeat_count': self.heartbeat_count,
                'point': list(self.point) if self.ready else None,
                'error': self.last_error,
                'observation_reason': self.observation_reason,
                'calibration_id': self.profile['calibration_id'] if self.profile else None,
                'fpga_input_scale': self.input_scale,
                'last_observation': copy.deepcopy(self.last_observation),
                'calibration_diagnostics': copy.deepcopy(self.calibration_diagnostics),
                'link_mode': 'personal_mapping' if self.profile else 'connected_waiting_calibration'}

    def close(self):
        self._stop.set()
        with self.lock:
            self._closed = True
            self.point = None
            if self.client:
                self.client.disconnect()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(1)
