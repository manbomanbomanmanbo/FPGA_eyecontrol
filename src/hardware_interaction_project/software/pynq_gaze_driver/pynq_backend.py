"""Real-board adapter: NOT EXECUTED ON HARDWARE in this no-board task.

Importing this module neither imports pynq nor loads/programs an Overlay.
"""
from .driver import check_offset, uint32


class PynqMMIOBackend:
    def __init__(self, mmio, *, phys_addr, addr_range):
        self._mmio = mmio
        self.phys_addr = phys_addr
        self.addr_range = addr_range

    @classmethod
    def from_overlay(cls, overlay, ip_name):
        """Use an already created Overlay; never guess or hardcode PS addresses.

        Actual PYNQ construction and MMIO access remain untested without a board.
        """
        if not isinstance(ip_name, str) or not ip_name:
            raise ValueError("ip_name must identify a real overlay.ip_dict entry")
        metadata = overlay.ip_dict[ip_name]
        base, size = metadata["phys_addr"], metadata["addr_range"]
        if isinstance(base, bool) or not isinstance(base, int) or base < 0 or base % 4:
            raise ValueError("Overlay phys_addr must be a nonnegative aligned integer")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0x80:
            raise ValueError("Overlay addr_range is smaller than the 128-byte register aperture")
        from pynq import MMIO  # intentionally lazy; this line was not run on a board
        return cls(MMIO(base, size), phys_addr=base, addr_range=size)

    def read(self, offset):
        return int(self._mmio.read(check_offset(offset)))

    def write(self, offset, value):
        self._mmio.write(check_offset(offset), uint32(value))
