"""MMIO only: PL calculates, stores command state and emits all UART bits."""
import time
from .protocol import ID, ABI, EVIDENCE, TTL33_PROFILE, Control as C, Register as R, signed, word


class DriverError(RuntimeError):
    pass


class UARTDriver:
    def __init__(self, mmio, clock=time.monotonic, sleep=time.sleep):
        self.mmio, self.clock, self.sleep = mmio, clock, sleep
        if mmio.read(R.ID) != ID or mmio.read(R.ABI) != ABI:
            raise DriverError("Stage8 hardware ID/ABI mismatch")
        # Construction and new connections never reset, seed, ACK or arm PL.

    def _wide(self, address):
        for _ in range(4):
            high = self.mmio.read(address + 4)
            low = self.mmio.read(address)
            if high == self.mmio.read(address + 4):
                return (high << 32) | low
        raise DriverError("Unstable hardware cycle counter")

    def query(self):
        stable_registers = (R.STATE, R.FLAGS, R.EPOCH, R.COMPLETE_COUNT,
                            R.PAN_STEPS, R.TILT_STEPS, R.PAN_ANGLE, R.TILT_ANGLE,
                            R.MODE, R.PROFILE, R.STATUS, R.RESULT_TAG,
                            R.STOP_STATUS) + tuple(R.OUT0 + index * 4 for index in range(16))
        stream_registers = (("tx_frames", R.TX_FRAMES), ("tx_bytes", R.TX_BYTES),
                            ("rx_bytes", R.RX_BYTES), ("rx_framing_errors", R.RX_ERRORS),
                            ("rx_invalid_packets", R.RX_INVALID), ("last_reply", R.LAST_REPLY),
                            ("valid_replies", R.VALID_REPLIES), ("monitor_count", R.MONITOR_COUNT),
                            ("monitor_framing_errors", R.MONITOR_ERRORS))
        frame_registers = (("last_frame_start_cycles", R.LAST_START),
                           ("last_frame_end_cycles", R.LAST_END),
                           ("previous_frame_end_cycles", R.PREVIOUS_END),
                           ("last_frame_gap_cycles", R.LAST_GAP))
        def read_stream():
            values = {name: self.mmio.read(address) for name, address in stream_registers}
            values.update({name: self._wide(address) for name, address in frame_registers})
            return values
        for _ in range(8):
            before = tuple(self.mmio.read(address) for address in stable_registers)
            state, flags, epoch, count = before[:4]
            stream_begin = read_stream()
            monitor_count = stream_begin["monitor_count"]
            raw_ring = [self.mmio.read(R.MONITOR_RING + index * 4) for index in range(16)]
            # Decode actual monitor registers, never reconstruct requested UART.
            ring = [byte for value in raw_ring for byte in value.to_bytes(4, "little")]
            monitor = ([ring[(monitor_count + index) % 64] for index in range(64)]
                       if monitor_count >= 64 else ring[:monitor_count])
            telemetry = {
                "flags": flags, "mode": before[8], "epoch": epoch,
                "interface_profile": before[9],
                # Continuously advancing watchdog age is a sampled diagnostic,
                # not part of the stable command/stream snapshot.
                "watchdog_age_cycles": self.mmio.read(R.WATCHDOG_AGE),
                "current_steps": [signed(before[4]), signed(before[5])],
                "estimated_angles_q22": [signed(before[6]), signed(before[7])],
                "last_stop_status": before[12], "monitor_bytes": monitor,
            }
            telemetry.update(stream_begin)
            result = {"id": ID, "abi": ABI, "state": state, "count": count,
                      "status": before[10], "result_tag": before[11], "output": list(before[13:]),
                      "telemetry": telemetry, "evidence": EVIDENCE}
            stream_end = read_stream()
            after = tuple(self.mmio.read(address) for address in stable_registers)
            telemetry["telemetry_snapshot_stable"] = stream_begin == stream_end
            telemetry["telemetry_snapshot_begin"] = stream_begin
            telemetry["telemetry_snapshot_end"] = stream_end
            telemetry["monitor_snapshot_stable"] = (monitor_count == stream_end["monitor_count"]
                                                     and stream_begin["monitor_framing_errors"] == stream_end["monitor_framing_errors"])
            telemetry["monitor_snapshot_end_count"] = stream_end["monitor_count"]
            # Streaming TX/RX diagnostics may advance faster than PS reads,
            # even after TX_DONE when unsolicited external RX continues.
            # Management uses consistent command state without waiting for a
            # quiet stream; terminal packet validation requires both stable flags.
            if before == after:
                return result
        raise DriverError("Hardware snapshot changed continuously")

    def _wait_idle(self, timeout=.4):
        deadline = self.clock() + timeout
        while self.mmio.read(R.STATE) & 1:
            if self.clock() > deadline:
                raise DriverError("PL operation timeout; no automatic retransmit")
            self.sleep(.001)
        return self.query()

    def acknowledge(self):
        return self.control(C.ACK, 0)

    def validate_call_request(self, op, inputs, tag):
        """Read-only preflight before an owner command may renew its lease."""
        if type(op) is not int or op not in (1, 2) or type(inputs) is not list or len(inputs) != 16:
            raise ValueError("Invalid stage8 motion word count/operation")
        values = [word(value) for value in inputs]
        word(tag)
        before = self.query()
        if before["state"] != 0:
            raise DriverError("PL busy or existing unacknowledged result; query/explicit ACK required")
        return values, before

    def call(self, op, inputs, tag):
        values, before = self.validate_call_request(op, inputs, tag)
        for index, value in enumerate(values):
            self.mmio.write(R.IN0 + index * 4, value)
        self.mmio.write(R.OP, op)
        self.mmio.write(R.TAG, tag)
        self.mmio.write(R.CTRL, int(C.START))
        result = self._wait_idle()
        if (not result["state"] & 2 or result["result_tag"] != tag
                or result["count"] != ((before["count"] + 1) & 0xffffffff)
                or result["status"] not in range(21)
                or (result["status"] != 0 and any(result["output"]))):
            raise DriverError("Invalid PL terminal tag/count/status/output")
        # Only this call's consumed terminal result is ACKed; position persists.
        self.acknowledge()
        return result

    def control(self, control, tag, values=None):
        if type(control) is not int and not isinstance(control, C):
            raise ValueError("Unknown management command")
        try:
            command = C(control)
        except (ValueError, TypeError):
            raise ValueError("Unknown management command")
        if command == C.START:
            raise ValueError("Use call for START")
        word(tag)
        if values is not None and type(values) not in (list, tuple):
            raise ValueError("Management values must be an explicit word list")
        values = list(values) if values is not None else []
        if command == C.SEED:
            if len(values) != 2:
                raise ValueError("SEED requires two explicit signed32 words")
            values = [word(value) for value in values]
            if any(not -9102 <= signed(value) <= 9102 for value in values):
                raise ValueError("SEED signed32 values must be in [-9102,9102]")
        elif values:
            raise ValueError("Unexpected management payload")
        before = self.query()
        state, flags = before["state"], before["telemetry"]["flags"]
        if command == C.SEED and (state & 3 or flags & 1):
            raise DriverError("SEED requires disarmed idle PL with no outstanding result")
        if command == C.ARM:
            if state & 3 or not flags & 2 or flags & 8:
                raise DriverError("ARM requires idle seeded PL, no outstanding result or sticky fault")
            if before["telemetry"]["mode"] == 0 and before["telemetry"]["interface_profile"] != TTL33_PROFILE:
                raise DriverError("Physical ARM requires confirmed 3.3V TTL profile")
        if command == C.ACK and (state & 1 or not state & 2):
            raise DriverError("ACK requires a completed idle result")
        if command == C.RESET and (state & 1 or flags & 1):
            raise DriverError("RESET requires disarmed idle PL")
        if command == C.HEARTBEAT and not flags & 1:
            raise DriverError("HEARTBEAT requires armed PL")
        if command == C.SEED:
            for index, value in enumerate(values):
                self.mmio.write(R.IN0 + (9 + index) * 4, value)
        # Management session tags do not rewrite the retained execution tag,
        # especially while a frame/seed/injection is BUSY.
        self.mmio.write(R.CTRL, int(command))
        result = self.query() if command == C.HEARTBEAT else self._wait_idle()
        if command == C.ARM and not result["telemetry"]["flags"] & 1:
            raise DriverError("PL ARM rejected")
        if command in (C.STOP, C.DISARM) and result["telemetry"]["flags"] & 1:
            raise DriverError("PL safety stop did not disarm")
        if command == C.SEED and not result["telemetry"]["flags"] & 2:
            raise DriverError("PL position seed rejected")
        if command == C.ACK and result["state"] != 0:
            raise DriverError("PL ACK rejected")
        return result

    def configure(self, name, value):
        if type(name) is not str:
            raise ValueError("Configuration name must be mode or profile")
        register = {"mode": R.MODE, "profile": R.PROFILE}.get(name)
        value = word(value)
        if register is None or (name == "mode" and value not in (0, 1)):
            raise ValueError("Invalid configuration")
        before = self.query()
        if before["state"] & 1 or before["telemetry"]["flags"] & 1:
            raise DriverError("Configuration requires disarmed idle PL")
        self.mmio.write(register, value)
        if self.mmio.read(register) != value:
            raise DriverError("PL configuration readback mismatch")
        return self.query()

    def inject_reply(self, value):
        value = word(value)
        snapshot = self.query()
        if snapshot["state"] & 1 or snapshot["telemetry"]["mode"] != 1 or snapshot["telemetry"]["flags"] & 4:
            raise DriverError("Test injection forbidden with physical mode/output")
        self.mmio.write(R.TEST_REPLY, value)
        self.mmio.write(R.TEST_INJECT, 1)
        return self._wait_idle()
