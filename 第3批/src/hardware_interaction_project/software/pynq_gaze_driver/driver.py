"""Single-owner driver for gaze_map_axi ABI 1.0; no PYNQ import required."""
from dataclasses import dataclass
from enum import IntEnum, IntFlag
import math
import time


IP_ID = 0x475A4D31
ABI_VERSION = 0x00010000
MASK32 = 0xFFFFFFFF


class Register(IntEnum):
    CTRL = 0x00
    STATE = 0x04
    IP_ID = 0x08
    ABI_VERSION = 0x0C
    GX_Q16 = 0x10
    GY_Q16 = 0x14
    A0 = 0x18
    A1 = 0x1C
    A2 = 0x20
    A3 = 0x24
    B0 = 0x28
    B1 = 0x2C
    B2 = 0x30
    B3 = 0x34
    WIDTH = 0x38
    HEIGHT = 0x3C
    INPUT_VALID = 0x40
    REQUEST_TAG = 0x44
    RESULT_X = 0x48
    RESULT_Y = 0x4C
    RESULT_STATUS = 0x50
    RESULT_TAG = 0x54
    COMPLETE_COUNT = 0x58
    SCRATCH = 0x5C


class Control(IntFlag):
    START = 1
    ACK_DONE = 2
    ABORT = 4
    CLEAR_REJECT = 8


class State(IntFlag):
    BUSY = 1
    DONE = 2
    RESULT_ERROR = 4
    START_REJECTED = 8


class GazeMapError(RuntimeError):
    pass


class IdentificationError(GazeMapError):
    pass


class BusyError(GazeMapError):
    pass


class UnacknowledgedResultError(GazeMapError):
    pass


class StartRejectedError(GazeMapError):
    pass


class GazeTimeoutError(GazeMapError, TimeoutError):
    pass


class TagMismatchError(GazeMapError):
    pass


class NoResultError(GazeMapError):
    pass


class ProtocolError(GazeMapError):
    pass


class ConfigurationError(GazeMapError, ValueError):
    pass


class ComputationError(GazeMapError):
    def __init__(self, status, tag):
        self.status = status
        self.tag = tag
        super().__init__(f"Kernel returned status={status}, tag={tag}; no valid coordinate")


@dataclass(frozen=True)
class GazeResult:
    x: int
    y: int
    status: int
    tag: int
    complete_count: int


def check_offset(offset):
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > 0x7C or offset % 4:
        raise ValueError("Only aligned 32-bit offsets 0x00..0x7C are allowed")
    return int(offset)


def uint32(value, name="value"):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MASK32:
        raise ConfigurationError(f"{name} must be an unsigned 32-bit integer")
    return value


def signed_word(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not -(1 << 31) <= value < (1 << 31):
        raise ConfigurationError(f"{name} must be a signed 32-bit Q16.16 raw integer")
    return value & MASK32


def positive_seconds(value, name, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value < 0 or (not allow_zero and value == 0):
        raise ValueError(f"{name} must be {'nonnegative' if allow_zero else 'positive'}")
    return float(value)


class GazeMapDriver:
    """One software owner; configure uses raw signed Q16.16 integers.

    A completed result must be read/explicitly acknowledged before another start.
    After a timeout, even a later DONE cannot be read until explicit recovery.
    reset() sends ABORT; it does not clear staged configuration or SCRATCH.
    """
    def __init__(self, mmio, *, timeout_s=1.0, poll_interval_s=0.0005):
        self.mmio = mmio
        self.timeout_s = positive_seconds(timeout_s, "timeout_s")
        self.poll_interval_s = positive_seconds(poll_interval_s, "poll_interval_s", True)
        self._configured = None
        self._pending_tag = None
        self._count_before = None
        self._timed_out = False
        identity = self._read(Register.IP_ID)
        abi = self._read(Register.ABI_VERSION)
        if identity != IP_ID or abi != ABI_VERSION:
            raise IdentificationError(f"Unsupported IP/ABI: 0x{identity:08X}/0x{abi:08X}")

    def _read(self, offset):
        value = self.mmio.read(check_offset(offset))
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MASK32:
            raise ProtocolError("Backend read did not return a uint32")
        return value

    def _write(self, offset, value):
        self.mmio.write(check_offset(offset), uint32(int(value)))

    def _state(self):
        state = self._read(Register.STATE)
        if state & ~0xF:
            raise ProtocolError("STATE contains undefined bits")
        if state & State.BUSY and state & State.DONE:
            raise ProtocolError("BUSY and DONE set simultaneously")
        if state & State.RESULT_ERROR and not state & State.DONE:
            raise ProtocolError("RESULT_ERROR requires DONE")
        return State(state)

    def _idle(self):
        state = self._state()
        if state & State.BUSY:
            raise BusyError("Hardware is busy; request was not submitted")
        if state & State.DONE:
            raise UnacknowledgedResultError("Read/acknowledge the prior result first")
        if state & State.START_REJECTED:
            raise StartRejectedError("Clear the sticky rejection before starting")
        if self._timed_out:
            raise GazeTimeoutError("Prior request timed out; explicitly reset or acknowledge")
        return state

    def configure(self, *, gx_q16, gy_q16, a, b, width, height, valid=1, tag=0):
        a, b = tuple(a), tuple(b)
        if len(a) != 4 or len(b) != 4:
            raise ConfigurationError("a and b each require four coefficients: gx, gy, gx*gy, constant")
        signed = (gx_q16, gy_q16, *a, *b)
        words = [signed_word(v, f"feature/coefficient[{i}]") for i, v in enumerate(signed)]
        words += [uint32(v, name) for v, name in ((width, "width"), (height, "height"), (valid, "valid"), (tag, "tag"))]
        self._idle()
        self._configured = None  # partial backend failure cannot be STARTed
        for index, value in enumerate(words):
            self._write(0x10 + index * 4, value)
        self._configured = {"tag": tag, "width": width, "height": height}

    def start(self):
        self._idle()
        if self._configured is None:
            raise ConfigurationError("configure() must succeed before start()")
        self._count_before = self._read(Register.COMPLETE_COUNT)
        self._pending_tag = self._configured["tag"]
        try:
            self._write(Register.CTRL, Control.START)
        except Exception:
            self._pending_tag = None
            raise
        if self._state() & State.START_REJECTED:
            self._pending_tag = None
            raise StartRejectedError("Hardware rejected START")

    def wait_done(self, *, timeout_s=None):
        if self._timed_out:
            raise GazeTimeoutError("Prior timeout requires explicit recovery")
        if self._pending_tag is None:
            raise NoResultError("No request belongs to this driver")
        duration = self.timeout_s if timeout_s is None else positive_seconds(timeout_s, "timeout_s")
        deadline = time.monotonic() + duration
        while True:
            if time.monotonic() >= deadline:
                self._timed_out = True
                raise GazeTimeoutError("DONE timeout; no coordinate returned; reset/acknowledge explicitly")
            state = self._state()
            if time.monotonic() >= deadline:
                self._timed_out = True
                raise GazeTimeoutError("DONE observed after deadline; late result rejected")
            if state & State.START_REJECTED:
                raise StartRejectedError("Sticky START_REJECTED observed")
            if state & State.DONE:
                if state & State.BUSY:
                    raise ProtocolError("BUSY and DONE set simultaneously")
                return state
            if self.poll_interval_s:
                time.sleep(min(self.poll_interval_s, max(0, deadline - time.monotonic())))

    def read_result(self):
        if self._timed_out:
            raise GazeTimeoutError("Late result after timeout is not a valid response")
        if self._pending_tag is None:
            raise NoResultError("No request belongs to this driver")
        state = self._state()
        if state & State.BUSY:
            raise BusyError("Result is still busy")
        if not state & State.DONE:
            raise NoResultError("DONE is not set; retained output words are stale")
        if state & State.START_REJECTED:
            raise StartRejectedError("Clear rejection explicitly before consuming a result")
        tag = self._read(Register.RESULT_TAG)
        status = self._read(Register.RESULT_STATUS)
        x, y = self._read(Register.RESULT_X), self._read(Register.RESULT_Y)
        count = self._read(Register.COMPLETE_COUNT)
        after = self._state()
        if after != state:
            raise ProtocolError("State changed while reading; a single software owner is required")
        if tag != self._pending_tag:
            raise TagMismatchError(f"Expected tag {self._pending_tag}, received {tag}")
        if count != ((self._count_before + 1) & MASK32):
            raise ProtocolError("Completion count does not belong to this request")
        if bool(state & State.RESULT_ERROR) != bool(status):
            raise ProtocolError("STATE error bit disagrees with RESULT_STATUS")
        if status:
            raise ComputationError(status, tag)
        if x >= self._configured["width"] or y >= self._configured["height"]:
            raise ProtocolError("Successful result is outside configured screen dimensions")
        return GazeResult(x, y, status, tag, count)

    def clear_or_ack(self, *, clear_reject=True):
        if self._state() & State.BUSY:
            raise BusyError("ACK while BUSY is ignored by RTL; use reset() to abort")
        self._write(Register.CTRL, Control.ACK_DONE | (Control.CLEAR_REJECT if clear_reject else 0))
        self._pending_tag = None
        self._count_before = None
        self._timed_out = False

    def reset(self):
        """Software ABORT only: preserve configuration and SCRATCH."""
        self._write(Register.CTRL, Control.ABORT)
        self._pending_tag = None
        self._count_before = None
        self._timed_out = False

    def compute(self, *, timeout_s=None, **configuration):
        self.configure(**configuration)
        self.start()
        self.wait_done(timeout_s=timeout_s)
        result = self.read_result()
        self.clear_or_ack()
        return result
