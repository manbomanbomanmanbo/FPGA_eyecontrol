"""Encode source-unit measurements and consume complete PL gimbal plans.

No geometric, angle-to-step, direction, revolution or remainder computation is
implemented here. The PC only keeps per-axis accepted-command output mirrors.
"""
from dataclasses import dataclass
import math

from .protocol import EVIDENCE, Op, Status


class GimbalLinkFailure(RuntimeError):
    pass


class GimbalTargetRejected(ValueError):
    pass


def encode_fixed(value, fractional_bits):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("FPGA 云台输入必须为有限数值")
    word = round(float(value) * (1 << fractional_bits))
    if not -(1 << 31) <= word < (1 << 31):
        raise ValueError("FPGA 云台输入超出定点表示范围")
    return word & 0xffffffff


def signed(word):
    return word - (1 << 32) if word & (1 << 31) else word


def step_input(value):
    if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
        raise ValueError("云台已发送步数必须为 signed32 整数")
    return value & 0xffffffff


@dataclass(frozen=True)
class AxisPlan:
    target_steps: int
    delta_steps: int
    direction: int
    revolutions: int
    remaining_steps: int
    estimated_angle_deg: float


@dataclass(frozen=True)
class GimbalPlan:
    raw_pan_deg: float
    raw_tilt_deg: float
    forward_distance_m: float
    ray_distance_m: float
    pan: AxisPlan
    tilt: AxisPlan
    tag: int
    count: int


class GimbalAdapter:
    def __init__(self, client, trace=None):
        self.client, self.trace = client, trace
        self.failed = False
        self.last_tag = None
        self.last_count = None
        self.last_result = None

    def _emit(self, event, **details):
        if self.trace is not None:
            try:
                self.trace(event, **details)
            except Exception:
                pass  # Preserve the source's diagnostic-only trace semantics.

    def _common_inputs(self, current_steps, speed, limits):
        if len(current_steps) != 2 or len(limits) != 4:
            raise ValueError("云台状态必须包含两轴步数和四个角度限位")
        if type(speed) is not int or not 1 <= speed <= 65535:
            raise ValueError("速度必须是 1..65535 范围内的整数")
        return [step_input(current_steps[0]), step_input(current_steps[1]), speed,
                *(encode_fixed(value, 22) for value in limits)]

    def point(self, camera_xyz, origin_xyz, laser_xyz, current_steps, speed=30,
              limits=(-170, 170, -45, 45)):
        if any(len(values) != 3 for values in (camera_xyz, origin_xyz, laser_xyz)):
            raise ValueError("相机目标、轴心与激光孔各需三个坐标")
        # These are already source Y-up metres; do not flip target/origin or by.
        inputs = [encode_fixed(value, 24) for values in (camera_xyz, origin_xyz, laser_xyz)
                  for value in values]
        return self._call(Op.POINT, inputs + self._common_inputs(current_steps, speed, limits))

    def angle(self, pan_deg, tilt_deg, current_steps, speed=30,
              limits=(-170, 170, -45, 45)):
        inputs = [encode_fixed(pan_deg, 22), encode_fixed(tilt_deg, 22)] + [0] * 7
        return self._call(Op.ANGLE, inputs + self._common_inputs(current_steps, speed, limits))

    def _call(self, op, inputs):
        if self.failed:
            raise GimbalLinkFailure("FPGA 云台链路已失败，本次运行不再执行运动计划")
        try:
            result = self.client.call(int(op), inputs)
            output, status = result.get("output"), result.get("status")
            tag, count = result.get("tag"), result.get("count")
            if (result.get("evidence") != EVIDENCE or type(status) is not int or status not in range(12)
                    or type(output) is not list or len(output) != 16
                    or any(type(value) is not int or not 0 <= value <= 0xffffffff for value in output)
                    or type(tag) is not int or not 1 <= tag <= 0xffffffff
                    or type(count) is not int or not 0 <= count <= 0xffffffff
                    or (self.last_tag is not None and tag <= self.last_tag)
                    or (self.last_count is not None and count != ((self.last_count + 1) & 0xffffffff))):
                raise ValueError("FPGA 云台返回身份、顺序或数据格式无效")
            self.last_tag, self.last_count, self.last_result = tag, count, result
            self._emit("gimbal_pl_result", op=int(op), inputs=inputs, output=output,
                       status=status, tag=tag, count=count, evidence=result["evidence"])
            if status != Status.OK:
                if any(output):
                    raise ValueError("FPGA 云台拒绝结果含非零运动输出")
                rejection = GimbalTargetRejected("FPGA 云台拒绝目标：" + Status(status).name)
                rejection.hardware_status = status
                raise rejection
            for direction, revolutions, remainder in ((output[8], output[10], output[11]),
                                                       (output[9], output[12], output[13])):
                if direction not in (0, 1, 2) or revolutions > 255 or remainder >= 6400:
                    raise ValueError("FPGA 云台输出的串口字段无效")
            pan = AxisPlan(signed(output[4]), signed(output[6]), output[8], output[10], output[11],
                           signed(output[14]) / (1 << 22))
            tilt = AxisPlan(signed(output[5]), signed(output[7]), output[9], output[12], output[13],
                            signed(output[15]) / (1 << 22))
            if ((pan.delta_steps == 0) != (pan.direction == 0)
                    or (tilt.delta_steps == 0) != (tilt.direction == 0)):
                raise ValueError("FPGA 云台零步数与方向字段不一致")
            return GimbalPlan(signed(output[0]) / (1 << 22), signed(output[1]) / (1 << 22),
                              signed(output[2]) / (1 << 20), signed(output[3]) / (1 << 20),
                              pan, tilt, tag, count)
        except GimbalTargetRejected:
            raise
        except Exception as error:
            self.failed = True
            raise GimbalLinkFailure("FPGA 云台链路或结果验证失败：" + str(error)) from error

    def close(self):
        self.client.close()
