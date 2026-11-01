"""Only input encoding and checked PL telemetry; no host motion authority."""
import math
import time
from .protocol import EVIDENCE, TTL33_PROFILE, signed
from .tcp import validate_snapshot


class GimbalExecutionFailure(RuntimeError):
    pass


class GimbalTargetRejected(ValueError):
    pass


class GimbalMotionPending(GimbalTargetRejected):
    """A new relative command must not replace a motor's active command."""


def encode_fixed(value, fractional_bits):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("FPGA 云台输入必须为有限数值")
    encoded = round(float(value) * (1 << fractional_bits))
    if not -(1 << 31) <= encoded < (1 << 31):
        raise ValueError("FPGA 云台输入超出定点范围")
    return encoded & 0xffffffff


class PLPanTilt:
    """Facade matching the original UI, without original serial construction."""
    def __init__(self, client, offset_x=0, offset_y=0, offset_z=0, event_callback=None):
        self.client, self._event_callback = client, event_callback
        self.failed = False
        self.offset_x, self.offset_y, self.offset_z = offset_x, offset_y, offset_z
        self.laser_offset_x = self.laser_offset_y = self.laser_offset_z = 0
        self.pan_min, self.pan_max, self.tilt_min, self.tilt_max = -170, 170, -45, 45
        self.speed_rpm = 30
        self._clock = time.monotonic
        self._ready_at = 0.0
        self._initial_configuration = None
        self._configuration_changed = False
        self._last_result = None
        self._observe(client.query())  # Read only: never seed/arm/reset at construction.

    def _emit(self, event, **details):
        if self._event_callback:
            try:
                self._event_callback(event, **details)
            except Exception:
                pass

    def _observe(self, result):
        validate_snapshot(result)
        if result["evidence"] != EVIDENCE:
            raise GimbalExecutionFailure("非PL UART返回")
        self._last_result = result
        return result

    @property
    def current_steps(self):
        return tuple(self._last_result["telemetry"]["current_steps"])

    @property
    def pan_angle(self):
        return self._last_result["telemetry"]["estimated_angles_q22"][0] / (1 << 22)

    @property
    def tilt_angle(self):
        return self._last_result["telemetry"]["estimated_angles_q22"][1] / (1 << 22)

    @property
    def last_result(self):
        return self._last_result

    @property
    def motion_wait_seconds(self):
        """Nominal command travel time, not measured mechanical arrival."""
        return max(0.0, self._ready_at - self._clock())

    def configure_laser_offset(self, x, y, z):
        for value in (x, y, z):
            encode_fixed(value, 24)
        self.laser_offset_x, self.laser_offset_y, self.laser_offset_z = x, y, z
        self._emit("gimbal_laser_offset", laser_offset_metres=[x, y, z])

    def initialize_known_state(self, pan_steps, tilt_steps, *, internal_test=True, confirmed_profile=None):
        """Explicit management only; seed is a supplied command-state estimate."""
        if any(type(value) is not int or not -9102 <= value <= 9102 for value in (pan_steps, tilt_steps)):
            raise ValueError("显式命令状态seed必须为[-9102,9102]步整数")
        if type(internal_test) is not bool:
            raise ValueError("internal_test必须为明确的布尔值")
        if not internal_test and (type(confirmed_profile) is not int or confirmed_profile != TTL33_PROFILE):
            raise ValueError("物理ARM需要已确认的3.3V TTL接口记录")
        snapshot = self.client.query()
        flags = snapshot["telemetry"]["flags"]
        if flags & (1 | 4 | 16) or snapshot["state"] & 1:
            raise GimbalExecutionFailure("先明确停止现有PL任务，不能覆盖活动状态")
        if flags & 8:
            if (flags & 2 or snapshot["telemetry"]["last_stop_status"] != 18
                    or (snapshot["state"], snapshot["status"]) not in ((0, 0), (6, 18))):
                raise GimbalExecutionFailure("上次不是正常停止；先检查故障，不自动复位或重发")
        elif snapshot["state"] & 4:
            raise GimbalExecutionFailure("存在未处理错误；不覆盖其它会话结果")
        self._initial_configuration = (snapshot["telemetry"]["mode"],
                                       snapshot["telemetry"]["interface_profile"])
        if snapshot["state"] & 2:
            self.client.ack()  # Consume only an existing terminal result during explicit preparation.
        self.client.configure("mode", 1 if internal_test else 0)
        self._configuration_changed = True
        if not internal_test:
            self.client.configure("profile", confirmed_profile)
        self.client.seed(pan_steps, tilt_steps)
        result = self.client.arm()
        self._observe(result)
        self._emit("gimbal_uart_armed", internal_test=internal_test, result=result,
                   seed_is_measured_position=False)

    def _common(self, speed):
        if type(speed) is not int or not 3 <= speed <= 65535:
            raise ValueError("执行速度必须为3..65535 RPM整数")
        # Normal motion can never submit a host mirror as authoritative position.
        return [0, 0, speed, *(encode_fixed(value, 22) for value in
                               (self.pan_min, self.pan_max, self.tilt_min, self.tilt_max))]

    def _call(self, op, inputs):
        if self.failed:
            raise GimbalExecutionFailure("本次FPGA云台链路已失败，不自动重发")
        if self.motion_wait_seconds > 0:
            raise GimbalMotionPending("上一条云台动作仍在执行，请约 %.1f 秒后重新确认目标"
                                      % self.motion_wait_seconds)
        try:
            result = self._observe(self.client.call(op, inputs))
            self._emit("gimbal_uart_pl_result", op=op, inputs=inputs, result=result,
                       transmission_only=True, mechanical_arrival=False)
            if result["status"] in range(1, 13):
                raise GimbalTargetRejected("FPGA云台拒绝目标，状态=" + str(result["status"]))
            if result["status"] != 0 or not result["telemetry"]["flags"] & 2:
                raise GimbalExecutionFailure("FPGA执行停止/状态失效，状态=" + str(result["status"]))
            # PL supplies the transmitted step deltas. This only spaces user
            # commands; it neither plans movement nor certifies motor arrival.
            delta_steps = max(abs(signed(value)) for value in result["output"][6:8])
            if result["telemetry"]["mode"] == 0 and delta_steps:
                nominal_seconds = delta_steps * 60.0 / (inputs[11] * 6400)
                self._ready_at = self._clock() + nominal_seconds + 0.30
                self._emit("gimbal_command_wait", nominal_seconds=nominal_seconds,
                           guard_seconds=0.30, mechanical_arrival_certified=False)
            self._emit("gimbal_motion_processed", pan_deg=self.pan_angle, tilt_deg=self.tilt_angle,
                       position_is_estimate=True, calculation="FPGA_PL_UART", transmission_only=True)
            return result
        except GimbalTargetRejected:
            raise
        except Exception as error:
            self.failed = True
            raise GimbalExecutionFailure("FPGA UART链路或执行失败：" + str(error)) from error

    def goto_point(self, x_cam, y_cam, z_cam):
        inputs = [encode_fixed(value, 24) for values in ((x_cam, y_cam, z_cam),
                  (self.offset_x, self.offset_y, self.offset_z),
                  (self.laser_offset_x, self.laser_offset_y, self.laser_offset_z)) for value in values]
        self._call(1, inputs + self._common(self.speed_rpm))
        return self.pan_angle, self.tilt_angle

    def goto_angle(self, pan_deg, tilt_deg, speed=30):
        self._call(2, [encode_fixed(pan_deg, 22), encode_fixed(tilt_deg, 22)] + [0] * 7 + self._common(speed))

    def home(self, speed=30):
        self.goto_angle(0, 0, speed)  # PL ANGLE; no physical homing claim.

    def read_pending_reply(self):
        result = self._observe(self.client.query())
        self._emit("gimbal_uart_diagnostics", result=result, controller_arrival_certified=False)
        return result

    def stop(self):
        result = self._observe(self.client.stop())
        self._emit("gimbal_uart_management", control=32, result=result)
        return result

    def close(self):
        if getattr(self.client, "failed", False):
            raise GimbalExecutionFailure("云台链路已失败，停止状态无法确认；不自动重连或重发")
        if self.client.owns_arm:
            result = self._observe(self.client.disarm())
            self._emit("gimbal_uart_management", control=16, result=result)
            if (result["state"] != 6 or result["status"] != 18
                    or result["telemetry"]["last_stop_status"] != 18
                    or not result["telemetry"]["flags"] & 8
                    or result["telemetry"]["flags"] & (1 | 2 | 4 | 16)):
                raise GimbalExecutionFailure("正常停止结果未确认，不清理或重发")
            self._observe(self.client.ack())
            self._ready_at = 0.0
        if self._configuration_changed:
            snapshot = self.client.query()
            if (snapshot["state"] != 0 or snapshot["telemetry"]["flags"] & (1 | 4 | 16)
                    or (snapshot["telemetry"]["flags"] & 8
                        and snapshot["telemetry"]["last_stop_status"] != 18)):
                raise GimbalExecutionFailure("退出时存在未处理状态；不清理其它会话或覆盖故障")
            mode, profile = self._initial_configuration
            self.client.configure("mode", mode)
            self._observe(self.client.configure("profile", profile))
            self._configuration_changed = False
        # The injected TCP lifetime belongs to the main ExitStack, not this facade.
