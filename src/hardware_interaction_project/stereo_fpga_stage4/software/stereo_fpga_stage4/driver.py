"""Fail-closed, single-owner MMIO driver. No PYNQ import or automatic recovery."""

from dataclasses import dataclass
from enum import IntEnum, IntFlag
import math
import threading
import time
import weakref

from .oracle import MASK32, RawStereoInput, Status, from_word, to_word, unsigned32

IP_ID = 0x53545831
ABI_VERSION = 0x00010000


class Register(IntEnum):
    CTRL = 0x00
    STATE = 0x04
    ID = 0x08
    ABI = 0x0C
    U = 0x10
    V = 0x14
    D = 0x18
    FX = 0x1C
    FY = 0x20
    CX = 0x24
    CY = 0x28
    B = 0x2C
    WIDTH = 0x30
    HEIGHT = 0x34
    ZMIN = 0x38
    ZMAX = 0x3C
    INPUT_VALID = 0x40
    REQUEST_TAG = 0x44
    X = 0x48
    Y = 0x4C
    Z = 0x50
    STATUS = 0x54
    TAG = 0x58
    COUNT = 0x5C
    SCRATCH = 0x60


class Control(IntFlag):
    START = 1
    ACK = 2
    ABORT = 4
    CLEAR_REJECT = 8


class State(IntFlag):
    BUSY = 1
    DONE = 2
    ERROR = 4
    REJECT = 8


class StereoDriverError(RuntimeError):
    pass


class IdentificationError(StereoDriverError):
    pass


class ProtocolError(StereoDriverError):
    pass


class MMIOAccessError(StereoDriverError):
    pass


class ConcurrentOwnerError(StereoDriverError):
    pass


class RecoveryRequired(StereoDriverError):
    pass


class RequestTimeout(StereoDriverError, TimeoutError):
    pass


class ComputationError(StereoDriverError):
    def __init__(self, status, tag, count):
        self.status, self.tag, self.count = status, tag, count
        super().__init__(f"stereo core rejected request: {Status(status).name} (tag={tag}, count={count})")


@dataclass(frozen=True)
class CompletedResult:
    x: int
    y: int
    z: int
    status: Status
    tag: int
    count: int
    evidence: str


def check_offset(offset):
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > 0x7C or offset % 4:
        raise ValueError("MMIO offset must be aligned within 0x00..0x7C")
    return int(offset)


def _seconds(value, name, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value < 0 or (not allow_zero and value == 0):
        raise ValueError(f"{name} is out of range")
    return float(value)


class StereoMMIODriver:
    """A backend object has exactly one driver owner.

    The lock rejects reentrant/concurrent calls. Use one backend for each physical
    IP; two independently constructed backend objects for one IP are forbidden by
    the caller contract. Timeout/protocol/I/O failures require explicit recover().
    A late DONE from a timed-out request is never returned by compute().
    """

    _owners = weakref.WeakKeyDictionary()
    _owners_lock = threading.Lock()

    def __init__(self, backend, *, timeout_s=1.0, poll_interval_s=0.0005,
                 clock=time.monotonic, sleep=time.sleep):
        self.backend = backend
        self.timeout_s = _seconds(timeout_s, "timeout_s")
        self.poll_interval_s = _seconds(poll_interval_s, "poll_interval_s", True)
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._needs_recovery = False
        self._next_tag = 1
        with self._owners_lock:
            owner = self._owners.get(backend)
            if owner is not None and owner() is not None:
                raise ConcurrentOwnerError("Backend already has a live stereo driver")
            self._identify()
            self._owners[backend] = weakref.ref(self)

    @property
    def evidence(self):
        return getattr(self.backend, "evidence", "unclassified_mmio_backend")

    @property
    def needs_recovery(self):
        return self._needs_recovery

    def _read(self, register):
        try:
            value = self.backend.read(check_offset(register))
        except Exception as error:
            raise MMIOAccessError(f"MMIO read failed at 0x{int(register):02X}") from error
        try:
            return unsigned32(value, "backend read")
        except ValueError as error:
            raise ProtocolError("Backend read must return a uint32 word") from error

    def _write(self, register, value):
        offset, word = check_offset(register), unsigned32(int(value))
        try:
            self.backend.write(offset, word)
        except Exception as error:
            raise MMIOAccessError(f"MMIO write failed at 0x{offset:02X}") from error

    def _identify(self):
        identity, abi = self._read(Register.ID), self._read(Register.ABI)
        if identity != IP_ID or abi != ABI_VERSION:
            raise IdentificationError(f"Unexpected stereo IP/ABI: 0x{identity:08X}/0x{abi:08X}")

    def _state(self):
        state = self._read(Register.STATE)
        if state & ~0xF or (state & State.BUSY and state & State.DONE):
            raise ProtocolError("Invalid stereo STATE bits")
        if state & State.ERROR and not state & State.DONE:
            raise ProtocolError("ERROR requires DONE")
        return State(state)

    def compute(self, point, *, request_tag=None, timeout_s=None):
        """Submit raw inputs and return one identified, tagged, counted result."""
        if not isinstance(point, RawStereoInput):
            raise TypeError("point must be RawStereoInput")
        tag = self._next_tag if request_tag is None else unsigned32(request_tag, "request_tag")
        duration = self.timeout_s if timeout_s is None else _seconds(timeout_s, "timeout_s")
        if not self._lock.acquire(blocking=False):
            raise ConcurrentOwnerError("Stereo driver is already executing or recovering")
        try:
            if self._needs_recovery:
                raise RecoveryRequired("Prior timeout/protocol/I/O failure requires explicit recover()")
            self._identify()
            if self._state() != 0:
                self._needs_recovery = True
                raise RecoveryRequired("IP is not idle/clear; recover() must discard unowned state")
            words = [to_word(getattr(point, name)) for name in ("u", "v", "d", "fx", "fy", "cx", "cy", "baseline")]
            words.extend((point.width, point.height, to_word(point.zmin), to_word(point.zmax), point.input_valid, tag))
            self._needs_recovery = True  # even a partial write or lost START response fails closed
            for index, word in enumerate(words):
                self._write(0x10 + 4 * index, word)
            count_before = self._read(Register.COUNT)
            self._write(Register.CTRL, Control.START)
            deadline = self._clock() + duration
            while True:
                if self._clock() > deadline:
                    raise RequestTimeout("Stereo request timed out; explicit recover() must discard late completion")
                state = self._state()
                if state & State.REJECT:
                    raise ProtocolError("Hardware rejected START or a competing write")
                if state & State.DONE:
                    if self._clock() > deadline:
                        raise RequestTimeout("Stereo completion arrived after deadline")
                    break
                if not state & State.BUSY:
                    raise ProtocolError("START produced neither BUSY nor DONE")
                self._sleep(self.poll_interval_s)
            returned_tag, count = self._read(Register.TAG), self._read(Register.COUNT)
            if returned_tag != tag:
                raise ProtocolError(f"Result tag mismatch: expected {tag}, received {returned_tag}")
            if count != ((count_before + 1) & MASK32):
                raise ProtocolError("Completion count did not advance exactly once")
            status_word = self._read(Register.STATUS)
            try:
                status = Status(status_word)
            except ValueError as error:
                raise ProtocolError(f"Unknown result status {status_word}") from error
            if bool(state & State.ERROR) != bool(status):
                raise ProtocolError("STATE ERROR and STATUS disagree")
            xyz = tuple(from_word(self._read(register)) for register in (Register.X, Register.Y, Register.Z))
            if status and xyz != (0, 0, 0):
                raise ProtocolError("Rejected request exposed nonzero XYZ")
            self._write(Register.CTRL, Control.ACK)
            if self._state() != 0:
                raise ProtocolError("ACK did not leave an idle, clear IP")
            self._needs_recovery = False
            self._next_tag = (tag + 1) & MASK32
            if status:
                raise ComputationError(status, returned_tag, count)
            return CompletedResult(*xyz, status, returned_tag, count, self.evidence)
        finally:
            self._lock.release()

    def recover(self):
        """Explicitly ABORT/discard any old result and clear sticky rejection.

        Recovery never returns an XYZ result. Completion count is retained.
        """
        if not self._lock.acquire(blocking=False):
            raise ConcurrentOwnerError("Cannot recover while an owner call is active")
        try:
            self._needs_recovery = True
            self._identify()
            self._write(Register.CTRL, Control.ABORT)
            self._write(Register.CTRL, Control.CLEAR_REJECT)
            if self._state() != 0:
                raise ProtocolError("ABORT did not clear BUSY/DONE/ERROR/REJECT")
            if any(self._read(register) != 0 for register in (Register.X, Register.Y, Register.Z, Register.STATUS, Register.TAG)):
                raise ProtocolError("ABORT left an old result visible")
            self._needs_recovery = False
        finally:
            self._lock.release()
