"""Validated-input stereo geometry only: no camera, motor or PWM I/O.

Pixel coordinates and disparity must refer to the same rectified left image.
Rectification must give matching horizontal principal points (zero-disparity
rectification); otherwise correct the principal-point offset before this API.
Disparity is in pixels (convert vendor fixed-point formats before calling).
Camera axes are +x image right, +y image down, +z forward; distance is metres.
No default camera-to-world or world-to-base transform is assumed.

The caller is responsible for evidence behind the explicit `verified` flags:
matrix checks prove a proper rigid transform, not a correct physical calibration.
Exposure synchronization requires an actual device timestamp / trigger contract;
close host receive times alone do not establish it. Numeric geometry tests and
synthetic_fixture() are not measurements from real stereo or gimbal hardware.
"""

from dataclasses import asdict, dataclass
import math
import statistics
from typing import Optional, Tuple


Vec3 = Tuple[float, float, float]
Mat4 = Tuple[Tuple[float, float, float, float], ...]


def _finite(value):
    try:
        return not isinstance(value, (bool, str, bytes)) and math.isfinite(float(value))
    except (ValueError, TypeError, OverflowError):
        return False


@dataclass(frozen=True)
class StereoCalibration:
    fx: float
    fy: float
    cx: float
    cy: float
    baseline_m: float
    width: int
    height: int
    calibration_id: str
    verified: bool = False
    rectified: bool = False


@dataclass(frozen=True)
class StereoTiming:
    left_exposure_ns: int
    right_exposure_ns: int
    now_monotonic_ns: int
    synchronization_verified: bool = False


@dataclass(frozen=True)
class RigidTransform:
    matrix: Mat4
    calibration_id: str
    source_frame: str
    target_frame: str
    verified: bool = False


@dataclass(frozen=True)
class StereoPointResult:
    valid: bool
    reason: str
    camera_xyz_m: Optional[Vec3] = None
    world_xyz_m: Optional[Vec3] = None
    world_reason: str = "not_evaluated"
    disparity_px: Optional[float] = None
    calibration_id: Optional[str] = None
    exposure_delta_ms: Optional[float] = None
    sample_age_ms: Optional[float] = None


@dataclass(frozen=True)
class DisparityEstimate:
    valid: bool
    reason: str
    disparity_px: Optional[float] = None
    valid_pixels: int = 0
    total_pixels: int = 0
    valid_fraction: float = 0.0
    roi: Optional[Tuple[int, int, int, int]] = None


@dataclass(frozen=True)
class AimResult:
    valid: bool
    reason: str
    base_xyz_m: Optional[Vec3] = None
    pan_deg: Optional[float] = None
    tilt_deg: Optional[float] = None
    range_m: Optional[float] = None
    command_emitted: bool = False


def _calibration_reason(calibration):
    if not isinstance(calibration, StereoCalibration) or calibration.verified is not True:
        return "stereo_uncalibrated"
    if calibration.rectified is not True:
        return "stereo_not_rectified"
    if not isinstance(calibration.calibration_id, str) or not calibration.calibration_id.strip():
        return "calibration_id_missing"
    if any(not _finite(v) for v in (calibration.fx, calibration.fy, calibration.cx,
                                    calibration.cy, calibration.baseline_m)):
        return "intrinsics_invalid"
    if min(calibration.fx, calibration.fy, calibration.baseline_m) <= 0:
        return "intrinsics_invalid"
    if any(type(v) is not int or v <= 0 for v in (calibration.width, calibration.height)):
        return "image_size_invalid"
    if not (0 <= calibration.cx < calibration.width and 0 <= calibration.cy < calibration.height):
        return "principal_point_outside_image"
    return None


def transform_reason(transform, source_frame, target_frame):
    """Check verification metadata, frame direction and rigid matrix properties."""
    if transform is None:
        return "transform_missing"
    if not isinstance(transform, RigidTransform) or transform.verified is not True:
        return "transform_unverified"
    if not isinstance(transform.calibration_id, str) or not transform.calibration_id.strip():
        return "transform_calibration_id_missing"
    if transform.source_frame != source_frame or transform.target_frame != target_frame:
        return "transform_frame_mismatch"
    try:
        matrix = transform.matrix
        if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
            return "transform_shape_invalid"
        if any(not _finite(x) for row in matrix for x in row):
            return "transform_nonfinite"
        if any(abs(matrix[3][i] - (1.0 if i == 3 else 0.0)) > 1e-6 for i in range(4)):
            return "transform_homogeneous_row_invalid"
        rotation = [row[:3] for row in matrix[:3]]
        for i in range(3):
            for j in range(3):
                dot = sum(rotation[k][i] * rotation[k][j] for k in range(3))
                if abs(dot - (1.0 if i == j else 0.0)) > 1e-6:
                    return "transform_rotation_not_orthonormal"
        a, b, c = rotation
        determinant = (a[0] * (b[1] * c[2] - b[2] * c[1]) -
                       a[1] * (b[0] * c[2] - b[2] * c[0]) +
                       a[2] * (b[0] * c[1] - b[1] * c[0]))
        if abs(determinant - 1.0) > 1e-6:
            return "transform_rotation_not_proper"
    except (TypeError, ValueError, IndexError):
        return "transform_invalid"
    return None


def _transform(point, transform):
    return tuple(sum(transform.matrix[i][j] * point[j] for j in range(3)) +
                 transform.matrix[i][3] for i in range(3))


def _timing_reason(timing, max_age_ms, max_sync_delta_ms):
    if not isinstance(timing, StereoTiming) or timing.synchronization_verified is not True:
        return "exposure_synchronization_unverified"
    if not _finite(max_age_ms) or max_age_ms <= 0 or not _finite(max_sync_delta_ms) or max_sync_delta_ms < 0:
        return "timing_thresholds_invalid"
    stamps = (timing.left_exposure_ns, timing.right_exposure_ns, timing.now_monotonic_ns)
    if any(type(v) is not int or v <= 0 for v in stamps):
        return "exposure_timestamp_invalid"
    if max(stamps[:2]) > timing.now_monotonic_ns:
        return "exposure_timestamp_in_future"
    if abs(stamps[0] - stamps[1]) > max_sync_delta_ms * 1_000_000:
        return "exposure_not_synchronized"
    if timing.now_monotonic_ns - min(stamps[:2]) > max_age_ms * 1_000_000:
        return "stale_stereo_frame"
    return None


def triangulate_pixel(u, v, disparity_px, calibration, timing, *,
                      world_transform=None, roi_valid=True, max_age_ms=250,
                      max_sync_delta_ms=20, min_depth_m=0.05, max_depth_m=20.0):
    """Triangulate already-validated rectified disparity, fail closed on inputs.

    `valid` describes the camera-space point. Missing world calibration leaves
    camera_xyz_m available and world_xyz_m None with an explicit world_reason.
    Input pixel should be the left-image coordinate of the chosen disparity.
    """
    reason = _calibration_reason(calibration) or _timing_reason(timing, max_age_ms, max_sync_delta_ms)
    if reason:
        return StereoPointResult(False, reason)
    if roi_valid is not True:
        return StereoPointResult(False, "roi_invalid")
    if not _finite(u) or not _finite(v) or not (0 <= u < calibration.width and 0 <= v < calibration.height):
        return StereoPointResult(False, "pixel_outside_image")
    if not _finite(disparity_px) or disparity_px <= 0:
        return StereoPointResult(False, "disparity_not_positive_finite")
    if not (0 <= u - disparity_px < calibration.width):
        return StereoPointResult(False, "right_correspondence_outside_image")
    if (not _finite(min_depth_m) or not _finite(max_depth_m) or
            not 0 < min_depth_m < max_depth_m):
        return StereoPointResult(False, "depth_limits_invalid")
    z = calibration.fx * calibration.baseline_m / disparity_px
    if not _finite(z) or not min_depth_m <= z <= max_depth_m:
        return StereoPointResult(False, "depth_out_of_range")
    camera = ((u - calibration.cx) * z / calibration.fx,
              (v - calibration.cy) * z / calibration.fy, z)
    if not all(_finite(x) for x in camera):
        return StereoPointResult(False, "triangulation_nonfinite")
    transform_error = transform_reason(world_transform, "camera_rectified_left", "world")
    world = None if transform_error else _transform(camera, world_transform)
    if world is not None and not all(_finite(x) for x in world):
        world, transform_error = None, "world_point_nonfinite"
    return StereoPointResult(
        True, "ok", camera, world, transform_error or "ok", float(disparity_px),
        calibration.calibration_id,
        abs(timing.left_exposure_ns - timing.right_exposure_ns) / 1_000_000,
        (timing.now_monotonic_ns - min(timing.left_exposure_ns, timing.right_exposure_ns)) / 1_000_000)


def estimate_roi_disparity(disparity_map, roi, *, valid_mask=None,
                           min_valid_fraction=0.6, min_valid_pixels=9,
                           min_disparity_px=0.1, max_disparity_px=None):
    """Median of valid disparity values in x0,y0,x1,y1 (exclusive upper bounds).

    The explicit mask must come from the correspondence / consistency checker;
    positive numbers alone do not establish valid stereo matches. This summary
    does not resolve mixed foreground/background surfaces in the same ROI.
    """
    try:
        height = len(disparity_map)
        width = len(disparity_map[0]) if height else 0
        if not height or not width or any(len(row) != width for row in disparity_map):
            return DisparityEstimate(False, "disparity_map_invalid")
        if (len(roi) != 4 or any(type(v) is not int for v in roi) or
                not (0 <= roi[0] < roi[2] <= width and 0 <= roi[1] < roi[3] <= height)):
            return DisparityEstimate(False, "roi_invalid")
        roi = tuple(roi)
        total = (roi[2] - roi[0]) * (roi[3] - roi[1])
        if valid_mask is None:
            return DisparityEstimate(False, "validity_mask_missing", total_pixels=total, roi=roi)
        if len(valid_mask) != height or any(len(row) != width for row in valid_mask):
            return DisparityEstimate(False, "validity_mask_shape_invalid", total_pixels=total, roi=roi)
        if (not _finite(min_valid_fraction) or not 0 < min_valid_fraction <= 1 or
                type(min_valid_pixels) is not int or min_valid_pixels <= 0 or
                not _finite(min_disparity_px) or min_disparity_px <= 0 or
                (max_disparity_px is not None and (not _finite(max_disparity_px) or
                                                  max_disparity_px <= min_disparity_px))):
            return DisparityEstimate(False, "disparity_thresholds_invalid", total_pixels=total, roi=roi)
        accepted = []
        for y in range(roi[1], roi[3]):
            for x in range(roi[0], roi[2]):
                value, mask = disparity_map[y][x], valid_mask[y][x]
                # Accept conventional bool or numeric 0/1 masks, never truthy NaN.
                mask_valid = mask is True or (_finite(mask) and float(mask) == 1.0)
                if (mask_valid and _finite(value) and value >= min_disparity_px and
                        (max_disparity_px is None or value <= max_disparity_px)):
                    accepted.append(float(value))
        fraction = len(accepted) / total
        if len(accepted) < min_valid_pixels or fraction < min_valid_fraction:
            return DisparityEstimate(False, "insufficient_valid_disparity", None,
                                     len(accepted), total, fraction, roi)
        return DisparityEstimate(True, "ok", statistics.median(accepted),
                                 len(accepted), total, fraction, roi)
    except (TypeError, ValueError, IndexError, OverflowError):
        return DisparityEstimate(False, "disparity_map_invalid")


def aim_angles(world_xyz_m, world_to_base, *, pan_limits_deg=(-90.0, 90.0),
               tilt_limits_deg=(-45.0, 45.0), min_range_m=0.05):
    """Candidate angles only; base +x right, +y up, +z forward.

    Pan is atan2(x,z); tilt is atan2(y,hypot(x,z)). Positive pan turns right,
    positive tilt turns up. Out-of-range candidates are rejected, not clipped.
    These angles are not servo pulse widths, joint commands or a motion plan.
    """
    error = transform_reason(world_to_base, "world", "gimbal_base")
    if error:
        return AimResult(False, error)
    try:
        if len(world_xyz_m) != 3 or not all(_finite(x) for x in world_xyz_m):
            return AimResult(False, "world_point_invalid")
        if (len(pan_limits_deg) != 2 or len(tilt_limits_deg) != 2 or
                not all(_finite(x) for x in (*pan_limits_deg, *tilt_limits_deg, min_range_m)) or
                not -180 <= pan_limits_deg[0] < pan_limits_deg[1] <= 180 or
                not -90 <= tilt_limits_deg[0] < tilt_limits_deg[1] <= 90 or min_range_m <= 0):
            return AimResult(False, "aim_limits_invalid")
        base = _transform(world_xyz_m, world_to_base)
        if not all(_finite(x) for x in base):
            return AimResult(False, "base_point_nonfinite")
        distance = math.sqrt(sum(x * x for x in base))
        if not math.isfinite(distance) or distance < min_range_m:
            return AimResult(False, "target_range_invalid")
        if base[2] <= 0:
            return AimResult(False, "target_not_in_front")
        pan = math.degrees(math.atan2(base[0], base[2]))
        tilt = math.degrees(math.atan2(base[1], math.hypot(base[0], base[2])))
        if not (pan_limits_deg[0] <= pan <= pan_limits_deg[1] and
                tilt_limits_deg[0] <= tilt <= tilt_limits_deg[1]):
            return AimResult(False, "angle_out_of_range")
        return AimResult(True, "candidate_only", base, pan, tilt, distance)
    except (TypeError, ValueError, OverflowError):
        return AimResult(False, "world_point_invalid")


def synthetic_fixture():
    """Explicit synthetic fixture for GUI/schema preview, with zero device I/O."""
    calibration = StereoCalibration(400.0, 400.0, 320.0, 240.0, 0.1, 640, 480,
                                    "synthetic-stereo-v1", verified=True, rectified=True)
    timing = StereoTiming(1_000_000_000, 1_001_000_000, 1_100_000_000, True)
    camera_to_world = RigidTransform(
        ((1., 0., 0., 1.), (0., 1., 0., 2.), (0., 0., 1., 3.), (0., 0., 0., 1.)),
        "synthetic-world-v1", "camera_rectified_left", "world", True)
    world_to_base = RigidTransform(
        ((1., 0., 0., -1.), (0., -1., 0., 2.), (0., 0., -1., 7.), (0., 0., 0., 1.)),
        "synthetic-base-v1", "world", "gimbal_base", True)
    point = triangulate_pixel(340., 250., 20., calibration, timing, world_transform=camera_to_world)
    aim = aim_angles(point.world_xyz_m, world_to_base)
    return {"evidence": "synthetic_geometry_not_measured", "hardware_connected": False,
            "command_emitted": False,
            "input": {"u": 340.0, "v": 250.0, "disparity_px": 20.0},
            "calibration": asdict(calibration), "timing": asdict(timing),
            "camera_to_world": asdict(camera_to_world), "world_to_base": asdict(world_to_base),
            "point": asdict(point), "aim": asdict(aim)}
