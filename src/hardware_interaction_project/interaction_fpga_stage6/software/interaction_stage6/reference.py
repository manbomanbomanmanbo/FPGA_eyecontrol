"""Independent arithmetic/behavior references for stage 6 verification.

QuantizedReference is an executable register-operation specification, not a
runtime fallback. SourceFloat implements the source algorithms using binary64;
its comparison with Q16.16 has a numeric error bound, not bit equality.
Hardware milliseconds can be injected for boundary and modular-wrap tests.
"""
from copy import deepcopy
from dataclasses import dataclass
import math

Q = 65536
MASK = 0xFFFFFFFF


def unsigned(x):
    return int(x) & MASK


def signed(x):
    x = unsigned(x)
    return x - 0x100000000 if x & 0x80000000 else x


def age(now, then):
    return signed(unsigned(now) - unsigned(then))


def round_div(value, divisor):
    """Round nearest, exact half away from zero (positive divisor)."""
    magnitude = (abs(value) + divisor // 2) // divisor
    return -magnitude if value < 0 else magnitude


@dataclass
class Result:
    status: int
    out: list


class QuantizedReference:
    def __init__(self):
        self.now = 0
        self.smooth = None
        self.screen = (0, 0)
        self.history = []
        self.remaining = 0
        self.active = False
        self.move = None
        self.hold_until = None
        self.direction = -1
        self.mouth_counter = 0
        self.scroll_at = None
        self.pending = False
        self.action_at = None
        self.blink_at = None
        self.offer = None
        self.offer_counter = 0

    def advance(self, milliseconds):
        self.now = unsigned(self.now + milliseconds)

    def state(self):
        return deepcopy({key: value for key, value in self.__dict__.items() if key != "now"})

    def execute(self, operation, inputs=(), now=None):
        if now is not None:
            self.now = unsigned(now)
        w = [unsigned(x) for x in inputs] + [0] * (8 - len(inputs))
        if len(w) != 8:
            return Result(2, [0] * 8)
        a, b, c, d, e, _, _, _ = w
        out = [0] * 8
        if operation == 1:
            x, y = signed(a), signed(b)
            if not (0 <= x <= 8192 * Q and 0 <= y <= 8192 * Q):
                return Result(2, out)
            if self.smooth is None:
                self.smooth = (x, y)
            else:
                ox, oy = self.smooth
                distance = math.isqrt((x - ox) ** 2 + (y - oy) ** 2)
                if distance >= Q:
                    alpha = max(22938, min(49152, 49152 - (min(distance, 20 * Q) + 25) // 50))
                    gain = Q - alpha
                    self.smooth = (ox + round_div((x - ox) * gain, Q),
                                   oy + round_div((y - oy) * gain, Q))
            out[:2] = self.smooth
        elif operation == 2:
            if c & ~7 or not (2 <= d <= 16383 and 2 <= e <= 16383):
                return Result(2, out)
            point = (min(max(signed(a), 0), d * Q), min(max(signed(b), 0), e * Q))
            valid = (c & 6) == 6
            if c & 1:
                if not self.active:
                    self.active = True
                    selected = self.history[-6:-2] if len(self.history) >= 6 else self.history if len(self.history) >= 3 else []
                    if selected:
                        count = len(selected)
                        self.screen = tuple((sum(p[i] for p in selected) + count // 2) // count for i in range(2))
                self.remaining = 8
            elif self.remaining:
                self.remaining -= 1
                if self.remaining <= 2 and self.history and valid:
                    self.screen = tuple((7 * self.screen[i] + 3 * point[i] + 5) // 10 for i in range(2))
            else:
                self.active = False
                if c & 2:
                    self.screen = point
                self.history.append(self.screen)
                self.history = self.history[-30:]
            out[:5] = [*self.screen, self.remaining, len(self.history), int(self.active)]
        elif operation == 3:
            if a > 2 or b > 1:
                return Result(2, out)
            expired = bool(self.pending and self.blink_at is not None and age(self.now, self.blink_at) >= 1500)
            if expired:
                self.pending = False
            action = 0
            blink_age = age(self.now, self.blink_at) if self.blink_at is not None else None
            second = a == 1 and b == 0 and self.pending and blink_age is not None and 150 < blink_age < 1500
            allowed = self.action_at is None or age(self.now, self.action_at) > 350 or second
            if a and allowed and self.offer is None:
                action = 4 if a == 1 and b else 2 if a == 1 and self.pending and blink_age is not None and 150 < blink_age < 1500 else 1 if a == 1 else 3
                self.offer_counter = unsigned(self.offer_counter + 1)
                self.offer = (self.offer_counter, action, self.now)
            out[:5] = [action, self.offer[0] if self.offer else 0, int(self.pending), int(expired), self.now]
        elif operation == 4:
            if b > 1:
                return Result(2, out)
            if self.offer is None:
                return Result(4, out)
            if a != self.offer[0]:
                return Result(3, out)
            _, action, tick = self.offer
            if action == 1:
                self.pending = bool(b)
                self.blink_at = tick
                self.action_at = tick
            elif action == 2:
                self.pending = False
                self.action_at = unsigned(self.now + 300)
            elif action == 3:
                self.pending = False
                self.action_at = tick
            else:
                self.pending = False
                self.action_at = unsigned(tick + 300)
            self.offer = None
            out[:2] = [int(self.pending), self.now]
        elif operation == 5:
            if c > 1:
                return Result(2, out)
            scroll = 0
            if not c:
                if signed(a) >= 6554:
                    self.direction = 1
                elif signed(b) >= 6554:
                    self.direction = -1
                self.mouth_counter = 0
            else:
                self.mouth_counter = min(5, self.mouth_counter + 1)
                if self.mouth_counter >= 3 and (self.scroll_at is None or age(self.now, self.scroll_at) > 10):
                    scroll = self.direction
                    self.scroll_at = self.now
            out[:3] = [scroll, self.direction, self.mouth_counter]
        elif operation == 6:
            if not (21 <= c <= 16383 and 21 <= d <= 16383):
                return Result(2, out)
            if self.move is None:
                self.move = ((c // 2) * Q, (d // 2) * Q)
            allowed = self.hold_until is None or age(self.now, self.hold_until) >= 0
            if allowed:
                point = (min(max(signed(a), 10 * Q), (c - 10) * Q),
                         min(max(signed(b), 10 * Q), (d - 10) * Q))
                self.move = tuple(round_div(4 * self.move[i] + point[i], 5) for i in range(2))
            out[:3] = [*self.move, int(allowed)]
        elif operation == 7:
            self.move = (signed(a), signed(b))
            self.hold_until = unsigned(self.now + 200)
        elif operation == 8:
            out = [*self.screen, self.remaining, len(self.history), int(self.active), int(self.pending), self.offer[0] if self.offer else 0, self.now]
        elif operation == 9:
            self.pending = False
            self.action_at = self.blink_at = self.offer = None
            self.scroll_at = None
        else:
            return Result(1, out)
        return Result(0, [unsigned(x) for x in out])


class SourceFloat:
    """Source numeric behavior, exercised separately from the wire model."""
    def __init__(self):
        self.smooth = None
        self.screen = (0.0, 0.0)
        self.history = []
        self.remaining = 0
        self.active = False
        self.move = None
        self.hold_until = None
        self.direction = -1
        self.mouth_counter = 0
        self.scroll_at = None
        self.pending = False
        self.action_at = None
        self.blink_at = None
        self.offer = None
        self.offer_counter = 0

    def smoothing(self, raw):
        if self.smooth is None:
            self.smooth = raw
        else:
            distance = math.hypot(raw[0] - self.smooth[0], raw[1] - self.smooth[1])
            if distance >= 1:
                alpha = max(0.35, min(0.75, 0.75 - 0.02 * min(distance, 20)))
                self.smooth = tuple(alpha * self.smooth[i] + (1 - alpha) * raw[i] for i in range(2))
        return self.smooth

    def screen_step(self, point, blink=False, valid=True, calibrated=True, width=1920, height=1080):
        point = (min(max(point[0], 0), width), min(max(point[1], 0), height))
        if blink:
            if not self.active:
                self.active = True
                selected = self.history[-6:-2] if len(self.history) >= 6 else self.history if len(self.history) >= 3 else []
                if selected:
                    self.screen = tuple(sum(p[i] for p in selected) / len(selected) for i in range(2))
            self.remaining = 8
        elif self.remaining:
            self.remaining -= 1
            if self.remaining <= 2 and self.history and valid and calibrated:
                self.screen = tuple(0.7 * self.screen[i] + 0.3 * point[i] for i in range(2))
        else:
            self.active = False
            if valid:
                self.screen = point
            self.history.append(self.screen)
            self.history = self.history[-30:]
        return self.screen

    def move_step(self, target, width=1920, height=1080, now=0.0):
        if self.move is None:
            self.move = (width // 2, height // 2)
        if self.hold_until is not None and now < self.hold_until:
            return self.move
        target = (min(max(target[0], 10), width - 10), min(max(target[1], 10), height - 10))
        self.move = tuple(0.8 * self.move[i] + 0.2 * target[i] for i in range(2))
        return self.move

    def hold(self, actual_cursor, now):
        self.move = tuple(actual_cursor)
        self.hold_until = now + 0.20

    def plan(self, blink, coord_mode, now):
        expired = bool(self.pending and self.blink_at is not None and now - self.blink_at >= 1.5)
        if expired:
            self.pending = False
        second = blink == 1 and not coord_mode and self.pending and self.blink_at is not None and 0.15 < now - self.blink_at < 1.5
        allowed = self.action_at is None or now - self.action_at > 0.35 or second
        action = 0
        if blink and allowed and self.offer is None:
            action = 3 if blink == 2 else 4 if coord_mode else 2 if second else 1
            self.offer_counter += 1
            self.offer = (self.offer_counter, action, now)
        return action, self.offer[0] if self.offer else 0, self.pending, expired

    def acknowledge(self, identifier, success, now):
        if self.offer is None or self.offer[0] != identifier:
            raise ValueError("exact action offer ACK required")
        _, action, offered = self.offer
        if action == 1:
            self.pending = bool(success)
            self.blink_at, self.action_at = offered, offered
        else:
            self.pending = False
            self.action_at = now + 0.3 if action == 2 else offered + 0.3 if action == 4 else offered
        self.offer = None
        return self.pending

    def mouth_step(self, left, right, opened, now):
        command = 0
        if not opened:
            if left > 0.1:
                self.direction = 1
            elif right > 0.1:
                self.direction = -1
            self.mouth_counter = 0
        else:
            self.mouth_counter = min(5, self.mouth_counter + 1)
            if self.mouth_counter >= 3 and (self.scroll_at is None or now - self.scroll_at > 0.01):
                command, self.scroll_at = self.direction, now
        return command, self.direction, self.mouth_counter

    def query(self, now):
        return (*self.screen, self.remaining, len(self.history), self.active,
                self.pending, self.offer[0] if self.offer else 0, now)

    def reset_action(self):
        self.pending = False
        self.action_at = self.blink_at = self.offer = None
        self.scroll_at = None
