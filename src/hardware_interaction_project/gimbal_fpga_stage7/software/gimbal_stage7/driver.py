"""Verified MMIO transactions; gimbal arithmetic is implemented only in PL."""
import time
from .protocol import ID, ABI, EVIDENCE, Register as R


class DriverError(RuntimeError):
    pass


class GimbalDriver:
    def __init__(self, mmio, clock=time.monotonic):
        self.mmio, self.clock = mmio, clock
        self.failed = False
        if mmio.read(R.ID) != ID or mmio.read(R.ABI) != ABI:
            raise DriverError('Gimbal hardware ID/ABI mismatch')

    def reset(self):
        self.mmio.write(R.CTRL, 4)
        deadline = self.clock() + .25
        while self.mmio.read(R.STATE) != 0:
            if self.clock() > deadline:
                self.failed = True
                raise DriverError('Gimbal reset did not clear state')
        if self.mmio.read(R.COMPLETE_COUNT) != 0:
            self.failed = True
            raise DriverError('Gimbal reset did not clear completion count')
        self.failed = False

    def compute(self, op, inputs, tag, timeout=.25):
        if self.failed:
            raise DriverError('Gimbal driver requires a new session')
        if type(op) is not int or op not in (1, 2) or len(inputs) != 16:
            raise ValueError('Invalid operation/input count')
        if any(type(word) is not int or not 0 <= word <= 0xffffffff for word in inputs + [tag]):
            raise ValueError('Expected uint32 words and tag')
        if self.mmio.read(R.STATE) != 0:
            self.failed = True
            raise DriverError('Busy or unacknowledged gimbal hardware')
        previous = self.mmio.read(R.COMPLETE_COUNT)
        for index, value in enumerate(inputs):
            self.mmio.write(R.IN0 + 4 * index, value)
        self.mmio.write(R.OP, op)
        self.mmio.write(R.TAG, tag)
        self.mmio.write(R.CTRL, 1)
        deadline = self.clock() + timeout
        while True:
            state = self.mmio.read(R.STATE)
            if state & 2:
                break
            if state & 8 or self.clock() > deadline:
                self.failed = True
                raise DriverError('Gimbal transaction rejected or timed out')
        result = {'output': [self.mmio.read(R.OUT0 + 4 * i) for i in range(16)],
                  'status': self.mmio.read(R.STATUS), 'tag': self.mmio.read(R.RESULT_TAG),
                  'count': self.mmio.read(R.COMPLETE_COUNT), 'evidence': EVIDENCE}
        if (result['tag'] != tag or result['count'] != ((previous + 1) & 0xffffffff)
                or result['status'] not in range(12)
                or state != (6 if result['status'] else 2)
                or (result['status'] and any(result['output']))):
            self.failed = True
            raise DriverError('Corrupt hardware identity/status/tag/count/output')
        self.mmio.write(R.CTRL, 2)
        if self.mmio.read(R.STATE) != 0:
            self.failed = True
            raise DriverError('Gimbal hardware ACK failed')
        return result
