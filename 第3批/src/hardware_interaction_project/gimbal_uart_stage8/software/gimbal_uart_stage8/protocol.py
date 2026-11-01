"""Stage8 register and numeric transport contract (no UART packet builder)."""
from enum import IntEnum

ID = 0x47554131
ABI = 0x00010000
BASE = 0x43C40000
EVIDENCE = "pynq_gimbal_uart_mmio_result"
TTL33_PROFILE = 0x334C5454


class Control(IntEnum):
    START = 1
    ACK = 2
    RESET = 4
    ARM = 8
    DISARM = 16
    STOP = 32
    SEED = 64
    HEARTBEAT = 128


class Register(IntEnum):
    CTRL = 0x00
    STATE = 0x04
    ID = 0x08
    ABI = 0x0c
    OP = 0x10
    IN0 = 0x14
    TAG = 0x54
    OUT0 = 0x60
    STATUS = 0xa0
    RESULT_TAG = 0xa4
    COMPLETE_COUNT = 0xa8
    RX_INVALID = 0xac
    MODE = 0xb0
    FLAGS = 0xb4
    PROFILE = 0xb8
    WATCHDOG_AGE = 0xbc
    PAN_STEPS = 0xc0
    TILT_STEPS = 0xc4
    PAN_ANGLE = 0xc8
    TILT_ANGLE = 0xcc
    TX_FRAMES = 0xd0
    TX_BYTES = 0xd4
    RX_BYTES = 0xd8
    RX_ERRORS = 0xdc
    LAST_REPLY = 0xe0
    VALID_REPLIES = 0xe4
    EPOCH = 0xe8
    STOP_STATUS = 0xec
    MONITOR_COUNT = 0xf0
    MONITOR_ERRORS = 0xf4
    TEST_REPLY = 0xf8
    TEST_INJECT = 0xfc
    MONITOR_RING = 0x100
    LAST_START = 0x150
    LAST_END = 0x158
    PREVIOUS_END = 0x160
    LAST_GAP = 0x168


def word(value):
    if type(value) is not int or not 0 <= value <= 0xffffffff:
        raise ValueError("Expected uint32")
    return value


def signed(value):
    return value - (1 << 32) if value & (1 << 31) else value
