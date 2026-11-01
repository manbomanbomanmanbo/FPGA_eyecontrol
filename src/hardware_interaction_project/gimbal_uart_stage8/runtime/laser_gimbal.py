"""Laser pointing for a fixed camera and an aligned, conventional pan-tilt head.

The laser-head position is fixed in the rotating head and may be above the tilt axis.
Both axes must intersect, and the zero-angle laser beam must point along +Z.
Positions are metres / Y-up here; the eye-control entry converts ZED coordinates.
Reported angles describe sent step commands, without encoder feedback.
"""
import math

from control import DualPanTilt


class LaserPanTilt(DualPanTilt):
    STEPS_PER_REV = 6400
    # Match the confirmed motor wiring while keeping geometric Y/tilt upward-positive.
    PAN_DIRECTION_SIGN = 1
    TILT_DIRECTION_SIGN = -1

    def __init__(self, port="COM3", offset_x=0, offset_y=0, offset_z=0,
                 event_callback=None, laser_offset_x=0,
                 laser_offset_y=0, laser_offset_z=0):
        self._event_callback = event_callback
        super().__init__(port, offset_x, offset_y, offset_z)
        self.ser.write_timeout = 1.0
        self._pan_steps = 0
        self._tilt_steps = 0
        self.configure_laser_offset(laser_offset_x, laser_offset_y, laser_offset_z)
        self._trace("gimbal_serial_open", port=port, baudrate=115200,
                    driver_id=1, pan_motor=1, tilt_motor=2,
                    pan_direction_sign=self.PAN_DIRECTION_SIGN,
                    tilt_direction_sign=self.TILT_DIRECTION_SIGN)

    def _trace(self, event, **details):
        if self._event_callback is not None:
            try:
                self._event_callback(event, **details)
            except Exception:
                pass  # Diagnostics must not change motor-control behaviour.

    def read_pending_reply(self):
        """Log available UART bytes, without waiting or assuming target accuracy."""
        try:
            count = getattr(self.ser, "in_waiting", 0)
            if count:
                received = self.ser.read(count)
                self._trace("gimbal_serial_reply", received_hex=received.hex(" "),
                            received_bytes=len(received))
                return received
        except Exception as error:
            self._trace("gimbal_serial_read_error", error=str(error))
        return b""

    @staticmethod
    def _finite_values(values):
        return all(not isinstance(value, bool)
                   and isinstance(value, (int, float))
                   and math.isfinite(value) for value in values)

    def configure_laser_offset(self, x, y, z):
        if not self._finite_values((x, y, z)):
            raise ValueError("激光孔相对转轴的偏移必须是有限数值")
        self.laser_offset_x, self.laser_offset_y, self.laser_offset_z = x, y, z
        self._trace("gimbal_laser_offset", laser_offset_metres=[x, y, z])

    def goto_point(self, x_cam, y_cam, z_cam):
        values = (x_cam, y_cam, z_cam,
                  self.offset_x, self.offset_y, self.offset_z)
        if not self._finite_values(values):
            raise ValueError("目标坐标和云台安装位置必须是有限数值")
        x = x_cam - self.offset_x
        y = y_cam - self.offset_y
        z = z_cam - self.offset_z
        if not self._finite_values((x, y, z)) or z <= 0:
            raise ValueError("目标必须位于云台前方，且不能与旋转中心重合")
        bx, by, bz = (self.laser_offset_x, self.laser_offset_y, self.laser_offset_z)
        horizontal_distance = math.hypot(x, z)
        if not math.isfinite(horizontal_distance) or horizontal_distance <= abs(bx):
            raise ValueError("目标不在激光横向安装偏移可到达的前向范围")
        pan_rad = math.atan2(x, z) - math.asin(bx / horizontal_distance)
        forward_distance = horizontal_distance * math.sqrt(1.0 - (bx / horizontal_distance) ** 2)
        vertical_distance = math.hypot(y, forward_distance)
        if not math.isfinite(vertical_distance) or vertical_distance <= abs(by):
            raise ValueError("目标不在激光安装高度可到达的前向范围")
        tilt_rad = math.atan2(y, forward_distance) - math.asin(by / vertical_distance)
        ray_distance = vertical_distance * math.sqrt(1.0 - (by / vertical_distance) ** 2) - bz
        if not math.isfinite(ray_distance) or ray_distance <= 0:
            raise ValueError("目标位于激光发射点之后，不发送运动指令")
        pan_deg, tilt_deg = math.degrees(pan_rad), math.degrees(tilt_rad)
        self._trace("gimbal_target_angles", relative_metres=[x, y, z],
                    laser_offset_metres=[bx, by, bz], ray_distance_metres=ray_distance,
                    pan_deg=pan_deg, tilt_deg=tilt_deg)
        self.goto_angle(pan_deg, tilt_deg)
        return self.pan_angle, self.tilt_angle

    def _target_steps(self, angle, minimum, maximum):
        steps = round(angle * self.STEPS_PER_REV / 360.0)
        # Keep the quantised command inside the mechanical angular bounds too.
        lower = math.ceil(minimum * self.STEPS_PER_REV / 360.0)
        upper = math.floor(maximum * self.STEPS_PER_REV / 360.0)
        return max(lower, min(upper, steps))

    def _send_step_delta(self, motor, delta_steps, speed, direction_sign=1):
        if not delta_steps:
            self._trace("gimbal_motion_noop", motor=motor.motor_num,
                        delta_steps=0, reason="target_equals_commanded_position")
            return
        revolutions, steps = divmod(abs(delta_steps), self.STEPS_PER_REV)
        if revolutions > 255:
            raise ValueError("电机转动圈数超出协议范围")
        if self.ser is None or not self.ser.is_open:
            raise OSError("云台串口未打开")
        motor_delta_steps = delta_steps * direction_sign
        # Same 10-byte protocol as control.py; check the entire frame was written.
        frame = bytes([
            0x57, motor.driver_id, motor.motor_num,
            1 if motor_delta_steps > 0 else 2,
            (speed >> 8) & 0xFF, speed & 0xFF, revolutions,
            (steps >> 8) & 0xFF, steps & 0xFF, 0x09,
        ])
        self._trace("gimbal_serial_write_start", motor=motor.motor_num,
                    delta_steps=delta_steps, direction_sign=direction_sign,
                    motor_delta_steps=motor_delta_steps, speed_rpm=speed,
                    frame_hex=frame.hex(" "), expected_bytes=len(frame))
        try:
            written = self.ser.write(frame)
        except Exception as error:
            self._trace("gimbal_serial_error", motor=motor.motor_num,
                        frame_hex=frame.hex(" "), error=str(error))
            raise
        self._trace("gimbal_serial_write", motor=motor.motor_num,
                    delta_steps=delta_steps, direction_sign=direction_sign,
                    motor_delta_steps=motor_delta_steps, frame_hex=frame.hex(" "),
                    expected_bytes=len(frame), accepted_bytes=written,
                    complete_write=(written == len(frame)))
        if written != len(frame):
            raise OSError("云台串口未完整写入运动指令")

    def goto_angle(self, pan_deg, tilt_deg, speed=30):
        if not self._finite_values((pan_deg, tilt_deg)):
            raise ValueError("云台角度必须是有限数值")
        if isinstance(speed, bool) or not isinstance(speed, int) or not 1 <= speed <= 65535:
            raise ValueError("速度必须是 1..65535 范围内的整数")
        if not self.pan_min <= pan_deg <= self.pan_max:
            raise ValueError("目标水平角超出云台范围，不发送运动指令")
        if not self.tilt_min <= tilt_deg <= self.tilt_max:
            raise ValueError("目标俯仰角超出云台范围，不发送运动指令")
        pan_steps = self._target_steps(pan_deg, self.pan_min, self.pan_max)
        tilt_steps = self._target_steps(tilt_deg, self.tilt_min, self.tilt_max)
        self._trace("gimbal_motion_plan", pan_steps=pan_steps,
                    tilt_steps=tilt_steps, current_pan_steps=self._pan_steps,
                    current_tilt_steps=self._tilt_steps, speed_rpm=speed)
        # Absolute step targets prevent truncation drift across repeated targets.
        # Direct subtraction avoids a shortest path through a mechanical limit.
        for motor, attribute, angle_attribute, target_steps, direction_sign in (
                (self.pan_motor, "_pan_steps", "pan_angle", pan_steps,
                 self.PAN_DIRECTION_SIGN),
                (self.tilt_motor, "_tilt_steps", "tilt_angle", tilt_steps,
                 self.TILT_DIRECTION_SIGN)):
            delta_steps = target_steps - getattr(self, attribute)
            self._send_step_delta(motor, delta_steps, speed, direction_sign)
            setattr(self, attribute, target_steps)
            setattr(self, angle_attribute, target_steps * 360.0 / self.STEPS_PER_REV)
        self._trace("gimbal_motion_processed", pan_deg=self.pan_angle,
                    tilt_deg=self.tilt_angle, position_is_estimate=True)
        print(f"[激光云台] 已处理目标：水平={self.pan_angle:.3f}°，"
              f"俯仰={self.tilt_angle:.3f}°（发送步数对应的估计角度）")
