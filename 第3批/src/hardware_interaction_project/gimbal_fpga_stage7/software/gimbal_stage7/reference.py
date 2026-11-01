"""Independent integer model of the frozen stage 7 numerical operations.

The model uses unbounded Python integers and math.isqrt. RTL widths must cover
these intermediates for all signed32 native Q8.24 inputs. Source floating point
and real motor behavior are deliberately evaluated as separate evidence tiers.
"""
from dataclasses import dataclass
from enum import IntEnum
import math

MASK32 = (1 << 32) - 1
LENGTH_Q = 1 << 24
ANGLE_Q = 1 << 22
INTERNAL_ANGLE_Q = 1 << 32
DISTANCE_Q = 1 << 20
MAX_PROTOCOL_DELTA = 256 * 6400 - 1
ATAN_DEG_Q32 = tuple(round(math.degrees(math.atan(2.0 ** -i)) * INTERNAL_ANGLE_Q) for i in range(32))


class Status(IntEnum):
    OK = 0
    BAD_OP = 1
    BAD_RANGE = 2
    BAD_SPEED = 3
    BAD_LIMITS = 4
    BEHIND = 5
    HORIZONTAL = 6
    VERTICAL = 7
    RAY = 8
    ANGLE_LIMIT = 9
    STEP_INTERVAL = 10
    DELTA_PROTOCOL = 11


@dataclass
class Result:
    status: int
    out: list


def u32(value):
    return int(value) & MASK32


def s32(value):
    value = u32(value)
    return value - (1 << 32) if value & (1 << 31) else value


def round_even(numerator, denominator):
    """Signed rational nearest integer; exact half ties go to even."""
    if denominator <= 0:
        raise ValueError("positive denominator required")
    magnitude, remainder = divmod(abs(numerator), denominator)
    if remainder * 2 > denominator or (remainder * 2 == denominator and magnitude & 1):
        magnitude += 1
    return -magnitude if numerator < 0 else magnitude


def ceil_div(numerator, denominator):
    return -((-numerator) // denominator)


def cordic_atan2_q32(y_q56, x_q56):
    """32 vectoring iterations, positive denominator, arithmetic right shift."""
    if x_q56 <= 0:
        raise ValueError("stage 7 atan2 requires positive denominator")
    if y_q56 == 0:
        return 0
    if y_q56 == x_q56:
        return 45 * INTERNAL_ANGLE_Q
    if y_q56 == -x_q56:
        return -45 * INTERNAL_ANGLE_Q
    x, y, angle = int(x_q56), int(y_q56), 0
    for i, constant in enumerate(ATAN_DEG_Q32):
        old_x, old_y = x, y
        if old_y >= 0:
            x = old_x + (old_y >> i)
            y = old_y - (old_x >> i)
            angle += constant
        else:
            x = old_x - (old_y >> i)
            y = old_y + (old_x >> i)
            angle -= constant
    return angle


def _failure(status):
    return Result(int(status), [0] * 16)


class QuantizedReference:
    """Stateless: current commanded steps are explicit request inputs."""

    def execute(self, op, inputs):
        if len(inputs) != 16:
            raise ValueError("exactly 16 native words required")
        raw = [u32(value) for value in inputs]
        values = [s32(value) for value in raw]
        if op not in (1, 2):
            return _failure(Status.BAD_OP)
        if op == 2 and any(raw[2:9]):
            return _failure(Status.BAD_RANGE)
        speed = raw[11]
        if not 1 <= speed <= 65535:
            return _failure(Status.BAD_SPEED)
        pmin, pmax, tmin, tmax = values[12:16]
        if pmin > pmax or tmin > tmax:
            return _failure(Status.BAD_LIMITS)

        forward_q20 = ray_q20 = 0
        if op == 1:
            x, y, z = (values[i] - values[i + 3] for i in range(3))
            bx, by, bz = values[6:9]
            if z <= 0:
                return _failure(Status.BEHIND)
            h = x * x + z * z - bx * bx
            if h <= 0:
                return _failure(Status.HORIZONTAL)
            v = h + y * y - by * by
            if v <= 0:
                return _failure(Status.VERTICAL)
            # Q48 radicand <<8 -> sqrt Q28. sqrt is floor, not float.
            f_q28, s_q28 = math.isqrt(h << 8), math.isqrt(v << 8)
            ray_q28 = s_q28 - (bz << 4)
            if ray_q28 <= 0:
                return _failure(Status.RAY)
            pan_q32 = cordic_atan2_q32(x << 32, z << 32) - cordic_atan2_q32(bx << 32, f_q28 << 28)
            tilt_q32 = cordic_atan2_q32(y << 32, f_q28 << 28) - cordic_atan2_q32(by << 32, s_q28 << 28)
            forward_q20, ray_q20 = f_q28 >> 8, ray_q28 >> 8
        else:
            pan_q32, tilt_q32 = values[0] << 10, values[1] << 10
        if not (pmin << 10) <= pan_q32 <= (pmax << 10) or not (tmin << 10) <= tilt_q32 <= (tmax << 10):
            return _failure(Status.ANGLE_LIMIT)

        step_intervals = [(ceil_div(low * 160, 9 * ANGLE_Q), (high * 160) // (9 * ANGLE_Q))
                          for low, high in ((pmin, pmax), (tmin, tmax))]
        if any(lower > upper for lower, upper in step_intervals):
            return _failure(Status.STEP_INTERVAL)
        targets = [max(lower, min(upper, round_even(angle * 160, 9 * INTERNAL_ANGLE_Q)))
                   for angle, (lower, upper) in zip((pan_q32, tilt_q32), step_intervals)]
        delta_pan, delta_tilt = targets[0] - values[9], targets[1] - values[10]
        if max(abs(delta_pan), abs(delta_tilt)) > MAX_PROTOCOL_DELTA:
            return _failure(Status.DELTA_PROTOCOL)
        pan_revs, pan_remaining = divmod(abs(delta_pan), 6400)
        tilt_revs, tilt_remaining = divmod(abs(delta_tilt), 6400)
        dir_pan = 0 if delta_pan == 0 else 1 if delta_pan > 0 else 2
        dir_tilt = 0 if delta_tilt == 0 else 1 if delta_tilt < 0 else 2
        outputs = [round_even(pan_q32, 1 << 10), round_even(tilt_q32, 1 << 10), forward_q20, ray_q20,
                   *targets, delta_pan, delta_tilt, dir_pan, dir_tilt, pan_revs, pan_remaining,
                   tilt_revs, tilt_remaining,
                   round_even(targets[0] * 9 * ANGLE_Q, 160), round_even(targets[1] * 9 * ANGLE_Q, 160)]
        return Result(0, [u32(value) for value in outputs])
