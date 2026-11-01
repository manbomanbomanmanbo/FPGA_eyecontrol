
import cv2
import time
import os
import sys
import numpy as np
import pyautogui
import subprocess
import socket
import struct
import json
import keyboard


# def fix_paths_before_mediapipe():
#     """在导入MediaPiece之前修复所有路径问题"""
#     import os
#     import sys
#
#     # 1. 设置当前工作目录
#     if getattr(sys, 'frozen', False):
#         base_dir = os.path.dirname(sys.executable)
#     else:
#         base_dir = os.path.dirname(os.path.abspath(__file__))
#
#     os.chdir(base_dir)
#
#     # 2. 查找mediapipe目录
#     mediapipe_path = None
#     for path in [
#         os.path.join(base_dir, "mediapipe"),
#         os.path.join(base_dir, "_internal", "mediapipe"),
#         os.path.join(base_dir, "_internal"),
#     ]:
#         if os.path.exists(path):
#             mediapipe_path = os.path.normpath(path)
#             break
#
#     # 3. 设置环境变量
#     os.environ['MEDIAPIPE_MODULE_PATH'] = mediapipe_path
#     os.environ['GLOG_logtostderr'] = '1'
#
#     # 4. 关键：猴子补丁os.path.exists
#     original_exists = os.path.exists
#
#     def patched_exists(path):
#         if isinstance(path, str):
#             # 修复MediaPiece路径
#             if 'mediapipe' in path and ('modules' in path or 'face_landmark' in path):
#                 # 统一分隔符
#                 fixed = path.replace('/', os.sep)
#
#                 # 如果包含_internal\mediapipe，尝试去掉_internal
#                 if '_internal' + os.sep + 'mediapipe' in fixed:
#                     # 方法1：去掉_internal
#                     alt1 = fixed.replace('_internal' + os.sep, '')
#                     if original_exists(alt1):
#                         return True
#
#                     # 方法2：使用mediapipe_path
#                     rel_path = fixed.split('mediapipe' + os.sep)[-1]
#                     alt2 = os.path.join(mediapipe_path, rel_path)
#                     if original_exists(alt2):
#                         return True
#
#                 # 直接使用原始函数检查
#                 return original_exists(fixed)
#
#         return original_exists(path)
#
#     os.path.exists = patched_exists
#
#
# # 调用
# fix_paths_before_mediapipe()

import math
import tkinter as tk
from tkinter import messagebox
from datetime import datetime
from eye_tracker import EyeTracker
from mouse_controller import MouseController
from calibrator import Calibrator



class EyeControlSystem:
    def __init__(self, eye_tracker=None, mouse_controller=None):
        self.eye_tracker = EyeTracker() if eye_tracker is None else eye_tracker
        self.mouse_controller = MouseController() if mouse_controller is None else mouse_controller
        self.interaction = getattr(self.eye_tracker, "interaction", None)
        self.calibrator = Calibrator(self.mouse_controller.get_screen_size())
        self.is_running = False
        self.enable_control = False
        # 添加标志：只在眼睛被检测到时开始记录
        self.eye_detected_once = False  # 眼睛是否被检测到过
        self.recording_started = False  # 记录是否已开始
        self.waiting_counter = 0  # 等待检测的帧数计数器

        # 窗口设置
        self.camera_window_size = (1000, 800)

        # 数据记录设置
        self.output_file = None
        self.output_enabled = True  # 默认开启记录
        self.output_counter = 0

        # 添加：是否显示校准网格窗口
        self.show_calibration_grid = True  # 默认显示校准网格
        screen_size = self.mouse_controller.get_screen_size()
        self.screen_width, self.screen_height = screen_size


        # 新增：嘴部滚轮控制
        self.mouth_control_enabled = False  # 校准后启用
        self.mouth_scroll_active = False
        self.last_scroll_time = 0
        self.scroll_interval = 0.01  # 滚动间隔（秒）

        # ===== 新增：ZED 子进程相关 =====
        self.zed_process = None
        self.zed_socket = None
        self.udp_sock = None  # UDP socket，初始为空
        self.zed_process = None  # 子进程对象，保持原样
        self.coord_output_mode = False  # 坐标输出模式（Z 键切换）
        self.zed_window_visible = False  # ZED 窗口是否显示
        self.zed_python_path = r"E:\zed_workspace\envs\zed\python.exe"
        # ===== 环境零点设置 =====
        # 零点相对于双目相机的三维坐标，单位 mm
        # X：左右，左负右正
        # Y：上下，上正下负
        # Z：前后，相机前方为正
        self.zero_x = 0.0
        self.zero_y = 0.0
        self.zero_z = 0.0
        self.zero_initialized = False

        # 当前目标点三维坐标
        self.current_target_xyz = None

        # 当前目标相对于零点的水平角
        self.current_horizontal_angle = None

        # 启动 ZED 子进程并建立连接
        self.start_zed_subprocess()

        # 获取当前程序目录
        if getattr(sys, 'frozen', False):
            # 如果是打包后的exe
            self.app_dir = os.path.dirname(sys.executable)
        else:
            # 如果是源代码运行
            self.app_dir = os.path.dirname(os.path.abspath(__file__))

        # 创建数据存储目录
        self.data_dir = os.path.join(self.app_dir, "eye_tracking_data")
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)

        # 自动创建输出文件（每次运行都创建新文件）
        self.open_output_file()



        # 全局 ESC 监听（不依赖窗口焦点）
        self.zed_esc_request = False  # ESC 请求标志
        keyboard.add_hotkey('esc', self.on_global_esc)



    def _ack_fpga_action(self, success):
        if self.interaction is not None:
            self.double_blink_pending = self.interaction.acknowledge(
                self.fpga_action_plan["offer_id"], success)
            if not self.double_blink_pending:
                self.pending_click_position = None

    def on_global_esc(self):
        """ESC 全局热键回调（在后台线程中执行）"""
        self.zed_esc_request = True
        print("[全局热键] ESC 被按下")

    def start_zed_subprocess(self):
        """启动 ZED 子进程（如果尚未运行），不等待连接"""
        if self.zed_process is not None and self.zed_process.poll() is None:
            # 已经在运行
            return True

        if getattr(sys, 'frozen', False):
            zed_exe = os.path.join(os.path.dirname(sys.executable), "zed_server.exe")
            if not os.path.exists(zed_exe):
                print("⚠️ 未找到 zed_server.exe")
                return False
            self.zed_process = subprocess.Popen([zed_exe],
                                                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        else:
            zed_script = os.path.join(os.path.dirname(__file__), "zed_server.py")
            if not os.path.exists(zed_script):
                print(f"⚠️ 未找到 {zed_script}")
                return False
            if not os.path.exists(self.zed_python_path):
                print(f"⚠️ 未找到 zed_env 解释器: {self.zed_python_path}")
                return False
            self.zed_process = subprocess.Popen(
                [self.zed_python_path, zed_script],
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            ("正在启动 ZED 子进程...")
            # 给子进程一点时间启动（UDP 不需要等待连接，但仍需等待它初始化）
            time.sleep(1.5)
        return True

    def send_zed_command(self, cmd_dict):
        """通过 UDP 发送命令到 ZED 子进程，返回响应字典（或 None）"""
        # 如果 UDP socket 还没创建，就创建一个
        if self.udp_sock is None:
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_sock.settimeout(0.6)  # 0.6 秒超时，避免长时间卡住

        # 重试 3 次，防止网络丢包
        for attempt in range(3):
            try:
                # 发送 JSON 数据
                data = json.dumps(cmd_dict).encode()
                self.udp_sock.sendto(data, ('127.0.0.1', 9999))
                # 等待回复
                recv_data, _ = self.udp_sock.recvfrom(65535)
                return json.loads(recv_data.decode())
            except socket.timeout:
                # 超时则重试
                continue
            except Exception as e:
                print(f"ZED UDP 通信错误: {e}")
                # 出错后，重置 socket，下次重试时会新建
                self.udp_sock = None
                return None
        # 重试全部失败
        print("ZED 命令超时，无响应")
        return None

    def stop_zed_subprocess(self):
        """停止 ZED 子进程并关闭 UDP socket"""
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except:
                pass
            self.udp_sock = None
        if self.zed_socket:  # 兼容旧代码，如果还有的话
            try:
                self.zed_socket.close()
            except:
                pass
            self.zed_socket = None
        if self.zed_process:
            try:
                self.zed_process.terminate()
                self.zed_process.wait(timeout=2)
            except:
                self.zed_process.kill()
            self.zed_process = None
        self.coord_output_mode = False
        self.zed_window_visible = False
        print("🔚 ZED 环境模式已退出，眼控鼠标继续运行")

    def open_output_file(self):
        """打开输出文件"""
        try:
            # 生成带时间戳的文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"eye_tracking_{timestamp}.txt"
            filepath = os.path.join(self.data_dir, filename)

            # 打开文件
            self.output_file = open(filepath, 'w', encoding='utf-8')
            self.output_enabled = True

            # 写入文件头
            header = f"# 眼动追踪数据文件\n"
            header += f"# 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            header += f"# 屏幕分辨率: {self.mouse_controller.screen_width}x{self.mouse_controller.screen_height}\n"
            header += f"# 格式: 帧号,时间戳(毫秒),眼睛在摄像头画面中的像素位置X,眼睛Y,鼠标在屏幕的位置X,屏幕Y\n"
            header += "#" * 60 + "\n"

            self.output_file.write(header)
            print(f"✅ 开始记录数据到: {filename}")
            print(f"📁 文件位置: {self.data_dir}")

        except Exception as e:
            print(f"❌ 无法创建输出文件: {e}")
            self.output_enabled = False

    def close_output_file(self):
        """关闭输出文件"""
        if self.output_file:
            try:
                # 写入文件尾
                footer = f"\n# 结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                footer += f"# 总记录帧数: {self.output_counter}\n"
                footer += "#" * 60 + "\n"

                self.output_file.write(footer)
                self.output_file.close()

                print(f"✅ 数据已保存，共记录 {self.output_counter} 帧")

            except Exception as e:
                print(f"❌ 关闭文件时出错: {e}")

            self.output_file = None
            self.output_enabled = False
            self.output_counter = 0

    def write_coordinates_to_file(self, eye_pos, screen_pos, frame_count):
        """将坐标写入文件"""
        if not self.output_enabled or not self.output_file:
            return

        try:
            # 获取当前时间的毫秒级时间戳
            current_time_ms = int(time.time() * 1000)

            # 获取校准状态
            calibrated_status = "CALIBRATED" if self.eye_tracker.is_calibrated else "UNCALIBRATED"

            # 处理眼睛坐标
            if eye_pos:
                eye_x, eye_y = eye_pos
            else:
                eye_x, eye_y = 0.0, 0.0

            # 写入数据行
            line = f"{frame_count},{current_time_ms},{eye_x:.1f},{eye_y:.1f},{screen_pos[0]:.1f},{screen_pos[1]:.1f}\n"
            self.output_file.write(line)
            flush_time = time.monotonic()
            last_flush_time = getattr(self, "_last_coordinate_flush_time", None)
            if last_flush_time is None or flush_time - last_flush_time >= 0.5:
                self.output_file.flush()
                self._last_coordinate_flush_time = flush_time

            self.output_counter += 1

        except Exception as e:
            print(f"❌ 写入文件错误: {e}")

    def initialize_camera(self):
        """Initializez camera """
        self.cap = cv2.VideoCapture(0)  # 使用摄像头
        if not self.cap.isOpened():
            print("Cannot open camera ")
            return False

        # 只设置分辨率，其他全部使用系统默认值
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        print("Camera initialized successfully")
        return True

    def _prepare_eye_frame(self, frame):
        # 先确认当前帧可用，再允许眨眼动作，避免恢复首帧误触发旧动作。
        eye_pos = self.eye_tracker.detect_iris_position(frame)
        state = (getattr(self.eye_tracker, "tracking_ready", bool(eye_pos)),
                 getattr(self.eye_tracker, "tracking_loss_reason", None),
                 getattr(self.eye_tracker, "tracking_generation", 0),
                 self.eye_tracker.recalibration_prompt)
        if state != getattr(self, "_last_tracking_state", None):
            self._last_tracking_state = state
            if hasattr(self.mouse_controller, "trace_event"):
                self.mouse_controller.trace_event(
                    "eye_tracking_state", ready=state[0], reason=state[1], generation=state[2],
                    recalibration_prompt=state[3], raw=self.eye_tracker.last_raw_position,
                    quality=self.eye_tracker.detection_quality, calibrated=self.eye_tracker.is_calibrated,
                    recovery_count=len(self.eye_tracker.recovery_candidates))
        self.fpga_action_plan = None
        if eye_pos is None:
            self.eye_tracker.current_blink_status = None
            self.eye_tracker.blink_active = False
            self.double_blink_pending = False
            self.pending_click_position = None
            self.last_blink_time = 0
            return eye_pos, None
        blink_result = self.eye_tracker.detect_blink(frame)
        self.eye_tracker.current_blink_status = blink_result
        if self.interaction is not None:
            self.fpga_action_plan = self.interaction.plan(blink_result, self.coord_output_mode)
            self.double_blink_pending = self.fpga_action_plan["pending"]
            if self.fpga_action_plan["expired"]:
                print("   单击完成（未检测到第二次眨眼）")
                self.pending_click_position = None
        return eye_pos, blink_result

    def _mapping_status_text(self):
        reason = getattr(self.eye_tracker, "mapping_invalid_reason", None)
        if reason == "eye_outside_calibrated_range":
            return "当前眼坐标超出可映射范围，鼠标暂停；请保持头部位置，按 C 重新校准。"
        if reason == "invalid_eye_position":
            return "当前眼坐标无效，鼠标暂停。"
        if reason == "calibration_required":
            return "尚未完成屏幕校准，鼠标暂停；请按 C 校准。"
        return "屏幕映射暂不可用，鼠标暂停。"

    def _report_mapping_state(self):
        state = (getattr(self.eye_tracker, "screen_mapping_valid", True),
                 getattr(self.eye_tracker, "mapping_invalid_reason", None))
        if state == getattr(self, "_last_mapping_state", None):
            return
        self._last_mapping_state = state
        if hasattr(self.mouse_controller, "trace_event"):
            mapper = getattr(self.eye_tracker, "mapper", None)
            diagnostics = mapper.snapshot() if mapper is not None else None
            self.mouse_controller.trace_event(
                "gaze_mapping_state", valid=state[0], reason=state[1], mapping=diagnostics)
        if not state[0]:
            print(self._mapping_status_text())

    def _stop_mouth_scroll(self):
        had_mouth_input = self.mouth_scroll_active or self.eye_tracker.mouth_open_counter > 0
        self.mouth_scroll_active = False
        self.eye_tracker.reset_mouth_scroll()
        if had_mouth_input:
            # 同步清除 PL 的计数；零嘴角偏差保留已选方向。
            self.eye_tracker.update_mouth_direction_and_scroll(0.0, 0.0, False)

    def _process_mouth_scroll(self, frame, eye_pos):
        if not (self.mouth_control_enabled and self.enable_control):
            self._stop_mouth_scroll()
            return

        left_change, right_change, is_open, _ = self.eye_tracker.detect_mouth_movement(frame)
        valid = left_change is not None and right_change is not None
        if hasattr(self.mouse_controller, "trace_event"):
            self.mouse_controller.trace_event(
                "mouth_observation", valid=valid, is_open=bool(is_open) if valid else False,
                ratio=self.eye_tracker.mouth_open_ratio, eye_detected=bool(eye_pos))
        if not valid:
            self._stop_mouth_scroll()
            return

        self.mouth_scroll_active = bool(is_open)
        scroll_command, direction_changed = self.eye_tracker.update_mouth_direction_and_scroll(
            left_change, right_change, is_open)
        if direction_changed:
            dir_text = "向上" if self.eye_tracker.mouth_scroll_direction > 0 else "向下"
            print(f"嘴部方向: {dir_text}")
        if scroll_command != 0:
            current_time = time.time()
            if self.interaction is not None or current_time - self.last_scroll_time > self.scroll_interval:
                self.mouse_controller.scroll(scroll_command)
                self.last_scroll_time = current_time

    def _record_calibration_target(self, target_pos):
        # The existing calibrator keeps the final ten valid frames for each
        # target. Their median is robust to a single disturbed final frame.
        samples = np.asarray(self.calibrator.quality_samples, dtype=float)
        if (samples.ndim != 2 or samples.shape[1] != 2 or
                len(samples) == 0 or not np.all(np.isfinite(samples))):
            raise ValueError("校准采样无效，请保持头部稳定后重新校准")
        representative = tuple(float(value) for value in np.median(samples, axis=0))
        self.eye_tracker.add_calibration_point(representative, target_pos)
        if hasattr(self.mouse_controller, "trace_event"):
            self.mouse_controller.trace_event(
                "calibration_target_sample", target_index=self.calibrator.current_target,
                target=list(target_pos), representative=list(representative),
                sample_count=self.calibrator.samples_collected,
                window_samples=samples.tolist(), spread=np.std(samples, axis=0).tolist())
        return representative

    def run_calibration(self):
        """Run calibration process"""
        self._stop_mouth_scroll()
        print("=== 9-Point Calibration Started ===")
        print("Please look at the 9 target points on screen")
        # 关键修复：重置所有校准数据
        self.eye_tracker.calibration_points = []  # 清空校准点
        self.eye_tracker.screen_points = []  # 清空屏幕点
        self.eye_tracker.is_calibrated = False  # 标记为未校准
        self.eye_tracker.calibration_matrix = None  # 清空校准矩阵

        # 重置自适应校准器（如果存在）
        if hasattr(self.eye_tracker, 'adaptive_calibrator'):
            self.eye_tracker.adaptive_calibrator.current_matrix = None

        print("✅ 已清空旧校准数据，开始新的校准...")

        cv2.namedWindow('Calibration', cv2.WND_PROP_FULLSCREEN)
        cv2.setWindowProperty('Calibration', cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

        # 获取真实的屏幕尺寸
        screen_width, screen_height = self.mouse_controller.get_screen_size()

        # 更新校准器的屏幕尺寸参数，确保映射一致
        self.calibrator.screen_width = screen_width
        self.calibrator.screen_height = screen_height


        self.calibrator.start_calibration()

        while self.calibrator.is_calibrating:
            ret, frame = self.cap.read()
            if not ret:
                continue

            # 修复镜像：水平翻转画面
            frame = cv2.flip(frame, 1)
            frame = cv2.resize(frame, self.camera_window_size)

            eye_pos = self.eye_tracker.detect_iris_position(frame)
            target_pos = self.calibrator.get_current_target()

            if eye_pos and target_pos:
                self.calibrator.add_sample(eye_pos)

                #calib_display = self.calibrator.draw_calibration_display(1200, 900)
                calib_display = self.calibrator.draw_calibration_display(screen_width, screen_height)
                cv2.imshow('Calibration', calib_display)

                if self.calibrator.is_target_complete():
                    self._record_calibration_target(target_pos)

                    if not self.calibrator.next_target():
                        break

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        if len(self.eye_tracker.calibration_points) >= 5:
            if self.eye_tracker.perform_calibration():
                calibration_quality = self.eye_tracker.get_calibration_quality()
                print(f"✅ Calibration completed successfully! Quality: {calibration_quality}")
                self.enable_control = True
                # 新增：校准成功后启用嘴部控制
                self.mouth_control_enabled = True
                self.eye_tracker.mouth_control_enabled = True
                if hasattr(self.mouse_controller, "trace_event"):
                    mapper = getattr(self.eye_tracker, "mapper", None)
                    self.mouse_controller.trace_event(
                        "calibration_completed", points=self.eye_tracker.calibration_points,
                        screen_points=self.eye_tracker.screen_points,
                        mapping=mapper.snapshot() if mapper is not None else None)
            else:
                print("❌ Calibration failed")
        else:
            print("⚠️ Not enough calibration data")

        cv2.destroyWindow('Calibration')

        # ===== 将校准网格窗口和人脸窗口都改成小窗口放在角落 =====
        if self.show_calibration_grid:
            screen_w, screen_h = self.mouse_controller.get_screen_size()

            # ===== 1. 设置人脸窗口（Eye Control）=====
            face_width, face_height = 400, 300  # 人脸窗口也改小
            cv2.namedWindow('Eye Control', cv2.WINDOW_NORMAL)
            cv2.resizeWindow('Eye Control', face_width, face_height)

            # 放到屏幕右上角，在校准网格窗口的上方
            face_x = screen_w - face_width - 10  # 距离右边10像素
            face_y = 10  # 距离顶部10像素
            cv2.moveWindow('Eye Control', face_x, face_y)

            # ===== 2. 设置校准网格窗口（Calibration Grid）=====
            grid_width, grid_height = 400, 300
            cv2.namedWindow('Calibration Grid', cv2.WINDOW_NORMAL)
            cv2.resizeWindow('Calibration Grid', grid_width, grid_height)

            # 放到人脸窗口下方
            grid_x = screen_w - grid_width - 10  # 距离右边10像素（与人脸窗口右对齐）
            grid_y = face_y + face_height + 15  # 在人脸窗口下方10像素
            cv2.moveWindow('Calibration Grid', grid_x, grid_y)

            # 刷新显示
            grid_display = self.draw_calibration_grid_window(grid_width, grid_height)
            cv2.imshow('Calibration Grid', grid_display)


    def _refresh_calibration_grid(self, width=1200, height=900):
        """辅助网格最多每秒刷新十次，鼠标控制仍逐帧运行。"""
        refresh_time = time.monotonic()
        last_refresh_time = getattr(self, "_last_grid_refresh_time", None)
        if last_refresh_time is not None and refresh_time - last_refresh_time < 0.1:
            return
        grid_display = self.draw_calibration_grid_window(width, height)
        cv2.imshow('Calibration Grid', grid_display)
        self._last_grid_refresh_time = refresh_time

    def draw_calibration_grid_window(self, width, height):
        """大赛展示专用：视觉优化版预览界面（避开底部文字区）"""

        # 1. 初始化背景
        display = np.zeros((height, width, 3), dtype=np.uint8)
        cv2.rectangle(display, (0, 0), (width, height), (20, 18, 15), -1)

        # 颜色定义 (BGR)
        CYAN = (255, 255, 0)
        DARK_CYAN = (80, 80, 0)
        WHITE = (255, 255, 255)
        ORANGE = (0, 165, 255)
        GRAY = (120, 120, 120)

        # --- 2. 视觉避让布局 ---
        # 我们手动给 y 轴加一个偏移和缩放，让点群在视觉上往上提
        v_offset = 80  # 整体向上平移
        v_scale = 0.6  # 纵向压缩比例，确保最下面一排点在文字框上方

        margin = 0.12
        targets_rel = [
            (margin, margin), (0.5, margin), (1.0 - margin, margin),
            (margin, 0.5), (0.5, 0.5), (1.0 - margin, 0.5),
            (margin, 1.0 - margin), (0.5, 1.0 - margin), (1.0 - margin, 1.0 - margin)
        ]

        for i, (rx, ry) in enumerate(targets_rel):
            tx = int(rx * width)
            # 核心改动：这里的 ty 只用于预览显示，向上提拉避开底部
            ty = int((ry * height * v_scale) + v_offset)

            # 绘制预览点
            cv2.circle(display, (tx, ty), 15, DARK_CYAN, 1, cv2.LINE_AA)
            cv2.circle(display, (tx, ty), 4, CYAN, -1)
            cv2.putText(display, f"CH_{i + 1}", (tx + 20, ty + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, GRAY, 1, cv2.LINE_AA)

        # --- 3. 底部操作指引面板 ---
        font = cv2.FONT_HERSHEY_DUPLEX
        footer_h = 160
        # 绘制半透明深色底框
        cv2.rectangle(display, (0, height - footer_h), (width, height), (35, 30, 25), -1)
        cv2.line(display, (0, height - footer_h), (width, height - footer_h), CYAN, 1)

        # 装饰线
        cv2.line(display, (width // 2, height - footer_h + 30), (width // 2, height - 30), (70, 70, 70), 1)

        # 左侧：系统按键说明 (English Focus)
        cv2.putText(display, "CONTROL PANEL", (40, height - footer_h + 35), font, 0.8, ORANGE, 1, cv2.LINE_AA)
        cv2.putText(display, "> PRESS [C] TO CALIBRATE", (50, height - 85), font, 0.8, WHITE, 1, cv2.LINE_AA)
        cv2.putText(display, "> PRESS [Q]     TO EXIT", (50, height - 40), font, 0.8, WHITE, 1, cv2.LINE_AA)

        # 右侧：动嘴功能说明 (Gesture Focus)
        cv2.putText(display, "GESTURE GUIDE", (width // 2 + 40, height - footer_h + 40), font, 0.8, ORANGE, 1,
                    cv2.LINE_AA)
        cv2.putText(display, "> POUT L/R : CHANGE SCROLL DIR", (width // 2 + 50, height - 85), font, 0.8, WHITE, 1,
                    cv2.LINE_AA)
        cv2.putText(display, "> OPEN MOUTH: START SCROLLING", (width // 2 + 50, height - 40), font, 0.8, WHITE, 1,
                    cv2.LINE_AA)

        # --- 4. 顶部标题区 ---
        cv2.rectangle(display, (0, 0), (width, 90), (30, 25, 20), -1)
        cv2.putText(display, "MULTIMODAL VISION-BASED INTERACTION TERMINAL", (40, 50), font, 1, WHITE, 2, cv2.LINE_AA)

        return display

    def draw_main_interface(self, frame, eye_pos, screen_pos):
        """大赛专供：纯净高对比度 HUD (删除进度条，大幅强化坐标数值)"""
        import time
        h, w = frame.shape[:2]

        # 颜色定义 (BGR)
        CYAN = (255, 255, 0)  # 荧光青
        ORANGE = (0, 165, 255)  # 警告橙
        WHITE = (255, 255, 255)
        BLACK = (0, 0, 0)  # 描边色

        # --- 1. 侧边信息栏背景 (加深加宽) ---
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (350, h), (15, 12, 10), -1)
        cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

        # --- 2. 辅助函数：带描霸的大文字 ---
        font = cv2.FONT_HERSHEY_DUPLEX

        def draw_big_text(img, text, pos, scale, color, thick):
            cv2.putText(img, text, pos, font, scale, BLACK, thick + 2, cv2.LINE_AA)
            cv2.putText(img, text, pos, font, scale, color, thick, cv2.LINE_AA)

        # --- 3. 绘制准星 (保持醒目) ---
        if eye_pos:
            ex, ey = int(eye_pos[0]), int(eye_pos[1])
            length, t = 30, 3
            # 四个护角
            for dx, dy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
                cv2.line(frame, (ex + dx * length, ey + dy * length), (ex + dx * (length - 15), ey + dy * length), CYAN,
                         t)
                cv2.line(frame, (ex + dx * length, ey + dy * length), (ex + dx * length, ey + dy * (length - 15)), CYAN,
                         t)
            # 动态圆环
            cv2.circle(frame, (ex, ey), int(35 + (time.time() * 12) % 15), CYAN, 2)

        # --- 4. 核心数据展示 (删除了进度条，放大坐标) ---

        # 系统状态
        status = "ACTIVE" if self.eye_tracker.is_calibrated else "WAITING"
        color = CYAN if self.eye_tracker.is_calibrated else ORANGE
        draw_big_text(frame, "STATUS:", (25, 60), 0.8, WHITE, 1)
        draw_big_text(frame, status, (150, 60), 1.0, color, 2)

        # 眼睛像素坐标 (EYE_PX) - 显著加大
        draw_big_text(frame, "EYE POSITION", (25, 140), 0.7, WHITE, 1)
        eye_x_str = f"X: {int(eye_pos[0]) if eye_pos else 0}"
        eye_y_str = f"Y: {int(eye_pos[1]) if eye_pos else 0}"
        draw_big_text(frame, eye_x_str, (40, 190), 1.2, CYAN, 2)  # 字号 1.2
        draw_big_text(frame, eye_y_str, (40, 240), 1.2, CYAN, 2)

        # 屏幕投影坐标 (SCR_PX) - 显著加大
        draw_big_text(frame, "SCREEN PROJ", (25, 320), 0.7, WHITE, 1)
        scr_x_str = f"X: {int(screen_pos[0])}"
        scr_y_str = f"Y: {int(screen_pos[1])}"
        draw_big_text(frame, scr_x_str, (40, 370), 1.2, ORANGE, 2)  # 字号 1.2
        draw_big_text(frame, scr_y_str, (40, 420), 1.2, ORANGE, 2)

        # # --- 5. 底部操作提示 ---
        # draw_big_text(frame, "COMMANDS:", (25, h - 110), 0.6, WHITE, 1)
        # draw_big_text(frame, "[C] CALIBRATE", (35, h - 75), 0.7, CYAN, 1)
        # draw_big_text(frame, "[M] MOUSE CTRL", (35, h - 40), 0.7, CYAN, 1)

        return frame

    def input_environment_zero(self):
        """
        启动时通过 Tkinter 窗口设置环境零点。

        坐标单位：mm

        X：右正，左负
        Y：下正，上负（保持与你当前 ZED 坐标一致）
        Z：前正，后负

        X、Y、Z 均允许输入正数、负数或 0。
        """

        root = tk.Tk()
        root.title("环境零点设置")

        win_w = 430
        win_h = 340

        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()

        root.geometry(
            f"{win_w}x{win_h}+{(screen_w - win_w) // 2}+{(screen_h - win_h) // 2}"
        )

        root.resizable(False, False)

        confirmed = {"ok": False}

        # ===== 标题 =====
        tk.Label(
            root,
            text="环境零点设置",
            font=("Microsoft YaHei", 18, "bold")
        ).pack(pady=(18, 8))

        tk.Label(
            root,
            text="请输入环境零点坐标（单位：mm）",
            font=("Microsoft YaHei", 10)
        ).pack()

        frame = tk.Frame(root)
        frame.pack(pady=20)

        # X
        tk.Label(frame, text="X：").grid(row=0, column=0, sticky="e", pady=8)
        x_entry = tk.Entry(frame, width=15)
        x_entry.grid(row=0, column=1, padx=8)
        tk.Label(frame, text="右正 左负").grid(row=0, column=2)

        # Y
        tk.Label(frame, text="Y：").grid(row=1, column=0, sticky="e", pady=8)
        y_entry = tk.Entry(frame, width=15)
        y_entry.grid(row=1, column=1, padx=8)
        tk.Label(frame, text="下正 上负").grid(row=1, column=2)

        # Z
        tk.Label(frame, text="Z：").grid(row=2, column=0, sticky="e", pady=8)
        z_entry = tk.Entry(frame, width=15)
        z_entry.grid(row=2, column=1, padx=8)
        tk.Label(frame, text="前正 后负").grid(row=2, column=2)

        # 默认值（方便直接回车）
        x_entry.insert(0, "0")
        y_entry.insert(0, "0")
        z_entry.insert(0, "0")

        def confirm():
            try:
                self.zero_x = float(x_entry.get())
                self.zero_y = float(y_entry.get())
                self.zero_z = float(z_entry.get())
            except ValueError:
                messagebox.showerror(
                    "输入错误",
                    "X、Y、Z 必须输入数字！",
                    parent=root
                )
                return

            self.zero_initialized = True
            confirmed["ok"] = True

            print("\n" + "=" * 55)
            print("✅ 环境零点设置成功")
            print(
                f"X={self.zero_x:.1f} mm   "
                f"Y={self.zero_y:.1f} mm   "
                f"Z={self.zero_z:.1f} mm"
            )
            print("=" * 55 + "\n")

            root.destroy()

        def cancel():
            confirmed["ok"] = False
            self.zero_initialized = False
            root.destroy()

        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=10)

        tk.Button(
            btn_frame,
            text="确认",
            width=10,
            command=confirm
        ).pack(side="left", padx=10)

        tk.Button(
            btn_frame,
            text="取消",
            width=10,
            command=cancel
        ).pack(side="left", padx=10)

        # 回车确认
        root.bind("<Return>", lambda e: confirm())

        # ESC 取消
        root.bind("<Escape>", lambda e: cancel())

        # 点右上角关闭
        root.protocol("WM_DELETE_WINDOW", cancel)

        x_entry.focus_set()

        root.mainloop()

        return confirmed["ok"]



    def calculate_horizontal_angle(self, target_xyz):
        """
        计算目标点相对于环境零点的水平角。

        target_xyz:
            (X, Y, Z)，单位 mm

        坐标约定：
            X：左负右正
            Y：上正下负
            Z：相机前方为正

        返回：
            水平角度，单位 degree
            左负、右正
        """

        if not self.zero_initialized:
            print("⚠️ 尚未设置环境零点")
            return None

        if target_xyz is None or len(target_xyz) < 3:
            return None

        try:
            target_x = float(target_xyz[0])
            target_y = float(target_xyz[1])
            target_z = float(target_xyz[2])

            # 相对于零点的坐标差
            dx = target_x - self.zero_x
            dz = target_z - self.zero_z

            # Y 不参与水平角计算
            angle_rad = math.atan2(dx, dz)
            angle_deg = math.degrees(angle_rad)

            self.current_target_xyz = (
                target_x,
                target_y,
                target_z
            )

            self.current_horizontal_angle = angle_deg

            return angle_deg

        except Exception as e:
            print(f"❌ 水平角计算错误: {e}")
            return None


    def run(self):
        """Run main system with Blink Test window and coordinate output"""

        # ============================================================
        # 1. 启动时设置环境零点
        # ============================================================
        self.input_environment_zero()

        # ============================================================
        # 2. 初始化摄像头
        # ============================================================
        if not self.initialize_camera():
            return

        print("Eye Control System + Blink Test Started!")
        print("数据记录将在检测到眼睛后自动开始")

        print("Hotkeys:")
        print("  c - Calibrate (9-point)")
        print("  m - Enable/Disable mouse control")
        print("  z - Enable ZED environment mode")
        print("  q - Quit")

        # 输出当前零点q
        print("\n========== 当前环境零点 ==========")
        print(
            f"X0 = {self.zero_x:.1f} mm"
        )
        print(
            f"Y0 = {self.zero_y:.1f} mm"
        )
        print(
            f"Z0 = {self.zero_z:.1f} mm"
        )
        print("==================================\n")

        last_zed_update = 0
        self.is_running = True

        # ============================================================
        # 3. 创建主窗口
        # ============================================================
        cv2.namedWindow('Eye Control', cv2.WINDOW_NORMAL)
        cv2.resizeWindow('Eye Control', 800, 600)

        # ============================================================
        # 4. 创建校准网格窗口
        # ============================================================
        cv2.namedWindow('Calibration Grid', cv2.WINDOW_NORMAL)
        cv2.resizeWindow('Calibration Grid', 1200, 900)

        # ============================================================
        # 5. 获取屏幕尺寸
        # ============================================================
        screen_w, screen_h = self.mouse_controller.get_screen_size()

        # ============================================================
        # 6. 放置主窗口
        # ============================================================
        eye_x = max(
            10,
            (screen_w // 4) - 400 - 150
        )

        eye_y = (screen_h - 600) // 2

        cv2.waitKey(1)
        cv2.moveWindow(
            'Eye Control',
            eye_x,
            eye_y
        )

        # ============================================================
        # 7. 放置校准网格窗口
        # ============================================================
        grid_x = (screen_w // 2) - 600
        grid_y = (screen_h - 900) // 2

        cv2.moveWindow(
            'Calibration Grid',
            grid_x,
            grid_y
        )

        self.debug_counter = 0

        # ============================================================
        # 8. 眨眼相关状态
        # ============================================================
        self.last_action_time = 0
        self.last_blink_time = 0
        self.double_blink_window = 1.5
        self.double_blink_pending = False
        self.pending_click_position = None

        if self.interaction is not None:
            self.interaction.reset_action()

        # ============================================================
        # 9. 帧计数
        # ============================================================
        frame_count = 0

        last_screen_pos = (
            screen_w // 2,
            screen_h // 2
        )

        # ============================================================
        # 10. 眼睛检测与数据记录状态
        # ============================================================
        eye_detected_once = False
        recording_started = False
        waiting_counter = 0

        try:

            # ========================================================
            # 主循环
            # ========================================================
            while self.is_running:

                # ----------------------------------------------------
                # 读取摄像头
                # ----------------------------------------------------
                ret, frame = self.cap.read()

                if not ret:
                    break

                # ----------------------------------------------------
                # 水平镜像
                # ----------------------------------------------------
                frame = cv2.flip(frame, 1)

                # ----------------------------------------------------
                # 调整画面尺寸
                # ----------------------------------------------------
                frame = cv2.resize(
                    frame,
                    self.camera_window_size
                )

                frame_count += 1

                current_time = time.time()

                # ====================================================
                # 11. 检测眨眼
                # ====================================================
                eye_pos, blink_result = self._prepare_eye_frame(frame)
                if blink_result and hasattr(self.mouse_controller, "trace_event"):
                    self.mouse_controller.trace_event(
                        "blink_detected", kind=blink_result,
                        coord_output_mode=self.coord_output_mode,
                        enable_control=self.enable_control,
                        pending=self.double_blink_pending,
                        blink_interval=current_time - self.last_blink_time,
                        action_age=current_time - self.last_action_time,
                        metrics=self.eye_tracker.last_blink_observation
                    )

                # ====================================================
                # 12. 眨眼动作处理
                # ====================================================
                BLINK_COOLDOWN = 0.35

                if (self.fpga_action_plan is not None and self.fpga_action_plan["action"] != 0) or (
                    self.interaction is None and
                    blink_result
                    and
                    (
                        current_time - self.last_action_time > BLINK_COOLDOWN
                        or (
                            blink_result == "double"
                            and not self.coord_output_mode
                            and self.double_blink_pending
                            and 0.15 < current_time - self.last_blink_time < self.double_blink_window
                        )
                    )
                ):

                    # ==================================================
                    # 双眼眨眼
                    # ==================================================
                    if blink_result == "double":

                        interval = (
                            current_time
                            - self.last_blink_time
                        )

                        # ==================================================
                        # ZED 环境坐标模式
                        # ==================================================
                        if self.coord_output_mode:

                            # ------------------------------------------
                            # 获取当前系统鼠标位置
                            # ------------------------------------------
                            mx, my = pyautogui.position()

                            print("\n")
                            print("=" * 60)
                            print("👁️ 眨眼确认环境目标")
                            print(
                                f"鼠标屏幕位置："
                                f"X={mx}, Y={my}"
                            )

                            # ------------------------------------------
                            # 根据屏幕坐标获取 ZED 三维坐标
                            # ------------------------------------------
                            resp = self.send_zed_command({
                                'cmd': 'get_coord',
                                'x': mx,
                                'y': my
                            })

                            # ==================================================
                            # 坐标有效
                            # ==================================================
                            if resp and 'error' not in resp:

                                x_mm = resp['x_mm']
                                y_mm = resp['y_mm']
                                z_mm = resp['z_mm']
                                dist_m = resp['distance_m']

                                # --------------------------------------
                                # 保存当前目标三维坐标
                                # --------------------------------------
                                self.current_target_xyz = (
                                    x_mm,
                                    y_mm,
                                    z_mm
                                )

                                # --------------------------------------
                                # 计算相对于环境零点的水平角
                                # --------------------------------------
                                angle = self.calculate_horizontal_angle(
                                    (
                                        x_mm,
                                        y_mm,
                                        z_mm
                                    )
                                )


                                # ==================================================
                                # 输出结果
                                # ==================================================
                                print(
                                    f"🌍 目标世界坐标："
                                    f"X={x_mm:.1f}mm, "
                                    f"Y={y_mm:.1f}mm, "
                                    f"Z={z_mm:.1f}mm, "
                                    f"距离={dist_m:.3f}m"
                                )

                                print(
                                    f"📍 环境零点："
                                    f"X0={self.zero_x:.1f}mm, "
                                    f"Y0={self.zero_y:.1f}mm, "
                                    f"Z0={self.zero_z:.1f}mm"
                                )

                                # --------------------------------------
                                # 输出相对坐标
                                # --------------------------------------
                                dx = x_mm - self.zero_x
                                dy = y_mm - self.zero_y
                                dz = z_mm - self.zero_z

                                print(
                                    f"📐 相对零点坐标："
                                    f"ΔX={dx:.1f}mm, "
                                    f"ΔY={dy:.1f}mm, "
                                    f"ΔZ={dz:.1f}mm"
                                )

                                # --------------------------------------
                                # 最终水平角
                                # --------------------------------------
                                if angle is not None:

                                    if angle > 0:
                                        direction = "右"
                                    elif angle < 0:
                                        direction = "左"
                                    else:
                                        direction = "正前方"

                                    print(
                                        f"🎯 水平角度："
                                        f"{angle:+.2f}°"
                                        f"  ({direction})"
                                    )

                                print("=" * 60)
                                print()

                            else:

                                print(
                                    "❌ 坐标无效"
                                    "（深度无数据或超出范围）"
                                )

                            # ------------------------------------------
                            # 环境坐标模式下：
                            # 一次眨眼完成一次测量
                            # ------------------------------------------
                            self.double_blink_pending = False

                            self.last_action_time = (
                                current_time + 0.3
                            )
                            self._ack_fpga_action(bool(resp and 'error' not in resp))

                        # ==================================================
                        # 普通鼠标模式
                        # ==================================================
                        else:

                            interval = (
                                current_time
                                - self.last_blink_time
                            )

                            # ------------------------------------------
                            # 双击
                            # ------------------------------------------
                            if (self.interaction is not None and self.fpga_action_plan["action"] == 2) or (
                                self.interaction is None and
                                self.double_blink_pending
                                and
                                (0.15 < interval < self.double_blink_window)
                            ):

                                sent = self.mouse_controller.double_click(
                                    self.pending_click_position
                                )

                                print(
                                    "[双击已发送] 检测到快速连眨"
                                    if sent else
                                    "[双击发送失败] 请查看上面的错误"
                                )

                                print(
                                    f"   间隔: "
                                    f"{interval:.2f}秒"
                                )

                                self.double_blink_pending = False
                                self.pending_click_position = None

                                self.last_action_time = (
                                    time.time() + 0.3
                                )
                                self._ack_fpga_action(sent)

                            # ------------------------------------------
                            # 单击
                            # ------------------------------------------
                            else:

                                time.sleep(0.02)

                                sent = self.mouse_controller.click()
                                print(
                                    "[单击已发送] 触发左键"
                                    if sent else
                                    "[单击发送失败] 请查看上面的错误"
                                )

                                self.double_blink_pending = bool(sent)
                                self.pending_click_position = (
                                    self.mouse_controller.last_click_position
                                    if sent else None
                                )

                                self.last_blink_time = current_time

                                self.last_action_time = current_time
                                self._ack_fpga_action(sent)

                                if sent:
                                    print("   等待第二次眨眼...")

                    # ==================================================
                    # 右眼眨眼 → 右键
                    # ==================================================
                    elif blink_result == "right":

                        print(
                            "[执行右键] "
                            "检测到右眼单眨"
                        )

                        time.sleep(0.02)

                        try:
                            if hasattr(self.mouse_controller, "trace_event"):
                                self.mouse_controller.trace_event(
                                    "right_call_start", metrics=self.eye_tracker.last_blink_observation)
                            pyautogui.rightClick()
                            if hasattr(self.mouse_controller, "trace_event"):
                                self.mouse_controller.trace_event("right_call_returned", method="pyautogui")
                        except Exception as error:
                            if hasattr(self.mouse_controller, "trace_event"):
                                self.mouse_controller.trace_event("right_call_failed", error=str(error))
                            self._ack_fpga_action(False)
                            raise

                        self.double_blink_pending = False
                        self.pending_click_position = None

                        self.last_action_time = current_time
                        self._ack_fpga_action(True)

                # ====================================================
                # 13. 清理双击超时状态
                # ====================================================
                if (
                    self.interaction is None
                    and
                    self.double_blink_pending
                    and
                    (
                        current_time
                        - self.last_blink_time
                        >= self.double_blink_window
                    )
                ):

                    print(
                        "   单击完成（未检测到第二次眨眼）"
                    )

                    self.double_blink_pending = False
                    self.pending_click_position = None

                # ====================================================
                # 14. 虹膜已在眨眼动作之前检测
                # ====================================================
                screen_size = (
                    self.mouse_controller
                    .get_screen_size()
                )

                # ====================================================
                # 15. 嘴部滚轮控制
                # ====================================================
                self._process_mouth_scroll(frame, eye_pos)

                # ====================================================
                # 16. 第一次检测到眼睛
                # ====================================================
                if eye_pos and not eye_detected_once:

                    eye_detected_once = True

                    recording_started = True

                    print(
                        f"检测到眼睛！"
                        f"从第{frame_count}帧开始记录数据"
                    )

                    print(
                        f"眼睛初始位置: "
                        f"({eye_pos[0]:.1f}, "
                        f"{eye_pos[1]:.1f})"
                    )

                # ====================================================
                # 17. 尚未检测到眼睛
                # ====================================================
                if not eye_detected_once:

                    waiting_counter += 1

                    if waiting_counter % 30 == 0:

                        print(
                            f"等待检测眼睛..."
                            f"（第{frame_count}帧）"
                        )

                    cv2.putText(
                        frame,
                        "WAITING FOR EYE DETECTION...",
                        (
                            frame.shape[1] // 2 - 200,
                            frame.shape[0] // 2
                        ),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (0, 255, 255),
                        2
                    )

                    cv2.putText(
                        frame,
                        "Please position your face in front of camera",
                        (
                            frame.shape[1] // 2 - 250,
                            frame.shape[0] // 2 + 40
                        ),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        (0, 255, 255),
                        1
                    )

                    screen_pos = (
                        screen_size[0] // 2,
                        screen_size[1] // 2
                    )

                    last_screen_pos = screen_pos

                # ====================================================
                # 18. 已经检测到眼睛
                # ====================================================
                else:

                    if eye_pos:

                        screen_pos = (
                            self.eye_tracker
                            .to_screen_coordinates(
                                eye_pos,
                                screen_size
                            )
                        )

                        last_screen_pos = screen_pos
                        self._report_mapping_state()

                        # ----------------------------------------------
                        # 数据记录
                        # ----------------------------------------------
                        if recording_started:

                            self.write_coordinates_to_file(
                                eye_pos,
                                screen_pos,
                                frame_count
                            )

                    else:

                        screen_pos = last_screen_pos

                        if recording_started:

                            self.write_coordinates_to_file(
                                None,
                                screen_pos,
                                frame_count
                            )

                    # ==================================================
                    # 19. 控制台坐标输出
                    # ==================================================
                    self.debug_counter += 1

                    if self.debug_counter % 30 == 0:

                        if eye_pos:

                            if getattr(self.eye_tracker, "screen_mapping_valid", True):
                                print(f"屏幕映射位置：({screen_pos[0]:.1f}, {screen_pos[1]:.1f})")
                            else:
                                print(f"已检测到眼睛 ({eye_pos[0]:.1f}, {eye_pos[1]:.1f})，{self._mapping_status_text()}")

                        else:

                            reason = getattr(self.eye_tracker, "tracking_loss_reason", "no_face")
                            status = {"recovering": "眼睛已检测到，正在确认恢复",
                                      "no_face": "未检测到人脸",
                                      "low_quality": "眼睛检测质量不足"}.get(reason, "眼睛检测暂不可用")
                            print(f"{status}，鼠标暂停")

                    # ==================================================
                    # 20. 绘制界面 + 鼠标控制
                    # ==================================================
                    if eye_pos:

                        frame = (
                            self.draw_main_interface(
                                frame,
                                eye_pos,
                                screen_pos
                            )
                        )

                        if not getattr(self.eye_tracker, "screen_mapping_valid", True):
                            cv2.putText(frame, "Gaze mapping unavailable - press C",
                                        (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)

                        if self.enable_control and not self.mouth_scroll_active and not getattr(
                            self.eye_tracker, "right_wink_candidate_active", False
                        ) and not (
                            not self.coord_output_mode and self.double_blink_pending
                        ) and getattr(self.eye_tracker, "screen_mapping_valid", True):

                            self.mouse_controller.move(
                                screen_pos
                            )

                    else:

                        cv2.putText(
                            frame,
                            ("Eyes detected - confirming recovery"
                             if self.eye_tracker.tracking_loss_reason == "recovering" else
                             "No valid eye detection - cursor paused"),
                            (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.6,
                            (0, 0, 255),
                            2
                        )

                # ====================================================
                # 21. 显示窗口
                # ====================================================
                cv2.imshow(
                    'Eye Control',
                    frame
                )

                if self.show_calibration_grid:
                    self._refresh_calibration_grid()

                # ====================================================
                # 22. 处理全局 ESC
                # ====================================================
                if self.zed_esc_request:
                    self.zed_esc_request = False
                    if self.coord_output_mode or self.zed_process is not None:
                        self.stop_zed_subprocess()
                        print("✅ 已通过全局 ESC 退出 ZED 环境模式")
                # ====================================================
                # 23. 键盘
                # ====================================================
                key = cv2.waitKey(1) & 0xFF

                if key == ord('q'):

                    break

                elif key == ord('c'):

                    self.eye_tracker.reset_recalibration_prompt()

                    self.run_calibration()

                elif key == ord('m'):

                    self.enable_control = (
                        not self.enable_control
                    )

                    print(
                        f"Mouse Control: "
                        f"{'ENABLED' if self.enable_control else 'DISABLED'}"
                    )

                # ====================================================
                # 24. Z：进入ZED环境模式
                # ====================================================
                elif key == ord('z'):
                    if not self.coord_output_mode:
                        if self.zed_process is None or self.zed_process.poll() is not None:
                            if not self.start_zed_subprocess():
                                continue

                        resp = self.send_zed_command({'cmd': 'show_window'})

                        if resp and resp.get('status') == 'ok':
                            self.coord_output_mode = True
                            self.zed_window_visible = True
                            # mx, my = pyautogui.position()
                            # self.send_zed_command({
                            #     'cmd': 'get_coord',
                            #     'x': mx,
                            #     'y': my
                            # })
                            print("🔘 ZED 环境模式已启动，窗口已显示。按 ESC 退出。")
                # ====================================================
                # 25. ESC
                # ====================================================
                elif key == 27:
                    if self.coord_output_mode or self.zed_process is not None:
                        self.stop_zed_subprocess()
                        print("✅ 已退出 ZED 环境模式，眼控鼠标继续运行")
                    else:
                        print("ZED 环境未开启，无需退出")
                # ====================================================
                # 26. ZED实时坐标更新
                # ====================================================
                #
                # 注意：
                # 你原来这里写的是：
                #
                # if self.coord_output_mode and self.zed_socket:
                #
                # 但你现在实际上使用的是 UDP socket，
                # 而不是 zed_socket。
                #
                # 因此这里改成：
                #
                # self.udp_sock is not None
                #


        # ============================================================
        # 异常处理
        # ============================================================
        except Exception as e:

            print(
                f"Runtime error: {e}"
            )
            if self.interaction is not None:
                # Make a failed hardware runtime visible to the launcher;
                # the original finally block still releases all surfaces.
                raise

        # ============================================================
        # 最终清理
        # ============================================================
        finally:

            keyboard.unhook_all()

            self.close_output_file()

            self.stop_zed_subprocess()

            self.cap.release()

            cv2.destroyAllWindows()


            # ========================================================
            # 最终统计
            # ========================================================
            if eye_detected_once:

                print(
                    f"数据记录完成: "
                    f"共{self.output_counter}帧"
                )

            else:

                print(
                    "未检测到眼睛，"
                    "数据记录未开始"
                )

            print("System closed")


def main():
    system = EyeControlSystem()
    system.run()


if __name__ == "__main__":
    main()
