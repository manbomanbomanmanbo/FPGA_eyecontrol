"""Input types, Q16.16 encoding and validation for stereo_xyz_axi ABI 1.0.

This module validates requests and encodes register words; it never computes XYZ.
"""

from dataclasses import dataclass
from enum import IntEnum
import math

SCALE = 1 << 16
MIN32 = -(1 << 31)
MAX32 = (1 << 31) - 1
MASK32 = (1 << 32) - 1


class Status(IntEnum):
    OK = 0
    INPUT_INVALID = 1
    NONPOSITIVE_INTRINSICS = 2
    IMAGE_BOUNDS = 3
    DEPTH_LIMITS = 4
    DEPTH_RANGE = 5
    OUTPUT_OVERFLOW = 6


SIGNED_FIELDS = ("u", "v", "d", "fx", "fy", "cx", "cy", "baseline", "zmin", "zmax")


def signed32(value, name="value"):
    if type(value) is not int or not MIN32 <= value <= MAX32:
        raise ValueError(f"{name} must be a signed 32-bit raw integer")
    return value


def unsigned32(value, name="value"):
    if type(value) is not int or not 0 <= value <= MASK32:
        raise ValueError(f"{name} must be an unsigned 32-bit integer")
    return value


def to_word(value):
    return signed32(value) & MASK32


def from_word(value):
    value = unsigned32(value)
    return value - (1 << 32) if value & (1 << 31) else value


def quantize_q16(value):
    """Quantize a finite numeric value by truncation toward zero; never wrap."""
    if isinstance(value, (bool, str, bytes)):
        raise ValueError("Q16.16 input must be finite numeric data")
    try:
        number = float(value)
        scaled = number * SCALE
        if not math.isfinite(number) or not math.isfinite(scaled):
            raise ValueError("Q16.16 input must be finite")
        return signed32(math.trunc(scaled), "quantized input")
    except (TypeError, OverflowError) as error:
        raise ValueError("Q16.16 input is not representable") from error




@dataclass(frozen=True)
class RawStereoInput:
    u: int
    v: int
    d: int
    fx: int
    fy: int
    cx: int
    cy: int
    baseline: int
    width: int
    height: int
    zmin: int
    zmax: int
    input_valid: int = 1

    def __post_init__(self):
        for name in SIGNED_FIELDS:
            signed32(getattr(self, name), name)
        for name in ("width", "height"):
            unsigned32(getattr(self, name), name)
        if type(self.input_valid) is not int or self.input_valid not in (0, 1):
            raise ValueError("input_valid must be integer 0 or 1")




def input_status(point):
    """Checks 1..4 only; useful before submission, with no XYZ calculation."""
    if not isinstance(point, RawStereoInput):
        raise TypeError("point must be RawStereoInput")
    if point.input_valid != 1:
        return Status.INPUT_INVALID
    if min(point.fx, point.fy, point.baseline, point.d) <= 0:
        return Status.NONPOSITIVE_INTRINSICS
    if not (1 <= point.width <= 32767 and 1 <= point.height <= 32767):
        return Status.IMAGE_BOUNDS
    if not (0 <= point.u < point.width * SCALE and 0 <= point.v < point.height * SCALE
            and 0 <= point.cx < point.width * SCALE and 0 <= point.cy < point.height * SCALE
            and 0 <= point.u - point.d < point.width * SCALE):
        return Status.IMAGE_BOUNDS
    if point.zmin <= 0 or point.zmax <= point.zmin:
        return Status.DEPTH_LIMITS
    return Status.OK


