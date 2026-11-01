"""Opt-in replacement for camera XYZ arithmetic using the new MMIO core.

The original competition_app.stereo module is read-only. Its calibration/timing
checks, transform validation, result type and world transform are reused. No
software XYZ fallback, camera acquisition, correspondence matching, mouse or
actuator I/O is present. Original software/ must be on the caller's PYTHONPATH.
"""

from dataclasses import dataclass
from typing import Optional

from competition_app import stereo

from .driver import ComputationError, RequestTimeout, StereoDriverError, StereoMMIODriver
from .oracle import RawStereoInput, SCALE, Status, input_status, quantize_q16


@dataclass(frozen=True)
class StereoExecutionResult:
    point: stereo.StereoPointResult
    evidence: str
    request_tag: Optional[int] = None
    completion_count: Optional[int] = None
    kernel_status: Optional[int] = None


_KERNEL_REASONS = {
    Status.INPUT_INVALID: "fpga_input_invalid",
    Status.NONPOSITIVE_INTRINSICS: "fpga_intrinsics_invalid",
    Status.IMAGE_BOUNDS: "fpga_image_bounds_invalid",
    Status.DEPTH_LIMITS: "fpga_depth_limits_invalid",
    Status.DEPTH_RANGE: "depth_out_of_range",
    Status.OUTPUT_OVERFLOW: "fpga_output_overflow",
}


def triangulate_fpga(u, v, disparity_px, calibration, timing, *, driver,
                     world_transform=None, roi_valid=True, max_age_ms=250,
                     max_sync_delta_ms=20, min_depth_m=0.05, max_depth_m=20.0,
                     request_tag=None):
    """Validate metadata, submit raw arithmetic, then transform the MMIO XYZ.

    A `verified` flag is a caller assertion, not evidence created by this code.
    The output evidence names the actual backend, including offline mock mode.
    """
    if not isinstance(driver, StereoMMIODriver):
        raise TypeError("driver must be an explicitly constructed StereoMMIODriver")

    def rejected(reason, *, tag=None, count=None, status=None):
        return StereoExecutionResult(stereo.StereoPointResult(False, reason), driver.evidence, tag, count, status)

    reason = stereo._calibration_reason(calibration) or stereo._timing_reason(timing, max_age_ms, max_sync_delta_ms)
    if reason:
        return rejected(reason)
    if roi_valid is not True:
        return rejected("roi_invalid")
    if not stereo._finite(u) or not stereo._finite(v) or not (0 <= u < calibration.width and 0 <= v < calibration.height):
        return rejected("pixel_outside_image")
    if not stereo._finite(disparity_px) or disparity_px <= 0:
        return rejected("disparity_not_positive_finite")
    if not (0 <= u - disparity_px < calibration.width):
        return rejected("right_correspondence_outside_image")
    if (not stereo._finite(min_depth_m) or not stereo._finite(max_depth_m) or
            not 0 < min_depth_m < max_depth_m):
        return rejected("depth_limits_invalid")
    try:
        values = [quantize_q16(value) for value in
                  (u, v, disparity_px, calibration.fx, calibration.fy, calibration.cx,
                   calibration.cy, calibration.baseline_m, min_depth_m, max_depth_m)]
        point = RawStereoInput(*values[:8], calibration.width, calibration.height, *values[8:])
    except (ValueError, TypeError, OverflowError):
        return rejected("fpga_q16_unrepresentable")
    if input_status(point) != Status.OK:
        return rejected("fpga_q16_input_invalid")
    try:
        completed = driver.compute(point, request_tag=request_tag)
    except ComputationError as error:
        return rejected(_KERNEL_REASONS[Status(error.status)], tag=error.tag, count=error.count, status=int(error.status))
    except RequestTimeout:
        return rejected("fpga_timeout_requires_recovery")
    except StereoDriverError:
        return rejected("fpga_driver_error_requires_recovery")
    camera = (completed.x / SCALE, completed.y / SCALE, completed.z / SCALE)
    # Apply the original float API's depth limits as well as the quantized core
    # limits: rounding a threshold down must not silently widen its acceptance.
    if not min_depth_m <= camera[2] <= max_depth_m:
        return rejected("depth_out_of_range", tag=completed.tag, count=completed.count, status=0)
    transform_error = stereo.transform_reason(world_transform, "camera_rectified_left", "world")
    world = None if transform_error else stereo._transform(camera, world_transform)
    if world is not None and not all(stereo._finite(value) for value in world):
        world, transform_error = None, "world_point_nonfinite"
    result = stereo.StereoPointResult(
        True, "ok", camera, world, transform_error or "ok", float(disparity_px),
        calibration.calibration_id,
        abs(timing.left_exposure_ns - timing.right_exposure_ns) / 1_000_000,
        (timing.now_monotonic_ns - min(timing.left_exposure_ns, timing.right_exposure_ns)) / 1_000_000)
    return StereoExecutionResult(result, completed.evidence, completed.tag, completed.count, int(completed.status))
