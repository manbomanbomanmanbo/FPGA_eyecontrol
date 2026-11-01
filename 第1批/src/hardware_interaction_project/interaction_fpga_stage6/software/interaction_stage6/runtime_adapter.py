"""Runtime wire adapter. All filtering and interaction decisions come from PL.

This module only encodes measurements and decodes authenticated hardware
results; it contains no replacement implementation of any interaction op.
"""
from enum import IntEnum
import math

from .driver import EVIDENCE


class Op(IntEnum):
    SMOOTH = 1
    SCREEN = 2
    PLAN = 3
    ACTION_ACK = 4
    MOUTH = 5
    MOVE = 6
    HOLD = 7
    QUERY = 8
    RESET_ACTION = 9


class InteractionFailure(RuntimeError):
    """The current process must stop instead of producing a CPU substitute."""


def q16(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("A finite Q16.16 measurement is required")
    encoded = round(float(value) * 65536)
    if not -(1 << 31) <= encoded < (1 << 31):
        raise ValueError("Q16.16 measurement overflow")
    return encoded & 0xffffffff


def signed(word):
    return word - (1 << 32) if word & (1 << 31) else word


def coordinate(word):
    return signed(word) / 65536.0


class InteractionAdapter:
    def __init__(self, client, trace=None):
        self.client = client
        self.trace = trace
        self.failed = False
        self.last_result = None
        self.last_plan = None
        self.pending = False
        self.screen_remaining = 0
        self.screen_active = False
        self.history_count = 0
        self.now_ms = 0
        self.operation_counts = {int(op): 0 for op in Op}

    def _call(self, op, *values):
        if self.failed:
            raise InteractionFailure("FPGA interaction session has failed; restart the program")
        try:
            result = self.client.call(int(op), list(values))
            output = result.get("output")
            if (result.get("evidence") != EVIDENCE or result.get("status") != 0
                    or not isinstance(output, list) or len(output) != 8
                    or any(type(word) is not int or not 0 <= word <= 0xffffffff for word in output)):
                raise ValueError("Unverified or failed interaction result: " + repr(result))
            self.last_result = result
            self.operation_counts[int(op)] += 1
            if self.trace is not None:
                self.trace("interaction_pl_result", op=int(op), inputs=list(values),
                           output=output, tag=result.get("tag"), count=result.get("count"),
                           evidence=result.get("evidence"), status=result.get("status"))
            return output
        except Exception as error:
            self.failed = True
            raise InteractionFailure("FPGA interaction unavailable: " + str(error)) from error

    def reset_action(self):
        self._call(Op.RESET_ACTION)
        self.pending = False
        self.last_plan = None

    def restart_tracking(self, actual_cursor, mouth_direction=-1):
        """Reset PL transient tracking state, retaining this adapter and trace.

        The separate gaze mapper owns the calibrated profile. Reopening only
        the interaction session leaves that profile and actuator sessions
        intact. QUERY verifies the board reset before any new tracking input.
        """
        if self.failed:
            raise InteractionFailure("FPGA interaction session has failed; restart the program")
        try:
            if type(mouth_direction) is not int or mouth_direction not in (-1, 1):
                raise ValueError("A scroll direction of -1 or 1 is required")
            cursor_words = (q16(float(actual_cursor[0])), q16(float(actual_cursor[1])))
            self.client.restart_session()
            state = self.query()
            if any(state[:7]):
                raise ValueError("FPGA interaction tracking state was not reset")

            self.screen_remaining = 0
            self.history_count = 0
            self.screen_active = False
            self.pending = False
            self.last_plan = None
            self.now_ms = state[7]

            # Restore the selected direction using the existing closed-mouth
            # PL operation; it cannot produce a scroll event with is_open=0.
            left = .2 if mouth_direction > 0 else 0.0
            command, direction, count = self.mouth(left, 0.0, False)
            if command != 0 or direction != mouth_direction or count != 0:
                raise ValueError("FPGA scroll direction restore failed")
            held = self._call(Op.HOLD, *cursor_words)
            if tuple(held[:2]) != cursor_words:
                raise ValueError("FPGA cursor seed was not acknowledged")
            if self.trace is not None:
                self.trace("interaction_tracking_restarted", actual_cursor=list(actual_cursor),
                           mouth_direction=mouth_direction, now_ms=self.now_ms)
        except Exception as error:
            self.failed = True
            self.client.close()
            raise InteractionFailure("FPGA tracking restart failed: " + str(error)) from error

    def smooth(self, point):
        out = self._call(Op.SMOOTH, q16(float(point[0])), q16(float(point[1])))
        return coordinate(out[0]), coordinate(out[1])

    def screen(self, point, screen_size, *, blink=False, calibrated=False):
        flags = int(bool(blink)) | (2 if point is not None else 0) | (4 if calibrated else 0)
        encoded = (0, 0) if point is None else (q16(float(point[0])), q16(float(point[1])))
        out = self._call(Op.SCREEN, *encoded, flags, int(screen_size[0]), int(screen_size[1]))
        self.screen_remaining, self.history_count, self.screen_active = out[2], out[3], bool(out[4])
        return coordinate(out[0]), coordinate(out[1])

    def plan(self, blink, coord_mode):
        blink_class = {None: 0, "double": 1, "right": 2}.get(blink)
        if blink_class is None:
            raise ValueError("Unknown geometric blink classification")
        out = self._call(Op.PLAN, blink_class, int(bool(coord_mode)))
        if out[0] not in range(5):
            self.failed = True
            raise InteractionFailure("Unknown action offered by PL")
        self.pending = bool(out[2])
        self.now_ms = out[4]
        self.last_plan = {"action": out[0], "offer_id": out[1], "pending": self.pending,
                          "expired": bool(out[3]), "now_ms": out[4]}
        return self.last_plan

    def acknowledge(self, offer_id, success):
        out = self._call(Op.ACTION_ACK, int(offer_id), int(bool(success)))
        self.pending, self.now_ms = bool(out[0]), out[1]
        return self.pending

    def mouth(self, left_change, right_change, is_open):
        out = self._call(Op.MOUTH, q16(float(left_change)), q16(float(right_change)), int(bool(is_open)))
        command, direction = signed(out[0]), signed(out[1])
        if command not in (-1, 0, 1) or direction not in (-1, 1):
            self.failed = True
            raise InteractionFailure("Unknown scroll result from PL")
        return command, direction, out[2]

    def move(self, point, screen_size):
        out = self._call(Op.MOVE, q16(float(point[0])), q16(float(point[1])),
                         int(screen_size[0]), int(screen_size[1]))
        if out[2] not in (0, 1):
            self.failed = True
            raise InteractionFailure("Unknown movement permission from PL")
        return (coordinate(out[0]), coordinate(out[1])), bool(out[2])

    def hold(self, actual_cursor):
        self._call(Op.HOLD, q16(float(actual_cursor[0])), q16(float(actual_cursor[1])))

    def query(self):
        return self._call(Op.QUERY)

    def close(self):
        self.client.close()
