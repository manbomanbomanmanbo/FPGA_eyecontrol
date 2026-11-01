"""Original laser UART workflow, with every movement plan supplied by PL."""
from laser_gimbal import LaserPanTilt


class FpgaLaserPanTilt(LaserPanTilt):
    def __init__(self, planner, *args, **kwargs):
        if planner is None:
            raise RuntimeError("必须连接 FPGA 云台计算接口；不提供 CPU 运动计划替代")
        self.fpga_planner = planner
        super().__init__(*args, **kwargs)
        planner.trace = self._trace

    def _limits(self):
        return self.pan_min, self.pan_max, self.tilt_min, self.tilt_max

    def goto_point(self, x_cam, y_cam, z_cam):
        plan = self.fpga_planner.point(
            (x_cam, y_cam, z_cam), (self.offset_x, self.offset_y, self.offset_z),
            (self.laser_offset_x, self.laser_offset_y, self.laser_offset_z),
            (self._pan_steps, self._tilt_steps), limits=self._limits())
        self._trace("gimbal_target_angles", camera_metres=[x_cam, y_cam, z_cam],
                    rotation_centre_metres=[self.offset_x, self.offset_y, self.offset_z],
                    laser_offset_metres=[self.laser_offset_x, self.laser_offset_y, self.laser_offset_z],
                    forward_distance_metres=plan.forward_distance_m,
                    ray_distance_metres=plan.ray_distance_m,
                    pan_deg=plan.raw_pan_deg, tilt_deg=plan.raw_tilt_deg,
                    calculation="FPGA_PL", plan_tag=plan.tag)
        self._dispatch_plan(plan, 30)
        return self.pan_angle, self.tilt_angle

    def goto_angle(self, pan_deg, tilt_deg, speed=30):
        plan = self.fpga_planner.angle(pan_deg, tilt_deg, (self._pan_steps, self._tilt_steps),
                                       speed, self._limits())
        self._dispatch_plan(plan, speed)

    def _send_pl_axis(self, motor, axis, speed):
        if axis.direction == 0:
            self._trace("gimbal_motion_noop", motor=motor.motor_num, delta_steps=axis.delta_steps,
                        reason="target_equals_commanded_position", calculation="FPGA_PL")
            return
        if self.ser is None or not self.ser.is_open:
            raise OSError("云台串口未打开")
        # All motion fields came from PL. Shifts below only pack UART bytes.
        frame = bytes([0x57, motor.driver_id, motor.motor_num, axis.direction,
                       (speed >> 8) & 0xff, speed & 0xff, axis.revolutions,
                       (axis.remaining_steps >> 8) & 0xff, axis.remaining_steps & 0xff, 0x09])
        self._trace("gimbal_serial_write_start", motor=motor.motor_num,
                    delta_steps=axis.delta_steps, direction=axis.direction,
                    speed_rpm=speed, frame_hex=frame.hex(" "), expected_bytes=len(frame),
                    calculation="FPGA_PL")
        try:
            written = self.ser.write(frame)
        except Exception as error:
            self._trace("gimbal_serial_error", motor=motor.motor_num,
                        frame_hex=frame.hex(" "), error=str(error))
            raise
        self._trace("gimbal_serial_write", motor=motor.motor_num, delta_steps=axis.delta_steps,
                    direction=axis.direction, frame_hex=frame.hex(" "), expected_bytes=len(frame),
                    accepted_bytes=written, complete_write=written == len(frame), calculation="FPGA_PL")
        if written != len(frame):
            raise OSError("云台串口未完整写入运动指令")

    def _dispatch_plan(self, plan, speed):
        self._trace("gimbal_motion_plan", pan_steps=plan.pan.target_steps,
                    tilt_steps=plan.tilt.target_steps, current_pan_steps=self._pan_steps,
                    current_tilt_steps=self._tilt_steps, speed_rpm=speed,
                    calculation="FPGA_PL", plan_tag=plan.tag, plan_count=plan.count)
        for motor, step_attribute, angle_attribute, axis in (
                (self.pan_motor, "_pan_steps", "pan_angle", plan.pan),
                (self.tilt_motor, "_tilt_steps", "tilt_angle", plan.tilt)):
            self._send_pl_axis(motor, axis, speed)
            # Commit each axis only after its full write, or its verified noop.
            # A later tilt failure leaves an accepted pan update intact.
            setattr(self, step_attribute, axis.target_steps)
            setattr(self, angle_attribute, axis.estimated_angle_deg)
        self._trace("gimbal_motion_processed", pan_deg=self.pan_angle,
                    tilt_deg=self.tilt_angle, position_is_estimate=True, calculation="FPGA_PL")
        print(f"[激光云台] 已处理目标：水平={self.pan_angle:.3f}°，"
              f"俯仰={self.tilt_angle:.3f}°（发送步数对应的估计角度）")
