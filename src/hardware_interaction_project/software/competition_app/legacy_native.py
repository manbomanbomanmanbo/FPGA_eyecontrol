"""Run the original eye-control windows with FPGA coordinate evaluation."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import time

import numpy as np

from legacy_eyecontrol.eye_tracker import EyeTracker
from legacy_eyecontrol.mouse_controller import MouseController
from legacy_eyecontrol.main import EyeControlSystem
from .legacy_fpga import LegacyFpgaMapper


class FpgaEyeTracker(EyeTracker):
    def __init__(self, mapper, screen_size):
        super().__init__()
        self.mapper = mapper
        self.screen_size = tuple(screen_size)
        self._reporting_quality = False

    def perform_calibration(self):
        if not super().perform_calibration():
            return False
        try:
            self.mapper.configure(self.poly_matrix, self.eye_min,
                self.eye_range, self.screen_size)
            return True
        except Exception as exc:
            self.is_calibrated = False
            print('FPGA calibration transfer failed:', exc)
            return False

    def _map_normalized(self, feat):
        if self._reporting_quality:
            # Preserve the old console quality report. It is never a gate,
            # never generates board test coordinates and never moves a mouse.
            return feat @ self.poly_matrix
        point = self.mapper.map_normalized(float(feat[0]), float(feat[1]))
        if point is None:
            raise ValueError(self.mapper.last_error or 'No current FPGA coordinate')
        return np.asarray(point, dtype=float)

    def _calculate_calibration_error(self):
        self._reporting_quality = True
        try:
            return super()._calculate_calibration_error()
        finally:
            self._reporting_quality = False

    def detect_iris_position(self, frame):
        position = super().detect_iris_position(frame)
        if position is None:
            self.mapper.skip_frame('no_eye')
        else:
            self.mapper.observation_reason = 'eye_detected'
        return position


class LinkedMouseController(MouseController):
    """Keep the original margin and smoothing; omit unusable FPGA frames."""
    def __init__(self, mapper, tracker):
        super().__init__()
        self.mapper, self.tracker = mapper, tracker
        self.os_moves = 0

    def _ready(self):
        return self.tracker.is_calibrated and self.mapper.ready

    def move(self, pos):
        if self._ready():
            super().move(pos)
            self.os_moves += 1

    def click(self):
        if self._ready():
            return super().click()

    def double_click(self):
        if self._ready():
            return super().double_click()

    def right_click(self):
        if self._ready():
            return super().right_click()

    def scroll(self, direction):
        if self._ready():
            return super().scroll(direction)


class FpgaEyeControlSystem(EyeControlSystem):
    def __init__(self, output, mapper):
        self.native_output = Path(output)
        self.native_output.mkdir(parents=True, exist_ok=True)
        self.mapper = mapper
        self._status_written = float('-inf')
        self._running = True
        mouse = MouseController()
        tracker = FpgaEyeTracker(mapper, mouse.get_screen_size())
        linked_mouse = LinkedMouseController(mapper, tracker)
        super().__init__(eye_tracker=tracker, mouse_controller=linked_mouse,
            on_frame=self.write_status)

    def open_output_file(self):
        self.app_dir = str(self.native_output.parent)
        self.data_dir = str(self.native_output)
        return super().open_output_file()

    def write_status(self, force=False):
        if not force and time.monotonic() - self._status_written < 1:
            return
        state = self.mapper.snapshot()
        state.update(application='eye_control_fpga_native', pid=os.getpid(),
            running=self._running, source='original_iris_camera',
            camera_running=bool(getattr(self, 'cap', None) and self.cap.isOpened()),
            mouse_enabled=bool(self._running and self.enable_control),
            mouse_output_ready=bool(self._running and self.enable_control and
                self.eye_tracker.is_calibrated and self.mapper.ready),
            calibrated=self.eye_tracker.is_calibrated,
            os_move_calls=self.mouse_controller.os_moves,
            implementation='original_vision_calibration_gestures; FPGA_mapping_only')
        temporary = self.native_output / 'native_status.tmp'
        temporary.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
        temporary.replace(self.native_output / 'native_status.json')
        self._status_written = time.monotonic()

    def close(self):
        self._running = False
        self.enable_control = False
        self.mapper.close()
        if getattr(self, 'cap', None) is not None:
            self.cap.release()
        self.close_output_file()
        self.eye_tracker.face_mesh.close()
        self.write_status(force=True)


def main():
    parser = argparse.ArgumentParser(description='Original desktop eye control with FPGA mapping')
    parser.add_argument('--board-host', default='192.168.2.99')
    parser.add_argument('--board-port', type=int, default=8765)
    parser.add_argument('--camera-index', type=int, default=0)
    parser.add_argument('--output', default=str(Path(__file__).resolve().parents[2] / 'runtime_native'))
    args = parser.parse_args()
    if os.name == 'nt':
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass
    mapper = LegacyFpgaMapper(args.board_host, args.board_port)
    mapper.start()
    system = None
    try:
        system = FpgaEyeControlSystem(args.output, mapper)
        system.camera_index = args.camera_index
        system.run()
    finally:
        if system is not None:
            system.close()
        else:
            mapper.close()


if __name__ == '__main__':
    main()
