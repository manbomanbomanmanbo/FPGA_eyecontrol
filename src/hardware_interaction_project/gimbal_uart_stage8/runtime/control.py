import math
import serial
import time

class StepperMotor:
    """单个步进电机控制（共享串口）"""
    def __init__(self, ser, driver_id=0x01, motor_num=1):
        self.ser = ser
        self.driver_id = driver_id
        self.motor_num = motor_num
        print(f"电机{motor_num} 已就绪")

    def _send_command(self, direction, speed_rpm, revolutions, steps=0):
        if self.ser is None:
            return
        speed_high = (speed_rpm >> 8) & 0xFF
        speed_low = speed_rpm & 0xFF
        steps_high = (steps >> 8) & 0xFF
        steps_low = steps & 0xFF
        cmd = bytes([
            0x57, self.driver_id, self.motor_num, direction,
            speed_high, speed_low, revolutions, steps_high, steps_low, 0x09
        ])
        self.ser.write(cmd)

    def rotate_angle(self, angle_deg, speed_rpm=30):
        steps_per_rev = 6400
        steps = int(abs(angle_deg) / 360 * steps_per_rev)
        revolutions = steps // steps_per_rev
        remaining_steps = steps % steps_per_rev
        direction = 1 if angle_deg >= 0 else 2  # 1=顺时针，2=逆时针
        self._send_command(direction, speed_rpm, revolutions, remaining_steps)

    def stop(self):
        self._send_command(0, 0, 0)


class DualPanTilt:
    """双电机云台控制（水平+垂直）"""
    def __init__(self, port='COM3', offset_x=0, offset_y=0, offset_z=0):
        """
        参数:
            port: 串口号
            offset_x: 电机相对于相机在X方向的偏移（米），右侧为正，左侧为负
            offset_y: 电机相对于相机在Y方向的偏移（米），上方为正，下方为负
            offset_z: 电机相对于相机在Z方向的偏移（米），前方为正，后方为负
        """
        print(f"正在连接串口 {port}...")
        self.ser = serial.Serial(port, 115200, timeout=1)
        time.sleep(2)
        print(f"串口 {port} 连接成功")
        self.pan_motor = StepperMotor(self.ser, driver_id=0x01, motor_num=1)   # 水平
        self.tilt_motor = StepperMotor(self.ser, driver_id=0x01, motor_num=2)  # 垂直
        self.pan_angle = 0
        self.tilt_angle = 0
        self.pan_min = -170
        self.pan_max = 170
        self.tilt_min = -45
        self.tilt_max = 45
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.offset_z = offset_z
        if offset_x != 0 or offset_y != 0 or offset_z != 0:
            print(f"已设置电机偏移: X={offset_x:.2f}m, Y={offset_y:.2f}m, Z={offset_z:.2f}m")

        print("云台初始化完成，初始位置: (0°, 0°)")

    def goto_angle(self, pan_deg, tilt_deg, speed=30):
        pan_deg = max(self.pan_min, min(self.pan_max, pan_deg))
        tilt_deg = max(self.tilt_min, min(self.tilt_max, tilt_deg))
        pan_delta = pan_deg - self.pan_angle
        tilt_delta = tilt_deg - self.tilt_angle
        if pan_delta > 180:
            pan_delta -= 360
        elif pan_delta < -180:
            pan_delta += 360
        if abs(pan_delta) > 0.1:
            self.pan_motor.rotate_angle(pan_delta, speed)
            self.pan_angle = pan_deg
            print(f"水平转动 {pan_delta:.1f}° → 当前水平角: {self.pan_angle:.1f}°")
        if abs(tilt_delta) > 0.1:
            self.tilt_motor.rotate_angle(tilt_delta, speed)
            self.tilt_angle = tilt_deg
            print(f"垂直转动 {tilt_delta:.1f}° → 当前垂直角: {self.tilt_angle:.1f}°")

    def goto_point(self, x_cam, y_cam, z_cam):
        """
        参数: 相机坐标系下的目标点（米）
        x_cam: 右侧为正
        y_cam: 上方为正
        z_cam: 前方为正
        """
        # 转换为电机坐标系
        x_motor = x_cam - self.offset_x   # 因为电机位于相机坐标系中的偏移量，目标点相对电机的位置要减去偏移
        y_motor = y_cam - self.offset_y
        z_motor = z_cam - self.offset_z

        # 计算角度
        pan_deg = math.degrees(math.atan2(x_motor, z_motor))
        tilt_deg = math.degrees(math.atan2(y_motor, z_motor))
        print(f"目标角度(补偿偏移后): 水平={pan_deg:.1f}°, 垂直={tilt_deg:.1f}°")
        self.goto_angle(pan_deg, tilt_deg)
        return pan_deg, tilt_deg

    def home(self, speed=30):
        print("回零中...")
        self.goto_angle(0, 0, speed)

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("串口已关闭")