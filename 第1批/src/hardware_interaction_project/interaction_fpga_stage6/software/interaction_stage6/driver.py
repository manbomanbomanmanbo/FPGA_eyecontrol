"""Real MMIO transactions; no interaction implementation or CPU fallback."""
import time

ID = 0x47494e33
ABI = 0x00030000
EVIDENCE = 'pynq_mmio_result'


class DriverError(RuntimeError):
    pass


class InteractionDriver:
    def __init__(self, mmio, clock=time.monotonic):
        self.mmio, self.clock = mmio, clock
        self.failed = False
        if self.mmio.read(8) != ID or self.mmio.read(12) != ABI:
            raise DriverError('Interaction hardware ID/ABI mismatch')

    def reset(self):
        self.mmio.write(0, 4)
        deadline = self.clock() + .25
        while self.mmio.read(4) != 0:
            if self.clock() > deadline:
                self.failed = True
                raise DriverError('Interaction reset did not clear state')
        self.failed = False

    def compute(self, op, inputs, tag, timeout=.25):
        if self.failed:
            raise DriverError('Interaction driver requires a new session')
        if not 1 <= op <= 12 or len(inputs) != 8:
            raise ValueError('Invalid operation or input word count')
        if self.mmio.read(4) != 0:
            self.failed = True
            raise DriverError('Unacknowledged or busy interaction hardware')
        previous = self.mmio.read(0x68)
        for index, word in enumerate(inputs):
            self.mmio.write(0x14 + index * 4, word)
        self.mmio.write(0x10, op)
        self.mmio.write(0x3c, tag)
        self.mmio.write(0, 1)
        deadline = self.clock() + timeout
        while True:
            state = self.mmio.read(4)
            if state & 2:
                break
            if state & 8:
                self.failed = True
                raise DriverError('Hardware rejected transaction')
            if self.clock() > deadline:
                self.failed = True
                raise DriverError('Hardware interaction timed out')
        result = {'output': [self.mmio.read(0x40 + 4*i) for i in range(8)],
                  'status': self.mmio.read(0x60), 'tag': self.mmio.read(0x64),
                  'count': self.mmio.read(0x68), 'evidence': EVIDENCE}
        if result['tag'] != tag or result['count'] != ((previous+1) & 0xffffffff):
            self.failed = True
            raise DriverError('Interaction tag/count mismatch')
        self.mmio.write(0, 2)
        if self.mmio.read(4) != 0:
            self.failed = True
            raise DriverError('Hardware completion ACK failed')
        return result
