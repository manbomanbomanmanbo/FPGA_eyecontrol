"""ZED SDK disparity acquisition with selected-point XYZ computed by PYNQ.

The original display, UDP commands, point validation, units and angle output
remain in zed_server/main. No host XYZ calculation or software fallback is used.
"""

from dataclasses import dataclass
import math
import os
from pathlib import Path
import sys
import threading


_PROJECT = Path(__file__).resolve().parents[2]
for _relative in ("software", "stereo_fpga_stage4/software"):
    _path = str(_PROJECT / _relative)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from stereo_fpga_stage4.driver import StereoDriverError
from stereo_fpga_stage4.oracle import RawStereoInput, SCALE, Status, input_status, quantize_q16
from stereo_fpga_stage4.tcp import StereoTCPClient
from zed_server import ZEDServer, cv2, zed


@dataclass(frozen=True)
class _Frame:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    baseline_m: float
    minimum_m: float
    maximum_m: float


class _FPGAPointCloud:
    """Keep the original get_world_coord() point-cloud query boundary."""

    def __init__(self, server):
        self.server = server

    def get_value(self, ix, iy):
        return self.server._get_fpga_point(ix, iy)


class ZEDServerFPGA(ZEDServer):
    def __init__(self, port=9999):
        super().__init__(port=port)
        self._frame_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._disparity = zed.Mat()
        self._frame = None
        self._minimum_m = 1.0 / SCALE
        self._maximum_m = 40.0
        self.point_cloud = _FPGAPointCloud(self)
        self._client = StereoTCPClient(
            host=os.environ.get("ZED_FPGA_HOST", "192.168.2.99"),
            port=int(os.environ.get("ZED_FPGA_STEREO_PORT", "8766")),
            timeout_s=0.6,
        )

    def _failure(self, reason):
        return zed.ERROR_CODE.FAILURE, None

    def init_camera(self):
        if not super().init_camera():
            return False
        try:
            parameters = self.cam.get_init_parameters()
            if parameters.coordinate_units != zed.UNIT.METER:
                raise ValueError("camera units must remain METER")
            if parameters.coordinate_system != zed.COORDINATE_SYSTEM.IMAGE:
                raise ValueError("camera coordinate system must remain IMAGE (Y down)")
            minimum = float(parameters.depth_minimum_distance)
            maximum = float(parameters.depth_maximum_distance)
            if not math.isfinite(minimum) or not math.isfinite(maximum):
                raise ValueError("nonfinite SDK depth limits")
            # The SDK's negative/zero value means its camera-specific default.
            # SDK disparity already carries that filtering. Do not impose 0.05 m.
            self._minimum_m = max(minimum, 1.0 / SCALE)
            self._maximum_m = maximum
            if not self._minimum_m < maximum or maximum != 40.0:
                raise ValueError("expected the original 40 m maximum depth")
            with self._frame_lock:
                self._frame = None
            return True
        except Exception:
            if self.cam is not None:
                self.cam.close()
                self.cam = None
            return False

    def update_coordinate_measure(self, rgb_error=None):
        """Called by the original display loop immediately after a good grab."""
        with self._frame_lock:
            # Invalidate first; neither SDK failure nor malformed metadata may
            # leave a previous usable frame behind.
            self._frame = None
            try:
                if rgb_error != zed.ERROR_CODE.SUCCESS:
                    raise RuntimeError(f"RGB retrieval failed or unconfirmed: {rgb_error}")
                width, height = int(self.orig_w), int(self.orig_h)
                if (self.depth_image.get_width(), self.depth_image.get_height()) != (width, height):
                    raise ValueError("RGB and native image resolutions differ")
                resolution = zed.Resolution(width, height)
                error = self.cam.retrieve_measure(
                    self._disparity, zed.MEASURE.DISPARITY, zed.MEM.CPU, resolution
                )
                if error != zed.ERROR_CODE.SUCCESS:
                    raise RuntimeError(f"DISPARITY retrieval failed: {error}")
                if (self._disparity.get_width(), self._disparity.get_height()) != (width, height):
                    raise ValueError("DISPARITY and RGB resolutions differ")
                information = self.cam.get_camera_information(resolution)
                calibration = information.camera_configuration.calibration_parameters
                intrinsics = calibration.left_cam
                values = tuple(float(value) for value in (
                    intrinsics.fx, intrinsics.fy, intrinsics.cx, intrinsics.cy,
                    calibration.get_camera_baseline(),
                ))
                fx, fy, cx, cy, baseline = values
                if (not all(math.isfinite(value) for value in values)
                        or min(fx, fy, baseline) <= 0
                        or not 0 <= cx < width or not 0 <= cy < height):
                    raise ValueError("invalid rectified camera calibration")
                # The disparity core assumes matching rectified intrinsics,
                # including the principal points (not raw lens calibration).
                right = calibration.right_cam
                right_values = (right.fx, right.fy, right.cx, right.cy)
                if any(quantize_q16(left) != quantize_q16(right_value)
                       for left, right_value in zip(values[:4], right_values)):
                    raise ValueError("rectified left/right camera intrinsics differ")
                self._frame = _Frame(
                    width, height, fx, fy, cx, cy, baseline,
                    self._minimum_m, self._maximum_m,
                )
            except Exception:
                pass

    def _get_fpga_point(self, ix, iy):
        try:
            with self._frame_lock:
                frame = self._frame
                if frame is None:
                    return self._failure("no valid disparity frame")
                if not (0 <= ix < frame.width and 0 <= iy < frame.height):
                    return self._failure("selected pixel outside disparity image")
                error, sdk_disparity = self._disparity.get_value(ix, iy)
                if error != zed.ERROR_CODE.SUCCESS:
                    return self._failure(f"DISPARITY pixel query failed: {error}")
                sdk_disparity = float(sdk_disparity)
                if not math.isfinite(sdk_disparity) or sdk_disparity >= 0:
                    return self._failure("SDK disparity is not finite and negative")
                # Copy scalar data while protected; do not retain a Mat view or
                # hold the frame lock while connecting/waiting for the FPGA.
                disparity = -sdk_disparity
            values = [quantize_q16(value) for value in (
                ix, iy, disparity, frame.fx, frame.fy, frame.cx, frame.cy,
                frame.baseline_m, frame.minimum_m, frame.maximum_m,
            )]
            request = RawStereoInput(
                *values[:8], frame.width, frame.height, *values[8:]
            )
            status = input_status(request)
            if status != Status.OK:
                return self._failure(f"FPGA input rejected: {status.name}")
            with self._request_lock:
                newly_connected = not self._client.connected
                if newly_connected:
                    self._client.connect()
                if newly_connected or self._client.needs_recovery:
                    self._client.recover()
                completed = self._client.compute(request)
                if completed.status != Status.OK or completed.evidence != "pynq_mmio_result":
                    raise StereoDriverError("result did not confirm successful PYNQ MMIO execution")
                coordinates = [completed.x / SCALE, completed.y / SCALE, completed.z / SCALE]
                return zed.ERROR_CODE.SUCCESS, coordinates + [0.0]
        except Exception as error:
            return self._failure(f"{type(error).__name__}: {error}")

    def run(self):
        try:
            super().run()
        finally:
            self.running = False
            with self._frame_lock:
                self._frame = None
            with self._request_lock:
                try:
                    self._client.close()
                except Exception:
                    pass
            if self.cam is not None:
                try:
                    if self.cam.is_opened():
                        self.cam.close()
                except Exception:
                    pass
                self.cam = None
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass


if __name__ == "__main__":
    ZEDServerFPGA().run()
