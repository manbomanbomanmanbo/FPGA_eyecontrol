"""Constants from the frozen stage-2 contract; no gimbal math implementation."""
from enum import IntEnum

ID = 0x474D4231
ABI = 0x00010000
BASE = 0x43C30000
EVIDENCE = 'pynq_mmio_result'
INPUT_COUNT = OUTPUT_COUNT = 16


class Register(IntEnum):
    CTRL = 0x00
    STATE = 0x04
    ID = 0x08
    ABI = 0x0C
    OP = 0x10
    IN0 = 0x14
    TAG = 0x54
    OUT0 = 0x60
    STATUS = 0xA0
    RESULT_TAG = 0xA4
    COMPLETE_COUNT = 0xA8


class Op(IntEnum):
    POINT = 1
    ANGLE = 2


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
