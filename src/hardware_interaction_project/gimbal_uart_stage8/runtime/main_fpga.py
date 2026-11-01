"""Original improve4 eye control and ZED workflow with both FPGA interfaces."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import keyboard

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "software"))

from competition_app.legacy_fpga import LegacyFpgaMapper
from eye_tracker import EyeTracker
from main import EyeControlSystem
from mouse_controller import MouseController


class FpgaEyeTracker(EyeTracker):
    def __init__(self, mapper, screen_size):
        super().__init__()
        self.mapper = mapper
        self.screen_size = tuple(screen_size)

    def perform_calibration(self):
        if not super().perform_calibration():
            return False
        try:
            self.mapper.configure(self.poly_matrix, self.eye_min,
                                  self.eye_range, self.screen_size)
            return True
        except Exception:
            self.is_calibrated = False
            return False

    def _map_normalized(self, feat):
        point = self.mapper.map_normalized(float(feat[0]), float(feat[1]))
        if point is None:
            point = self.last_locked_output
        return np.asarray(point, dtype=float)

    def detect_iris_position(self, frame):
        position = super().detect_iris_position(frame)
        if position is None:
            self.mapper.skip_frame("no_eye")
        return position


class FpgaEyeControlSystem(EyeControlSystem):
    def __init__(self, mapper, tracker, mouse, zed_python, stereo_port):
        self.mapper = mapper
        self.fpga_zed_python = str(zed_python)
        self.fpga_stereo_port = int(stereo_port)
        try:
            super().__init__(eye_tracker=tracker, mouse_controller=mouse)
        except BaseException:
            self.close()
            raise

    def close(self):
        # Covers constructor failure and the original camera-open early return.
        cleanups = []
        if hasattr(self, "zed_process"):
            cleanups.append(self.stop_zed_subprocess)
        if getattr(self, "output_file", None) is not None:
            cleanups.append(self.close_output_file)
        if getattr(self, "cap", None) is not None:
            cleanups.append(self.cap.release)
        cleanups.append(keyboard.unhook_all)
        for cleanup in cleanups:
            try:
                cleanup()
            except Exception:
                pass

    def start_zed_subprocess(self):
        if self.zed_process is not None and self.zed_process.poll() is None:
            return True
        self.zed_python_path = self.fpga_zed_python
        script = Path(__file__).resolve().with_name("zed_server_fpga.py")
        if not Path(self.zed_python_path).is_file() or not script.is_file():
            return False
        env = os.environ.copy()
        env["ZED_FPGA_HOST"] = self.mapper.host
        env["ZED_FPGA_STEREO_PORT"] = str(self.fpga_stereo_port)
        # ZED uses its own Python environment; never pass the eye-control venv.
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        self.zed_process = subprocess.Popen(
            [self.zed_python_path, "-B", str(script)], env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        time.sleep(1.5)  # Preserve the original asynchronous UDP startup delay.
        return True

def main():
    parser = argparse.ArgumentParser(description="原 improve4 眼控与 ZED 2i，FPGA 计算眼控映射和选中点 XYZ")
    parser.add_argument("--board-host", default="192.168.2.99")
    parser.add_argument("--gaze-port", type=int, default=8765)
    parser.add_argument("--stereo-port", type=int, default=8766)
    parser.add_argument("--zed-python", default=r"E:\zed_workspace\envs\zed\python.exe")
    args = parser.parse_args()
    if not (1 <= args.gaze_port <= 65535 and 1 <= args.stereo_port <= 65535):
        parser.error("端口必须在 1..65535 范围内")
    if not Path(args.zed_python).is_file():
        parser.error("ZED Python 解释器不存在: " + args.zed_python)
    mapper = LegacyFpgaMapper(args.board_host, args.gaze_port, result_max_age_s=None)
    tracker = None
    system = None
    try:
        mapper.start()
        mouse = MouseController()
        tracker = FpgaEyeTracker(mapper, mouse.get_screen_size())
        system = FpgaEyeControlSystem(mapper, tracker, mouse,
                                     args.zed_python, args.stereo_port)
        system.run()
    finally:
        mapper.close()
        if system is not None:
            system.close()
        if tracker is not None:
            tracker.face_mesh.close()


if __name__ == "__main__":
    main()
