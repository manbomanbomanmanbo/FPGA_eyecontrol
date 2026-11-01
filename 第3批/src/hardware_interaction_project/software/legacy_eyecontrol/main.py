import cv2
import time
import os
import sys
import numpy as np
import pyautogui
#
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


from datetime import datetime
from .eye_tracker import EyeTracker
from .mouse_controller import MouseController
from .calibrator import Calibrator


class EyeControlSystem:
    def __init__(self, eye_tracker=None, mouse_controller=None, on_frame=None):
        self.eye_tracker = EyeTracker() if eye_tracker is None else eye_tracker
        self.mouse_controller = MouseController() if mouse_controller is None else mouse_controller
        self.on_frame = on_frame
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
        self.last_scroll_time = 0
        self.scroll_interval = 0.01  # 滚动间隔（秒）

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
            self.output_file.flush()

            self.output_counter += 1

        except Exception as e:
            print(f"❌ 写入文件错误: {e}")

    def initialize_camera(self):
        """Initialize camera """
        self.cap = cv2.VideoCapture(getattr(self, 'camera_index', 0))  # 使用摄像头
        if not self.cap.isOpened():
            print("Cannot open camera ")
            return False

        # 只设置分辨率，其他全部使用系统默认值
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        print("Camera initialized successfully")
        return True

    def run_calibration(self):
        """Run calibration process"""
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
            if self.on_frame is not None:
                self.on_frame()
            if not ret:
                if cv2.waitKey(1) & 0xFF in (ord('q'), ord('Q')):
                    self.is_running = False
                    break
                try:
                    if cv2.getWindowProperty('Calibration', cv2.WND_PROP_VISIBLE) < 1:
                        self.is_running = False
                        break
                except cv2.error:
                    self.is_running = False
                    break
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
                    self.eye_tracker.add_calibration_point(eye_pos, target_pos)

                    if not self.calibrator.next_target():
                        break

            if cv2.waitKey(1) & 0xFF in (ord('q'), ord('Q')):
                self.is_running = False
                break
            try:
                if cv2.getWindowProperty('Calibration', cv2.WND_PROP_VISIBLE) < 1:
                    self.is_running = False
                    break
            except cv2.error:
                self.is_running = False
                break

        if not self.is_running:
            try:
                cv2.destroyWindow('Calibration')
            except cv2.error:
                pass
            return

        if len(self.eye_tracker.calibration_points) >= 5:
            if self.eye_tracker.perform_calibration():
                calibration_quality = self.eye_tracker.get_calibration_quality()
                print(f"✅ Calibration completed successfully! Quality: {calibration_quality}")
                self.enable_control = True
                # 新增：校准成功后启用嘴部控制
                self.mouth_control_enabled = True
                self.eye_tracker.mouth_control_enabled = True
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

    def run(self):
        """Run main system with Blink Test window and coordinate output"""
        if not self.initialize_camera():
            return

        print("Eye Control System + Blink Test Started!")
        print("数据记录将在检测到眼睛后自动开始")
        print("Hotkeys:")
        print("  c - Calibrate (9-point)")
        print("  q - Quit")


        self.is_running = True

        # 创建主窗口
        cv2.namedWindow('Eye Control', cv2.WINDOW_NORMAL)
        cv2.resizeWindow('Eye Control', 800, 600)

        # 创建校准网格窗口
        cv2.namedWindow('Calibration Grid', cv2.WINDOW_NORMAL)
        cv2.resizeWindow('Calibration Grid', 1200, 900)

        # 获取屏幕尺寸
        screen_w, screen_h = self.mouse_controller.get_screen_size()

        # 放置主窗口
        eye_x = max(10, (screen_w // 4) - 400 - 150)
        eye_y = (screen_h - 600) // 2
        cv2.waitKey(1)
        cv2.moveWindow('Eye Control', eye_x, eye_y)

        # 放置校准网格窗口
        grid_x = (screen_w // 2) - 600
        grid_y = (screen_h - 900) // 2
        cv2.moveWindow('Calibration Grid', grid_x, grid_y)
        self.debug_counter = 0  # 用于坐标输出

        self.last_action_time = 0  # 全局动作冷却（防止信号重叠）
        self.last_blink_time = 0  # 记录第一次眨眼的时间戳
        self.double_blink_pending = False  # 标记是否正在等待第二次眨眼

        frame_count = 0  # 新增：总帧数计数器
        last_screen_pos = (screen_w // 2, screen_h // 2)  # 用于记录上一次的屏幕位置

        # 添加标志：只在眼睛被检测到时开始记录
        eye_detected_once = False  # 眼睛是否被检测到过
        recording_started = False  # 记录是否已开始
        waiting_counter = 0  # 等待检测的帧数计数器

        try:
            while self.is_running:
                ret, frame = self.cap.read()
                if self.on_frame is not None:
                    self.on_frame()
                if not ret:
                    break

                frame = cv2.flip(frame, 1)
                frame = cv2.resize(frame, self.camera_window_size)

                frame_count += 1  # 帧数递增

                current_time = time.time()

                # 检测眨眼信号
                blink_result = self.eye_tracker.detect_blink(frame)

                # --- 新增：将眨眼状态传递给 EyeTracker，用于坐标锁定 ---
                self.eye_tracker.current_blink_status = blink_result

                # --- 核心点击处理逻辑 (带增强的防抖和双击判定) ---
                # 增加冷却时间到0.35秒，确保眨眼完全结束
                BLINK_COOLDOWN = 0.35

                if blink_result and (current_time - self.last_action_time > BLINK_COOLDOWN):

                    if blink_result == "double":
                        # 计算与上一次信号的时间差
                        interval = current_time - self.last_blink_time

                        # 如果是第二次眨眼且间隔在合理范围内 (0.15s - 0.8s)
                        if self.double_blink_pending and (0.15 < interval < 0.8):
                            print("[执行双击] 检测到快速连眨")
                            self.mouse_controller.double_click()  # 使用已有的 mouse_controller

                            print(f"   间隔: {interval:.2f}秒")

                            self.double_blink_pending = False
                            # 双击后延长冷却，防止残留动作
                            self.last_action_time = current_time + 0.3
                        else:
                            # 判定为第一次眨眼或普通单击
                            print("[执行单击] 触发左键")

                            # 单击前稍微延迟，确保鼠标稳定
                            time.sleep(0.02)
                            self.mouse_controller.click()

                            # 记录第一次眨眼时间
                            self.double_blink_pending = True
                            self.last_blink_time = current_time
                            self.last_action_time = current_time
                            print(f"   等待第二次眨眼...")

                    elif blink_result == "right":
                        # 判定为挤右眼
                        print("[执行右键] 检测到右眼单眨")

                        # 右键前稍微延迟
                        time.sleep(0.02)
                        self.mouse_controller.right_click()

                        # 右击后立即重置双击判定
                        self.double_blink_pending = False
                        self.last_action_time = current_time

                # 自动清理超时的双击状态
                if self.double_blink_pending and (current_time - self.last_blink_time > 0.8):
                    print("   双击等待超时，重置")
                    self.double_blink_pending = False

                # 检测虹膜位置（用于左边Eye Control画面）
                eye_pos = self.eye_tracker.detect_iris_position(frame)
                screen_size = self.mouse_controller.get_screen_size()

                # ===== 嘴部滚轮控制 =====
                if self.mouth_control_enabled and self.enable_control and eye_pos:
                    # 检测嘴巴 - 现在返回四个值
                    left_change, right_change, is_mouth_open, mouth_width = self.eye_tracker.detect_mouth_movement(frame)

                    if left_change is not None:
                        scroll_command, direction_changed = self.eye_tracker.update_mouth_direction_and_scroll(
                            left_change, right_change, is_mouth_open
                        )

                        if direction_changed:
                            dir_text = "向上" if self.eye_tracker.mouth_scroll_direction > 0 else "向下"
                            print(f"嘴部方向: {dir_text}")

                        # 执行滚动
                        if scroll_command != 0:
                            current_time = time.time()
                            if current_time - self.last_scroll_time > self.scroll_interval:
                                self.mouse_controller.scroll(scroll_command)
                                self.last_scroll_time = current_time
                # ===== 嘴部滚轮控制结束 =====

                # 检查是否第一次检测到眼睛
                if eye_pos and not eye_detected_once:
                    eye_detected_once = True
                    recording_started = True
                    print(f"检测到眼睛！从第{frame_count}帧开始记录数据")
                    print(f"眼睛初始位置: ({eye_pos[0]:.1f}, {eye_pos[1]:.1f})")

                # 如果没有检测到眼睛且还未开始记录，显示等待信息
                if not eye_detected_once:
                    waiting_counter += 1
                    if waiting_counter % 30 == 0:  # 每30帧显示一次等待信息
                        print(f"等待检测眼睛... (第{frame_count}帧)")

                    # 在界面上显示等待提示
                    cv2.putText(frame, "WAITING FOR EYE DETECTION...",
                                (frame.shape[1] // 2 - 200, frame.shape[0] // 2),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
                    cv2.putText(frame, "Please position your face in front of camera",
                                (frame.shape[1] // 2 - 250, frame.shape[0] // 2 + 40),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)

                    # 使用屏幕中心作为默认位置
                    screen_pos = (screen_size[0] // 2, screen_size[1] // 2)
                    last_screen_pos = screen_pos

                else:
                    # 已经检测到眼睛，正常处理
                    if eye_pos:

                        screen_pos = self.eye_tracker.to_screen_coordinates(eye_pos, screen_size)
                        last_screen_pos = screen_pos

                        # 记录坐标到文件（只在眼睛检测到后开始记录）
                        if recording_started:
                            self.write_coordinates_to_file(eye_pos, screen_pos, frame_count)
                    else:
                        # 如果检测不到眼睛，使用上一次的位置
                        screen_pos = last_screen_pos

                        # 仍然记录数据，但眼睛坐标为0
                        if recording_started:
                            self.write_coordinates_to_file(None, screen_pos, frame_count)

                    # 坐标输出到控制台，每30帧打印一次
                    self.debug_counter += 1
                    if self.debug_counter % 30 == 0:
                        if eye_pos:
                            print(
                                f"屏幕鼠标位置: ({screen_pos[0]:.1f}, {screen_pos[1]:.1f})")
                        else:
                            print(f"眼睛检测丢失，使用上次位置: ({screen_pos[0]:.1f}, {screen_pos[1]:.1f})")

                    # 绘制界面并控制鼠标
                    if eye_pos:
                        frame = self.draw_main_interface(frame, eye_pos, screen_pos)
                        if self.enable_control:
                            self.mouse_controller.move(screen_pos)

                    else:
                        cv2.putText(frame, "No eye detected", (20, 40),
                                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)

                # 显示两个窗口
                cv2.imshow('Eye Control', frame)

                # 显示校准网格窗口（如果启用）
                if self.show_calibration_grid:
                    grid_display = self.draw_calibration_grid_window(1200, 900)
                    cv2.imshow('Calibration Grid', grid_display)

                # 键盘操作
                key = cv2.waitKey(1) & 0xFF
                try:
                    if (cv2.getWindowProperty('Eye Control', cv2.WND_PROP_VISIBLE) < 1 or
                            (self.show_calibration_grid and
                             cv2.getWindowProperty('Calibration Grid', cv2.WND_PROP_VISIBLE) < 1)):
                        self.is_running = False
                        break
                except cv2.error:
                    self.is_running = False
                    break
                if key in (ord('q'), ord('Q')):
                    self.is_running = False
                    break
                elif key in (ord('c'), ord('C')):
                    self.eye_tracker.reset_recalibration_prompt()
                    self.run_calibration()
        except Exception as e:
            print(f"Runtime error: {e}")
        finally:
            # 关闭输出文件
            self.close_output_file()
            self.cap.release()
            cv2.destroyAllWindows()

            # 显示最终统计
            if eye_detected_once:
                print(f"数据记录完成: 共{self.output_counter}帧")
            else:
                print("未检测到眼睛，数据记录未开始")
            print("System closed")


def main():
    system = EyeControlSystem()
    system.run()


if __name__ == "__main__":
    main()
