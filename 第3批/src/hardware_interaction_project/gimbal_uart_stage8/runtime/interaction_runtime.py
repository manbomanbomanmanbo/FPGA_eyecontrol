"""Stage 1 adapters for the copied original desktop workflow.

Vision and Windows semantics remain in the original classes. SMOOTH, SCREEN,
PLAN/ACK, MOUTH, MOVE and HOLD use the exclusive PL interaction session.
"""
import numpy as np
import pyautogui

from main_fpga import FpgaEyeTracker
from mouse_controller import MouseController
from interaction_stage6.runtime_adapter import InteractionFailure


class FpgaInteractionTracker(FpgaEyeTracker):
    def __init__(self, mapper, screen_size, interaction):
        super().__init__(mapper, screen_size)
        self.interaction = interaction
        self.screen_mapping_valid = False
        self.mapping_invalid_reason = None
        self._tracking_reset_pending = False
        # 等观察到真实图像尺寸后才配置；相同尺寸的预览只配置一次。
        self._preview_profile_size = None

    def _configure_preview_profile(self):
        if self.frame_size is None:
            raise InteractionFailure("Camera frame size has not been observed")
        width, height = self.screen_size
        matrix = np.asarray(((width, 0), (0, height), (0, 0), (0, 0)), dtype=float)
        # The existing mapper clamps at dimension-1. A preview-only profile
        # one pixel larger permits the source's inclusive screen endpoints.
        self.mapper.configure(matrix, (0, 0), self.frame_size, (width + 1, height + 1))
        self._preview_profile_size = self.frame_size

    def _invalidate_tracking(self, reason):
        was_ready = self.tracking_ready
        if was_ready:
            # 推迟到重获连续有效观测时重建；每个丢失周期只执行一次。
            self._tracking_reset_pending = True
        self.screen_mapping_valid = False
        super()._invalidate_tracking(reason)

    def _enhanced_smoothing(self, new_position):
        if self._tracking_reset_pending:
            cursor = tuple(pyautogui.position())
            self.interaction.restart_tracking(cursor, self.mouth_scroll_direction)
            self._tracking_reset_pending = False
            self.last_locked_output = cursor
            self.mouth_open_counter = 0
            if self.interaction.trace is not None:
                self.interaction.trace("eye_tracking_reset", cursor=list(cursor),
                                       generation=self.tracking_generation,
                                       calibrated=self.is_calibrated)
        point = self.interaction.smooth(new_position)
        self.last_good_position = point
        return point

    def detect_iris_position(self, frame):
        point = super().detect_iris_position(frame)
        # The original vision method catches ordinary geometry errors. A
        # failed PL call must escape that catch and terminate this session.
        if self.interaction.failed:
            raise InteractionFailure("FPGA iris smoothing failed")
        return point

    def perform_calibration(self):
        # 校准可能覆盖映射器配置；失败后或下次 C 校准也要重建预览。
        self._preview_profile_size = None
        return super().perform_calibration()

    def _map_normalized(self, feat):
        point = self.mapper.map_normalized(float(feat[0]), float(feat[1]))
        if point is None:
            raise InteractionFailure("FPGA gaze mapping returned no verified coordinate")
        return np.asarray(point, dtype=float)

    def to_screen_coordinates(self, eye_pos, screen_size):
        blink = (self.current_blink_status in ("double", "right") or
                 self.right_wink_candidate_active)
        remaining = self.interaction.screen_remaining
        # Preserve the source's lazy mapping: no request during the early
        # lock calls, only the three final transition calls and normal calls.
        needs_mapping = (not blink and (remaining == 0 or
                         (remaining <= 3 and self.interaction.history_count > 0
                          and self.is_calibrated)))
        mapped = None
        self.screen_mapping_valid = False
        self.mapping_invalid_reason = None
        if needs_mapping and eye_pos:
            if self.is_calibrated and hasattr(self, "poly_matrix"):
                gx = (eye_pos[0] - self.eye_min[0]) / self.eye_range[0]
                gy = (eye_pos[1] - self.eye_min[1]) / self.eye_range[1]
            else:
                # C 校准期间也使用实际帧尺寸，只在尺寸变化或配置被覆盖时重建。
                if self._preview_profile_size != self.frame_size or self.frame_size is None:
                    self._configure_preview_profile()
                frame_width, frame_height = self.frame_size
                gx, gy = eye_pos[0] / frame_width, eye_pos[1] / frame_height
            mapped = self.mapper.map_normalized(float(gx), float(gy))
            if mapped is None and not self.mapper.connected:
                raise InteractionFailure("FPGA gaze mapping link is unavailable")
            self.screen_mapping_valid = mapped is not None
            if mapped is None:
                self.mapping_invalid_reason = getattr(self.mapper, "observation_reason", "mapping_unavailable")
                self.recalibration_prompt = True
            else:
                self.recalibration_prompt = False
        else:
            # Lock output is itself verified PL state and can be moved to
            # while the source permits movement. No CPU coordinate is used.
            self.screen_mapping_valid = True
        result = self.interaction.screen(mapped, screen_size, blink=blink,
                                         calibrated=self.is_calibrated)
        self.last_locked_output = result
        self.lock_frames_remaining = self.interaction.screen_remaining
        self.blink_lock_active = self.interaction.screen_active
        return result

    def update_mouth_direction_and_scroll(self, left_change, right_change, is_mouth_open):
        command, direction, count = self.interaction.mouth(left_change, right_change, is_mouth_open)
        self.mouth_scroll_direction = direction
        self.mouth_open_counter = count
        return command, False


class FpgaInteractionMouse(MouseController):
    def __init__(self, interaction):
        self.interaction = interaction
        super().__init__()
        interaction.trace = self.trace_event

    def move(self, pos):
        point, permitted = self.interaction.move(pos, self.get_screen_size())
        if permitted:
            pyautogui.moveTo(*point, _pause=False)
            self.last_pos = point

    def _hold_position(self, position):
        # The inherited click/double methods pass the real OS cursor chosen
        # at click time. Never substitute the estimated screen coordinates.
        self.interaction.hold(position)
        self.last_pos = position
