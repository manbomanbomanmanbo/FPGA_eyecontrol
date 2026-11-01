import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import time

import pyautogui
from typing import Tuple


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _KeyboardInput(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyboardInput),
                ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("data", _InputUnion)]


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
        self.last_click_position = None
        self._last_single_down_at = None
        self._movement_resume_at = 0.0
        self._trace_path = Path(__file__).resolve().parent / "eye_tracking_data" / "mouse_click_trace.jsonl"
        self._trace_session = f"{os.getpid()}-{time.time_ns()}"
        self._trace_error_reported = False
        self._trace_sample_interval_s = 0.2
        self._trace_last_sample_times = {}
        self._trace_last_mouth_state = None
        self._user32 = None
        if sys.platform == "win32":
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._user32.SendInput.argtypes = (
                wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int)
            self._user32.SendInput.restype = wintypes.UINT
            self._user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
            self._user32.SetCursorPos.restype = wintypes.BOOL
            self._user32.GetDoubleClickTime.restype = wintypes.UINT
            self._user32.WindowFromPoint.argtypes = (wintypes.POINT,)
            self._user32.WindowFromPoint.restype = wintypes.HWND
            self._user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            self._user32.GetAncestor.restype = wintypes.HWND
            self._user32.GetForegroundWindow.restype = wintypes.HWND
            self._user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
            self._user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
            self._user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
            self._user32.GetAsyncKeyState.restype = wintypes.SHORT
        sources = {}
        for name in ("__main__", "main", "mouse_controller", "eye_tracker"):
            filename = getattr(sys.modules.get(name), "__file__", None)
            if filename and Path(filename).is_file():
                sources[name] = {"path": str(Path(filename).resolve()),
                                 "sha256": hashlib.sha256(Path(filename).read_bytes()).hexdigest()}
        sources["controller"] = {"path": str(Path(__file__).resolve()),
                                  "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        self.trace_event("startup", python=sys.executable, sources=sources,
                         version="native_click_trace_v2")

    def _window_info(self, hwnd):
        classname = ctypes.create_unicode_buffer(128)
        title = ctypes.create_unicode_buffer(160)
        if hwnd:
            self._user32.GetClassNameW(hwnd, classname, len(classname))
            self._user32.GetWindowTextW(hwnd, title, len(title))
        return {"hwnd": hwnd, "class": classname.value, "title": title.value}

    def _sample_trace_event(self, event, details):
        """Sample routine observations before any Windows or file work."""
        changed = False
        if event == "mouth_observation":
            state = (bool(details.get("valid")), bool(details.get("is_open")))
            changed = state != getattr(self, "_trace_last_mouth_state", None)
            self._trace_last_mouth_state = state
            key = (event, None)
        elif event == "interaction_pl_result":
            op = details.get("op")
            if details.get("status", 0) != 0 or op not in (1, 2, 3, 5, 6):
                return True, False
            output = details.get("output")
            if op in (3, 5) and isinstance(output, (list, tuple)) and output and output[0] != 0:
                return True, False
            if op == 3 and isinstance(output, (list, tuple)) and len(output) > 3 and output[3]:
                return True, False
            key = (event, op)
        else:
            return True, False

        now = time.monotonic()
        samples = getattr(self, "_trace_last_sample_times", None)
        if samples is None:
            samples = self._trace_last_sample_times = {}
        previous = samples.get(key)
        interval = getattr(self, "_trace_sample_interval_s", 0.2)
        if not changed and previous is not None and 0 <= now - previous < interval - 1e-9:
            return False, True
        samples[key] = now
        return True, True

    def trace_event(self, event, **details):
        """只记录动作，不记录摄像头画面；记录失败不打断控制。"""
        try:
            emit, sampled = self._sample_trace_event(event, details)
            if not emit:
                return
            record = {"event": event, "time_ns": time.time_ns(),
                      "pid": os.getpid(), "session": self._trace_session, **details}
            if sampled:
                record["sampled"] = True
            if self._user32 is not None and event != "input_receipt" and not sampled:
                position = tuple(map(int, pyautogui.position()))
                point_window = self._user32.WindowFromPoint(wintypes.POINT(*position))
                record["cursor"] = position
                record["point_window"] = self._window_info(point_window)
                record["point_root"] = self._window_info(self._user32.GetAncestor(point_window, 2))
                record["foreground"] = self._window_info(self._user32.GetForegroundWindow())
                record["held_keys"] = [name for name, key in
                                       (("left_button", 1), ("right_button", 2),
                                        ("shift", 16), ("ctrl", 17), ("alt", 18))
                                       if self._user32.GetAsyncKeyState(key) & 0x8000]
            self._trace_path.parent.mkdir(parents=True, exist_ok=True)
            with self._trace_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        except Exception as error:
            if not self._trace_error_reported:
                print(f"点击记录写入失败: {error}")
                self._trace_error_reported = True

    def move(self, pos: Tuple[float, float]):
        """移动鼠标"""
        # 给目标窗口处理点击的时间，避免下一帧眼控立即移开箭头。
        if time.monotonic() < self._movement_resume_at:
            return
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

    def _send_button(self, flags):
        event = _Input()
        event.type = 0  # INPUT_MOUSE
        event.data.mi.dwFlags = flags
        ctypes.set_last_error(0)
        sent = self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input))
        error = ctypes.get_last_error()
        self.trace_event("input_receipt", flags=flags, accepted=sent, error=error)
        if sent != 1:
            raise OSError(error, f"Windows 未接收鼠标事件（SendInput={sent}/1）")

    def _send_click(self, position):
        """分开发送按下、松开，返回按下时刻。"""
        x, y = position
        if self._user32 is None:
            down_at = time.monotonic()
            pyautogui.click(x=x, y=y, button="left", _pause=False)
            return down_at
        target = (int(x), int(y))
        current = tuple(map(int, pyautogui.position()))
        # 普通单击就在当前光标处；避免重复定位引起偶发权限拒绝。
        # 双击若指定了首次位置且光标已移开，仍须先准确回到该位置。
        if current != target:
            ctypes.set_last_error(0)
            positioned = self._user32.SetCursorPos(*target)
            error = ctypes.get_last_error()
            if not positioned:
                self.trace_event("cursor_position_result", success=False,
                                 api="SetCursorPos", error=error,
                                 requested_position=target, previous_position=current)
                raise OSError(error, f"光标定位失败（SetCursorPos，Windows错误={error}，目标={target}）")
            current = tuple(map(int, pyautogui.position()))
            if current != target:
                self.trace_event("cursor_position_result", success=False,
                                 api="SetCursorPos", error=error,
                                 requested_position=target, actual_position=current)
                raise RuntimeError(f"光标未到点击目标（目标={target}，实际={current}），未发送点击")
            self.trace_event("cursor_position_result", success=True,
                             method="SetCursorPos", requested_position=target)
        else:
            self.trace_event("cursor_position_result", success=True,
                             method="already_at_target", requested_position=target)
        down_at = time.monotonic()
        self._send_button(0x0002)  # MOUSEEVENTF_LEFTDOWN
        try:
            time.sleep(0.025)
        finally:
            self._send_button(0x0004)  # MOUSEEVENTF_LEFTUP
        return down_at

    def _hold_position(self, position):
        self.last_pos = position
        self._movement_resume_at = time.monotonic() + 0.20

    def click(self):
        """立即单击，并保存实际点击位置。"""
        try:
            position = tuple(map(int, pyautogui.position()))
            self.trace_event("single_start", requested_position=position)
            self._last_single_down_at = self._send_click(position)
            self.last_click_position = position
            self._hold_position(position)
            self.trace_event("single_result", success=True, requested_position=position)
            return True
        except Exception as e:
            self._last_single_down_at = None
            self.last_click_position = None
            self.trace_event("single_result", success=False, error=str(e))
            print(f"点击发送失败: {e}")
            return False

    def double_click(self, position=None):
        """图标直接执行默认打开动作，其他位置在同一点完成双击。"""
        try:
            position = tuple(map(int, position if position is not None
                                 else pyautogui.position()))
            self.trace_event("double_start", requested_position=position)
            if self._user32 is not None:
                point_window = self._user32.WindowFromPoint(wintypes.POINT(*position))
                point_root = self._user32.GetAncestor(point_window, 2)
                root_class = self._window_info(point_root)["class"]
                icon = None
                if root_class in ("Progman", "WorkerW", "CabinetWClass"):
                    # 用独立进程隔离系统图标接口，并限制本次等待时间。
                    # 超时结果不明确时停止本次动作，不再追加鼠标点击。
                    helper = Path(__file__).with_name("shell_icon_action.py")
                    result = subprocess.run(
                        [sys.executable, str(helper), str(position[0]), str(position[1])],
                        capture_output=True, text=True, encoding="utf-8",
                        timeout=5.0, creationflags=subprocess.CREATE_NO_WINDOW)
                    if result.returncode != 0:
                        raise OSError(result.stderr.strip() or "Windows 图标打开接口失败")
                    icon = json.loads(result.stdout)
                if icon is not None:
                    self._hold_position(position)
                    self.trace_event("shell_icon_open", **icon)
                    self.trace_event("double_result", success=True,
                                     method=icon.get("method", "shell_default_action"),
                                     requested_position=position, target_name=icon["name"])
                    print(f"[图标打开请求已接受] {icon['name']}")
                    return True
            limit = (self._user32.GetDoubleClickTime() / 1000.0
                     if self._user32 is not None else 0.5)
            elapsed = (time.monotonic() - self._last_single_down_at
                       if self._last_single_down_at is not None else float("inf"))
            same_single = position == self.last_click_position
            if same_single and elapsed < limit - 0.04:
                self.trace_event("double_plan", clicks=1, elapsed=elapsed, system_limit=limit)
                # 第一次眨眼已单击，补一次即可，避免产生第三次点击。
                self._send_click(position)
            else:
                self.trace_event("double_plan", clicks=2,
                                 elapsed=elapsed if self._last_single_down_at is not None else None,
                                 system_limit=limit)
                if same_single and elapsed <= limit + 0.04:
                    # 边界附近等首次单击过期，避免新窗口收到多余的第三击。
                    time.sleep(max(0.0, limit + 0.04 - elapsed))
                self._send_click(position)
                time.sleep(min(0.08, limit / 4))
                self._send_click(position)
            self._hold_position(position)
            self.trace_event("double_result", success=True, requested_position=position)
            return True
        except Exception as e:
            self.trace_event("double_result", success=False, error=str(e))
            print(f"双击发送失败: {e}")
            return False
        finally:
            self._last_single_down_at = None

    def get_screen_size(self):
        """获取屏幕尺寸"""
        return self.screen_width, self.screen_height

    def scroll(self, direction):
        """每次发送一个标准滚轮刻度，并记录 Windows 接收结果。
        Args:
            direction: 1 向上滚动, -1 向下滚动
        """
        try:
            if direction == 0:
                return False
            wheel_delta = 120 if direction > 0 else -120  # WHEEL_DELTA
            accepted = None
            if self._user32 is None:
                pyautogui.scroll(1 if direction > 0 else -1, _pause=False)
            else:
                event = _Input()
                event.type = 0  # INPUT_MOUSE
                event.data.mi.dwFlags = 0x0800  # MOUSEEVENTF_WHEEL
                event.data.mi.mouseData = wheel_delta & 0xffffffff
                ctypes.set_last_error(0)
                accepted = self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input))
                error = ctypes.get_last_error()
                if accepted != 1:
                    raise OSError(error, f"Windows 未接收滚轮事件（SendInput={accepted}/1）")
            self.trace_event("scroll_result", success=True, wheel_delta=wheel_delta,
                             accepted=accepted)
            return True
        except Exception as e:
            self.trace_event("scroll_result", success=False, error=str(e))
            print(f"滚轮滚动错误: {e}")
            return False
