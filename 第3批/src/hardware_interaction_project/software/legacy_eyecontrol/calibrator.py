import cv2
import numpy as np
import time
from typing import Tuple


class Calibrator:
    def __init__(self, screen_size: Tuple[int, int]):
        self.screen_width, self.screen_height = screen_size
        # 更精确的居中9点校准网格
        margin = 0.12  # 边距比例
        self.calibration_targets = [
            (margin, margin),  # 左上
            (0.5, margin),  # 上中
            (1.0 - margin, margin),  # 右上
            (margin, 0.5),  # 左中
            (0.5, 0.5),  # 中心
            (1.0 - margin, 0.5),  # 右中
            (margin, 1.0 - margin),  # 左下
            (0.5, 1.0 - margin),  # 下中
            (1.0 - margin, 1.0 - margin)  # 右下
        ]
        self.current_target = 0
        self.samples_collected = 0
        self.samples_per_target = 40
        self.is_calibrating = False
        self.quality_samples = []

    def start_calibration(self):
        """开始校准"""
        self.is_calibrating = True
        self.current_target = 0
        self.samples_collected = 0
        self.quality_samples = []
        self.start_time = time.time()
        print("开始9点校准...")
        print("请保持头部稳定，注视每个目标点直到完成")

    def get_current_target(self) -> Tuple[int, int]:
        """获取当前目标点屏幕坐标"""
        if self.current_target < len(self.calibration_targets):
            x_ratio, y_ratio = self.calibration_targets[self.current_target]
            x = int(x_ratio * self.screen_width)
            y = int(y_ratio * self.screen_height)
            return (x, y)
        return None

    def add_sample(self, eye_pos: Tuple[float, float]):
        """添加样本并评估质量"""
        if self.is_calibrating:
            self.samples_collected += 1
            self.quality_samples.append(eye_pos)

            if len(self.quality_samples) > 10:
                self.quality_samples.pop(0)

    def get_sample_quality(self):
        """评估当前样本质量"""
        if len(self.quality_samples) < 5:
            return "LOW"

        x_values = [p[0] for p in self.quality_samples]
        y_values = [p[1] for p in self.quality_samples]

        x_std = np.std(x_values)
        y_std = np.std(y_values)

        if x_std < 5 and y_std < 5:
            return "HIGH"
        elif x_std < 10 and y_std < 10:
            return "MEDIUM"
        else:
            return "LOW"

    def is_target_complete(self) -> bool:
        """当前目标点是否完成"""
        return self.samples_collected >= self.samples_per_target

    def next_target(self) -> bool:
        """移动到下一个目标点"""
        if self.is_target_complete():
            quality = self.get_sample_quality()
            print(f"完成点 {self.current_target + 1}/9 - 质量: {quality}")

            self.current_target += 1
            self.samples_collected = 0
            self.quality_samples = []

            if self.current_target >= len(self.calibration_targets):
                self.is_calibrating = False
                print("所有校准点完成！")
                return False
            return True
        return False

    def get_progress(self):
        """获取进度信息"""
        return self.current_target + 1, len(self.calibration_targets), self.samples_collected

    # def draw_calibration_display(self, width=1000, height=800):
    #     """绘制校准显示界面 - 修复居中问题"""
    #     # 创建校准显示窗口 - 使用黑色背景
    #     display = np.ones((height, width, 3), dtype=np.uint8) * 30  # 深灰色背景
    #
    #     target_pos = self.get_current_target()
    #     if target_pos:
    #         # 将屏幕坐标映射到显示窗口 - 修复居中计算
    #         display_x = int((target_pos[0] / self.screen_width) * (width * 0.9) + width * 0.05)
    #         display_y = int((target_pos[1] / self.screen_height) * (height * 0.9) + height * 0.05)
    #
    #         # 添加调试信息显示实际坐标
    #         cv2.putText(display, f"Screen: ({target_pos[0]}, {target_pos[1]})",
    #                     (700, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    #         cv2.putText(display, f"Display: ({display_x}, {display_y})",
    #                     (500, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    #
    #         # 根据样本质量改变目标点颜色和大小
    #         quality = self.get_sample_quality()
    #         if quality == "HIGH":
    #             color = (0, 255, 0)
    #             inner_radius = 40
    #             outer_radius = 60
    #         elif quality == "MEDIUM":
    #             color = (0, 255, 255)
    #             inner_radius = 35
    #             outer_radius = 55
    #         else:
    #             color = (0, 0, 255)
    #             inner_radius = 30
    #             outer_radius = 50
    #
    #         # 绘制目标点
    #         cv2.circle(display, (display_x, display_y), inner_radius, color, -1)
    #         cv2.circle(display, (display_x, display_y), outer_radius, (255, 255, 255), 6)
    #
    #         # 绘制十字准星
    #         cross_size = 80
    #         cv2.line(display, (display_x - cross_size, display_y),
    #                  (display_x + cross_size, display_y), (255, 255, 255), 3)
    #         cv2.line(display, (display_x, display_y - cross_size),
    #                  (display_x, display_y + cross_size), (255, 255, 255), 3)
    #
    #         # 显示进度信息 - 调整位置到左上角
    #         current, total, samples = self.get_progress()
    #         cv2.putText(display, f"Calibration Point: {current}/{total}", (50, 60),
    #                     cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
    #         cv2.putText(display, f"Samples: {samples}/{self.samples_per_target}", (50, 120),
    #                     cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    #         cv2.putText(display, f"Quality: {quality}", (50, 180),
    #                     cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 3)
    #         cv2.putText(display, "Keep head still and look at the center", (50, 240),
    #                     cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    #
    #         # 绘制进度条 - 调整位置
    #         progress = samples / self.samples_per_target
    #         bar_width = 800
    #         bar_height = 20
    #         bar_x = (width - bar_width) // 2  # 居中
    #         bar_y = height - 40
    #
    #         cv2.rectangle(display, (bar_x, bar_y),
    #                       (int(bar_x + bar_width * progress), bar_y + bar_height), color, -1)
    #         cv2.rectangle(display, (bar_x, bar_y),
    #                       (bar_x + bar_width, bar_y + bar_height), (255, 255, 255), 3)
    #
    #         # 添加百分比显示
    #         cv2.putText(display, f"{progress * 100:.0f}%",
    #                     (bar_x + bar_width + 20, bar_y + bar_height // 2 + 10),
    #                     cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    #
    #     return display

    def draw_calibration_display(self, width=1000, height=800):
        """大赛专供：科幻实验室校准界面"""
        # 1. 创建深色背景 (深蓝黑色调)
        display = np.zeros((height, width, 3), dtype=np.uint8)
        bg_color = (20, 15, 10)  # 极深蓝黑
        cv2.rectangle(display, (0, 0), (width, height), bg_color, -1)

        # 绘制装饰性背景网格
        grid_size = 80
        for i in range(0, width, grid_size):
            cv2.line(display, (i, 0), (i, height), (40, 35, 30), 1)
        for j in range(0, height, grid_size):
            cv2.line(display, (0, j), (width, j), (40, 35, 30), 1)

        target_pos = self.get_current_target()
        if target_pos:
            # 将屏幕坐标映射到显示窗口
            display_x = int((target_pos[0] / self.screen_width) * (width * 0.9) + width * 0.05)
            display_y = int((target_pos[1] / self.screen_height) * (height * 0.9) + height * 0.05)

            # 颜色定义
            CYAN = (255, 255, 0)
            ORANGE = (0, 165, 255)
            WHITE = (255, 255, 255)

            # 2. 根据样本质量动态调整 UI 表现
            quality = self.get_sample_quality()
            if quality == "HIGH":
                main_color = CYAN
                ring_speed = 15
                shaking = 0
            elif quality == "MEDIUM":
                main_color = (0, 255, 255)  # 黄色
                ring_speed = 8
                shaking = 1
            else:
                main_color = (0, 0, 255)  # 红色
                ring_speed = 3
                shaking = 3  # 质量差时目标点微颤

            # 增加随机震颤效果 (模拟扫描不稳定的视觉感)
            import random
            dx = random.randint(-shaking, shaking)
            dy = random.randint(-shaking, shaking)
            display_x += dx
            display_y += dy

            # 3. 绘制核心目标点 (电子瞄准环)
            # 外旋转环 (由四个圆弧组成)
            angle = int(time.time() * ring_speed * 10) % 360
            cv2.ellipse(display, (display_x, display_y), (50, 50), angle, 0, 70, main_color, 2, cv2.LINE_AA)
            cv2.ellipse(display, (display_x, display_y), (50, 50), angle, 180, 250, main_color, 2, cv2.LINE_AA)

            # 内聚光点
            cv2.circle(display, (display_x, display_y), 6, WHITE, -1, cv2.LINE_AA)
            cv2.circle(display, (display_x, display_y), 15, main_color, 1, cv2.LINE_AA)

            # 四向延伸线 (十字准星)
            cv2.line(display, (display_x - 70, display_y), (display_x - 20, display_y), main_color, 1, cv2.LINE_AA)
            cv2.line(display, (display_x + 20, display_y), (display_x + 70, display_y), main_color, 1, cv2.LINE_AA)
            cv2.line(display, (display_x, display_y - 70), (display_x, display_y - 20), main_color, 1, cv2.LINE_AA)
            cv2.line(display, (display_x, display_y + 20), (display_x, display_y + 70), main_color, 1, cv2.LINE_AA)

            # 4. 绘制左上角信息面板
            current, total, samples = self.get_progress()
            font = cv2.FONT_HERSHEY_DUPLEX

            # 背景装饰块
            cv2.rectangle(display, (40, 40), (450, 220), (30, 30, 30), -1)
            cv2.rectangle(display, (40, 40), (450, 220), (80, 80, 80), 1)

            cv2.putText(display, f"CALIBRATION STAGE: {current}/{total}", (60, 85), font, 0.8, WHITE, 1, cv2.LINE_AA)
            cv2.putText(display, f"DATA SAMPLES: {samples}/{self.samples_per_target}", (60, 130), font, 0.6, CYAN, 1,
                        cv2.LINE_AA)

            # # q_text = f"FOCUS QUALITY: {quality}"
            # #cv2.putText(display, q_text, (60, 175), font, 0.6, main_color, 1, cv2.LINE_AA
            # cv2.putText(display, q_text, (60, 175), font, 0.6, main_color, 1, cv2.LINE_AA)

            # 5. 底部进度条 (科技质感)
            progress = samples / self.samples_per_target
            bar_w, bar_h = 600, 6
            bx = (width - bar_w) // 2
            by = height - 80
            # 条背景
            cv2.rectangle(display, (bx, by), (bx + bar_w, by + bar_h), (50, 50, 50), -1)
            # 条填充 (带外光晕效果)
            cv2.rectangle(display, (bx, by), (bx + int(bar_w * progress), by + bar_h), main_color, -1)

            # 提示文字
            cv2.putText(display, "STARE AT THE CENTER CROSS UNTIL COMPLETE",
                        (width // 2 - 200, by - 20), font, 0.5, (150, 150, 150), 1, cv2.LINE_AA)

        return display