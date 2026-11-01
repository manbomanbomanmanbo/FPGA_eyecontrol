import pyautogui
from typing import Tuple


class MouseController:
    def __init__(self):
        self.screen_width, self.screen_height = pyautogui.size()
        self.last_pos = (self.screen_width // 2, self.screen_height // 2)
        self.smooth_factor = 0.8

        # 安全设置 - 临时禁用安全停止（开发阶段）
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0

        # 边界缓冲区域
        self.border_margin = 10

    def move(self, pos: Tuple[float, float]):
        """移动鼠标"""
        try:
            x, y = pos

            # 边界检查
            x = max(self.border_margin, min(x, self.screen_width - self.border_margin))
            y = max(self.border_margin, min(y, self.screen_height - self.border_margin))

            # 平滑移动
            smooth_x = self.smooth_factor * self.last_pos[0] + (1 - self.smooth_factor) * x
            smooth_y = self.smooth_factor * self.last_pos[1] + (1 - self.smooth_factor) * y

            pyautogui.moveTo(smooth_x, smooth_y, _pause=False)
            self.last_pos = (smooth_x, smooth_y)

        except Exception as e:
            print(f"鼠标移动错误: {e}")

    def click(self):
        """执行点击"""
        try:
            pyautogui.click()
        except Exception as e:
            print(f"点击错误: {e}")

    def double_click(self):
        """执行双击操作"""
        pyautogui.doubleClick()  # 使用 pyautogui 的双击

    def right_click(self):
        return pyautogui.rightClick()

    def get_screen_size(self):
        """获取屏幕尺寸"""
        return self.screen_width, self.screen_height

    def scroll(self, direction):
        """执行滚轮滚动
        Args:
            direction: 1 向上滚动, -1 向下滚动
        """
        try:
            if direction > 0:
                pyautogui.scroll(13)  # 向上滚动
            elif direction < 0:
                pyautogui.scroll(-13)  # 向下滚动
        except Exception as e:
            print(f"滚轮滚动错误: {e}")
