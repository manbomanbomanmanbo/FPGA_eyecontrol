"""Functional MMIO model of the reviewed RTL, not a cycle-accurate AXI model."""
import time

from .driver import ABI_VERSION, IP_ID, MASK32, Control, Register, State, check_offset, positive_seconds, uint32


class AXISlaveError(RuntimeError):
    """Mock representation of SLVERR; real PYNQ exception behavior is untested."""


def signed32(word):
    return word - (1 << 32) if word & (1 << 31) else word


def round_away(value, shift):
    half = 1 << (shift - 1)
    return (value + half) >> shift if value >= 0 else -((-value + half) >> shift)


def fixed_map(words):
    """Return x,y,status from 13 uint32 staging words; matches RTL check order."""
    gx, gy, *coefficients = [signed32(v) for v in words[:10]]
    a0, a1, a2, a3, b0, b1, b2, b3 = coefficients
    width, height, valid = words[10:13]
    if valid != 1 or not 2 <= width <= 8192 or not 2 <= height <= 8192:
        return 0, 0, 1
    if not -16384 <= gx <= 81920 or not -16384 <= gy <= 81920:
        return 0, 0, 2
    if any(abs(c) > 1966080000 for c in coefficients):
        return 0, 0, 3
    cross = round_away(gx * gy, 16)
    sx = a0 * gx + a1 * gy + a2 * cross + (a3 << 16)
    sy = b0 * gx + b1 * gy + b2 * cross + (b3 << 16)
    return min(width - 1, max(0, round_away(sx, 32))), min(height - 1, max(0, round_away(sy, 32))), 0


class MockMMIOBackend:
    """Deterministic functional state; latency_s is host time, never FPGA latency.

    Fault controls: stuck=True prevents completion; result_tag_override and
    result_status_override replace completion fields for driver error tests.
    advance() completes an outstanding request immediately unless stuck.
    access_log preserves ('read'/'write', offset, uint32) records.
    """
    def __init__(self, *, latency_s=0.0, stuck=False, ip_id=IP_ID, abi_version=ABI_VERSION):
        self.latency_s = positive_seconds(latency_s, "latency_s", True)
        self.stuck = bool(stuck)
        self.ip_id, self.abi_version = uint32(ip_id), uint32(abi_version)
        self.result_tag_override = None
        self.result_status_override = None
        self.cfg = [0] * 14
        self.scratch = 0
        self.busy = self.done = self.rejected = False
        self.result = [0, 0, 0, 0]
        self.complete_count = 0
        self._request = None
        self._ready_at = 0.0
        self.access_log = []

    def _update(self, force=False):
        if self._request is not None and not self.stuck and (force or time.monotonic() >= self._ready_at):
            snapshot = self._request
            x, y, status = fixed_map(snapshot)
            if self.result_status_override is not None:
                status = uint32(self.result_status_override)
                if status:
                    x = y = 0
            tag = snapshot[13] if self.result_tag_override is None else uint32(self.result_tag_override)
            self.result = [x, y, status, tag]
            self.complete_count = (self.complete_count + 1) & MASK32
            self._request = None
            self.busy, self.done = False, True

    def advance(self):
        self._update(force=True)

    def read(self, offset):
        offset = check_offset(offset)
        self._update()
        if offset == Register.CTRL:
            value = 0
        elif offset == Register.STATE:
            value = int(self.busy) | (int(self.done) << 1) | (int(self.done and self.result[2] != 0) << 2) | (int(self.rejected) << 3)
        elif offset == Register.IP_ID:
            value = self.ip_id
        elif offset == Register.ABI_VERSION:
            value = self.abi_version
        elif 0x10 <= offset <= 0x44:
            value = self.cfg[(offset - 0x10) // 4]
        elif 0x48 <= offset <= 0x54:
            value = self.result[(offset - 0x48) // 4]
        elif offset == Register.COMPLETE_COUNT:
            value = self.complete_count
        elif offset == Register.SCRATCH:
            value = self.scratch
        else:
            value = 0
        self.access_log.append(("read", offset, value))
        return value

    def write(self, offset, value, *, wstrb=0xF):
        offset = check_offset(offset)
        value = uint32(value)
        if isinstance(wstrb, bool) or not isinstance(wstrb, int) or not 0 <= wstrb <= 15:
            raise ValueError("WSTRB must be 0..15")
        self._update()
        self.access_log.append(("write", offset, value))
        mask = sum(0xFF << (8 * i) for i in range(4) if wstrb & (1 << i))
        if not mask:
            return
        if offset == Register.CTRL:
            command = value & mask
            if command & ~0xF:
                raise AXISlaveError("SLVERR: enabled CTRL bits above bit3")
            if command & Control.ABORT:
                self.busy = self.done = self.rejected = False
                self.result = [0, 0, 0, 0]
                self.complete_count = 0
                self._request = None
                return
            if command & Control.CLEAR_REJECT:
                self.rejected = False
            if command & Control.ACK_DONE and not self.busy:
                self.done = False
            if command & Control.START:
                if self.busy or self.done:
                    self.rejected = True
                    raise AXISlaveError("SLVERR: busy or unacknowledged result")
                self._request = tuple(self.cfg)
                self.busy, self.done = True, False
                self._ready_at = time.monotonic() + self.latency_s
        elif 0x10 <= offset <= 0x44:
            index = (offset - 0x10) // 4
            self.cfg[index] = (self.cfg[index] & ~mask) | (value & mask)
        elif offset == Register.SCRATCH:
            self.scratch = (self.scratch & ~mask) | (value & mask)
        else:
            raise AXISlaveError("SLVERR: read-only or reserved register")
