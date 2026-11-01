"""Explicit PYNQ binding; no device is opened on import."""

from .driver import check_offset
from .oracle import unsigned32






class PynqMMIOBackend:
    """Use an existing Overlay and its actual IP metadata; no address guesses.

    A completed driver transaction on this backend is a live MMIO result. It
    does not establish camera synchronization/calibration or physical accuracy.
    This adapter has not been run on a real board in this staged implementation.
    """

    evidence = "pynq_mmio_result"

    def __init__(self, mmio, *, phys_addr, addr_range):
        self._mmio = mmio
        self.phys_addr, self.addr_range = phys_addr, addr_range

    @classmethod
    def from_overlay(cls, overlay, ip_name):
        if not isinstance(ip_name, str) or not ip_name:
            raise ValueError("ip_name must name an actual overlay.ip_dict entry")
        metadata = overlay.ip_dict[ip_name]
        base, size = metadata["phys_addr"], metadata["addr_range"]
        if type(base) is not int or base < 0 or base % 4:
            raise ValueError("Overlay physical address must be a nonnegative aligned integer")
        if type(size) is not int or size < 0x80:
            raise ValueError("Overlay MMIO aperture must be at least 128 bytes")
        from pynq import MMIO  # lazy, only after the caller explicitly requests a real binding
        return cls(MMIO(base, size), phys_addr=base, addr_range=size)

    def read(self, offset):
        return int(self._mmio.read(check_offset(offset)))

    def write(self, offset, value):
        self._mmio.write(check_offset(offset), unsigned32(value))
