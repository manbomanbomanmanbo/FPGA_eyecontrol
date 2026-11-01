"""Native-window controller with nine-point calibration and real FPGA mapping.

Calibration labels describe positions across the physical primary display.
Mapping still uses the existing 1920x1080 FPGA coordinate system. A small
control window may subsequently lose focus while the user operates another
application; collection additionally requires the real full-screen target
window, focus, and an acknowledgement after each target was presented.
"""
import copy
from dataclasses import replace
from pathlib import Path
import threading
import time
import uuid
import json

from .engine import Workbench, CaptureSequence
from .calibration import (FEATURE_SOURCE, fit_profile,
    save_profile, validate_local_profile)
from .control import MouseGate
from mock_link.calibration import build_profile, sha256_id
from mock_link.host_client import HostClient

NATIVE_SPACE = 'native_primary_screen_normalized'
NATIVE_GEOMETRY = 'native_primary_screen_v1'
MAPPING_SIZE = (1920, 1080)
UI_TTL_S = 0.75
SETTLE_NS = 900_000_000
FRAME_TTL_NS = 250_000_000


def primary_geometry(screen_size, screen_origin=(0, 0)):
    """Persistent coordinate contract, independent of the control-window rect."""
    if (type(screen_size) not in (list, tuple) or len(screen_size) != 2 or
            any(type(v) is not int or not 2 <= v <= 32768 for v in screen_size)):
        raise ValueError('主屏尺寸必须是两个有效物理像素整数')
    if (type(screen_origin) not in (list, tuple) or len(screen_origin) != 2 or
            any(type(v) is not int for v in screen_origin) or list(screen_origin) != [0, 0]):
        raise ValueError('当前原生会话仅支持原点为(0,0)的主显示器')
    return {'kind': NATIVE_GEOMETRY, 'physical_size': list(screen_size),
            'screen_origin': [0, 0], 'mapping_size': list(MAPPING_SIZE)}


def _bind_native_profile(profile, geometry):
    """Rehash the explicit native coordinate contract before any transmission."""
    result = copy.deepcopy(profile)
    contract = result['feature_contract']
    contract['display_space'] = NATIVE_SPACE
    contract['display_geometry'] = copy.deepcopy(geometry)
    contract['display_transform'] = 'physical_xy=mapping_xy/(mapping_size-1)*(physical_size-1)'
    wire = result['wire']
    result['wire'] = build_profile(sha256_id(contract), MAPPING_SIZE[0], MAPPING_SIZE[1],
                                  wire['a_q16'], wire['b_q16'])
    validate_local_profile(result)
    return result


def _validate_native_profile(profile, geometry):
    validate_local_profile(profile)
    contract = profile['feature_contract']
    if (contract.get('display_space') != NATIVE_SPACE or
            contract.get('display_geometry') != geometry or
            (profile['wire']['width'], profile['wire']['height']) != MAPPING_SIZE):
        raise ValueError('此标定不属于当前主屏原生坐标契约，请重新标定')


class NativeMouseGate(MouseGate):
    """Reuse cancellation/apply mechanics, with an actual native arm contract."""
    def __init__(self, backend=None, live_guard=None):
        super().__init__(backend)
        self.live_guard = live_guard
        self.continuous = False

    def apply(self, point, width, height, frame_age_s, action=None, event_id=0):
        # Recheck after the network round trip, not just before sending it.
        if self.live_guard is not None and not self.live_guard():
            self.disarm('原生窗口心跳或显示契约失效')
            return False
        applied = super().apply(point, width, height, frame_age_s, action, event_id)
        if applied and self.continuous:
            with self.lock:
                if self.enabled:
                    self.until = time.monotonic() + 30
        return applied

    def arm(self, profile, geometry):
        self.disarm('重新检查原生鼠标授权条件')
        expected = primary_geometry(geometry.get('physical_size'), geometry.get('screen_origin'))
        if geometry != expected:
            raise ValueError('原生显示契约字段不匹配')
        _validate_native_profile(profile, geometry)
        if profile.get('source') != 'camera':
            raise ValueError('系统鼠标需要真实摄像头个人标定')
        generation = self.generation
        if self.backend is None:
            import pyautogui
            self.backend = pyautogui
        self.backend.FAILSAFE = True
        self.backend.PAUSE = 0
        size = tuple(self.backend.size())
        if list(size) != geometry['physical_size']:
            raise ValueError('桌面物理像素与标定记录不同，未启用鼠标')
        with self.lock:
            if self.generation != generation:
                raise ValueError('鼠标授权已被停止操作取消')
            self.screen_size = size
            self.enabled = True
            self.until = time.monotonic() + 30
            self.reason = 'native_primary_screen_30_seconds'


class NativeCaptureSequence(CaptureSequence):
    def __init__(self, kind, geometry):
        super().__init__(kind, copy.deepcopy(geometry))
        self.target_token = uuid.uuid4().hex
        self.presented_ns = None
        self.capture_started = False
        self.camera_size = None

    def status(self):
        result = super().status()
        result.update(target_token=self.target_token, presented=self.presented_ns is not None,
                      settling=self.presented_ns is None or
                      time.monotonic_ns() - self.presented_ns < SETTLE_NS)
        return result

    def acknowledge(self, token):
        if token != self.target_token:
            return False
        if self.presented_ns is None:
            self.presented_ns = time.monotonic_ns()
            self.started = time.monotonic()
            self.capture_started = True
        return True

    def observe(self, obs, prediction=None):
        if self.presented_ns is None or obs.capture_monotonic_ns < self.presented_ns + SETTLE_NS:
            return False
        if obs.valid:
            if obs.feature_source != FEATURE_SOURCE:
                raise ValueError('采集特征版本改变，请重新开始')
            size = (obs.width, obs.height)
            if self.camera_size is None:
                self.camera_size = size
            elif self.camera_size != size:
                raise ValueError('采集期间摄像头尺寸改变，请重新开始')
        old_index, old_token, old_presented = self.index, self.target_token, self.presented_ns
        complete = super().observe(obs, prediction)
        if self.index != old_index:
            self.groups[-1].update(target_token=old_token, presented_monotonic_ns=old_presented,
                                   settle_ms=SETTLE_NS // 1_000_000)
            if not complete:
                self.target_token = uuid.uuid4().hex
                self.presented_ns = None
        return complete


class NativeSession(Workbench):
    def __init__(self, output, board_host='192.168.2.99', board_port=8765, camera_index=0,
                 *, camera=None, mouse_backend=None, client_factory=HostClient, autostart=True,
                 simple_controls=False):
        self._native_ready = False
        self._closed = False
        self._client_factory = client_factory
        self._screen_geometry = None
        self._native_visible = False
        self._native_focused = False
        self._capture_rect = None
        self._capture_fullscreen = False
        self._startup_monitor = False
        self._startup_next_at = 0.0
        self._link_mode = 'offline'
        self.board_link_error = ''
        self._calibration_workflow = False
        self.simple_controls = simple_controls
        self._auto_arm_pending = False
        self._arm_after_frame_id = -1
        self._last_mouth_frame_id = -1
        from .gestures import MouthGestureDetector
        self.mouth = MouthGestureDetector()
        super().__init__(output, board_host, board_port, camera_index)
        if not autostart:
            self.stop_event.set()
            self.thread.join(1)
        if camera is not None:
            self.camera = camera
        self.mouse = NativeMouseGate(mouse_backend, self._mouse_context_live)
        self.mouse.continuous = simple_controls
        self._native_ready = True

    def _alive(self):
        return (not self._closed and self._screen_geometry is not None and
                time.monotonic() - self.ui_seen <= UI_TTL_S)

    def _capture_ready(self):
        if not self._alive() or not self._native_visible or not self._native_focused or not self._capture_fullscreen:
            return False
        width, height = self._screen_geometry['physical_size']
        return self._capture_rect == [0, 0, width, height]

    def _mouse_context_live(self):
        return (self._alive() and self.sequence is None and self.tracking and not self.paused and
                self.profile is not None and self.profile['feature_contract'].get('display_geometry') == self._screen_geometry)

    def _require_alive(self):
        if not self._alive():
            raise ValueError('原生窗口事件循环无新鲜心跳，请恢复窗口后重试')

    def heartbeat(self, screen_size, *, screen_origin=(0, 0), visible=True, focused=True,
                  capture_rect=None, capture_fullscreen=False):
        geometry = primary_geometry(screen_size, screen_origin)
        if any(type(v) is not bool for v in (visible, focused, capture_fullscreen)):
            raise ValueError('原生窗口状态必须为布尔值')
        if capture_rect is not None and (type(capture_rect) not in (tuple, list) or
                len(capture_rect) != 4 or any(type(v) is not int for v in capture_rect)):
            raise ValueError('采集窗口矩形必须为四个物理像素整数')
        if self._closed:
            return
        # Cancel a pending network operation before waiting for the worker lock.
        if self._screen_geometry is not None and geometry != self._screen_geometry:
            self.pause()
        if self.sequence and self.sequence.capture_started and not (visible and focused and capture_fullscreen and
                                  list(capture_rect or []) == [0, 0] + geometry['physical_size']):
            self.pause()
        with self.lock:
            self._screen_geometry = geometry
            self._native_visible, self._native_focused = visible, focused
            self._capture_rect = None if capture_rect is None else list(capture_rect)
            self._capture_fullscreen = capture_fullscreen
            self.ui_seen = time.monotonic()
            # Native activity means a live controller on the same physical
            # display. Actual target-window focus is separately enforced above.
            self.ui_geometry = copy.deepcopy(geometry)
            self.ui_active = True

    def open_camera(self):
        return super().action('camera_start', {})

    def stop_camera(self):
        return super().action('camera_stop', {})

    def start_application(self):
        """Normal launch: open the camera and connect the board, no test mapping."""
        self.open_camera()
        with self.lock:
            self._require_alive()
            self._startup_monitor = True
            self._startup_next_at = 0.0
            self._maintain_startup_link()
        return self.snapshot()

    def _maintain_startup_link(self):
        # Empty messages maintain the server's five-second idle lease before
        # personal calibration. They never ask the PL to calculate coordinates.
        if not self._startup_monitor or time.monotonic() < self._startup_next_at:
            return
        if not self._alive():
            return
        try:
            if self.client is None or not self.client.connected:
                self.disconnect()
                self.client = self._client_factory(host=self.board_host, port=self.board_port,
                    timeout_s=.3, result_ttl_s=.25, calibration=None)
                self.client.connect()
                self._link_mode = 'connected_waiting_calibration'
                if self.sequence is None:
                    self.reason = 'FPGA 已连接；按 C 开始个人标定'
            result = self.client.submit(0, 0, valid=0)
            if result.status != 'invalid_input' or result.screen_x is not None or result.screen_y is not None:
                raise ValueError('FPGA 链路心跳返回无效')
            self.board_link_error = ''
            self._startup_next_at = time.monotonic() + 1
        except Exception as exc:
            self.disconnect()
            self._link_mode = 'offline'
            self.board_link_error = 'FPGA 连接失败，正在重试：' + str(exc)
            self._startup_next_at = time.monotonic() + 2

    def start_calibration(self, screen_size=None):
        # Cancel OS input before waiting for a possibly in-flight mapping.
        self.mouse.disarm('重新标定')
        with self.lock:
            self._auto_arm_pending = False
            self.mouth.reset()
            self._last_mouth_frame_id = -1
            self.confirm_enabled = False
            self._require_alive()
            if screen_size is not None and primary_geometry(screen_size) != self._screen_geometry:
                raise ValueError('请求尺寸与实际主屏不一致')
            if self.source != 'camera' or not self.camera.status()['running']:
                raise ValueError('先启动真实摄像头')
            if self._link_mode != 'connected_waiting_calibration' or not self.client or not self.client.connected:
                self.disconnect()
                self._link_mode = 'offline'
            self._startup_monitor = True
            self._startup_next_at = 0.0
            self.point = self.raw_point = None
            self.tracking = False
            self.paused = False
            self.error = ''
            self.sequence = NativeCaptureSequence('calibration', self._screen_geometry)
            self._calibration_workflow = True
            self.reason = '等待目标实际呈现后开始九点采集'
        return self.snapshot()

    def ack_target(self, token):
        with self.lock:
            if self.sequence is None or not self._capture_ready():
                return False
            return self.sequence.acknowledge(token)

    def connect(self):
        """Internal and compatibility alias for an explicit native connection."""
        return self.connect_board()

    def connect_board(self):
        with self.lock:
            self._require_alive()
            if self.profile is None:
                raise ValueError('请先完成原生全屏个人标定')
            _validate_native_profile(self.profile, self._screen_geometry)
            if self.sequence is not None:
                raise ValueError('请先完成或取消正在进行的采集')
            self.disconnect()
            self._startup_monitor = False
            self._link_mode = 'offline'
            self.client = self._client_factory(host=self.board_host, port=self.board_port,
                timeout_s=.3, result_ttl_s=.25, calibration=self.profile['wire'])
            self.client.connect()
            self._link_mode = 'personal_mapping'
            self.board_link_error = ''
            self.tracking, self.paused = True, False
            self.error = ''
            self.reason = '已连接 FPGA，系统鼠标仍关闭'
        return self.snapshot()

    def start_validation(self):
        with self.lock:
            self._require_alive()
            if (self.source != 'camera' or not self.camera.status()['running'] or
                    self.profile is None or self.profile['source'] != 'camera' or
                    self.client is None or not self.client.connected):
                raise ValueError('先完成真实标定并连接 FPGA')
            _validate_native_profile(self.profile, self._screen_geometry)
            self.mouse.disarm('独立评估中禁止系统鼠标')
            self.point = self.raw_point = None
            self.sequence = NativeCaptureSequence('validation', self._screen_geometry)
            self.tracking, self.paused = True, False
            self.reason = '独立目标评估，不重新拟合标定'
        return self.snapshot()

    def arm_mouse(self):
        with self.lock:
            self._require_alive()
            if (self.paused or self.sequence is not None or self.point is None or
                    time.monotonic() - self.last_result_at > .25 or
                    self.client is None or not self.client.connected):
                raise ValueError('先完成评估并获得新鲜 FPGA 坐标，采集期间不能启用鼠标')
            self.mouse.arm(self.profile, self._screen_geometry)
        return self.snapshot()

    def mouse_off(self):
        self.mouse.disarm('用户停止')
        with self.lock:
            self.mouse.disarm('用户停止')
        return self.snapshot()

    def set_confirmation(self, enabled, mode='click'):
        if type(enabled) is not bool or mode not in ('click', 'scroll_up', 'scroll_down'):
            raise ValueError('确认开关或动作类型无效')
        with self.lock:
            self.confirm_enabled, self.confirm_action = enabled, mode
        return self.snapshot()

    def pause(self):
        self.mouse.disarm('用户暂停')
        if self.client is not None:
            self.client.disconnect()
        with self.lock:
            self._startup_monitor = False
            self._calibration_workflow = False
            self._auto_arm_pending = False
            self.mouth.reset()
            self._last_mouth_frame_id = -1
            self.sequence = None
            self.tracking, self.paused = False, True
            self.disconnect()
            self._link_mode = 'offline'
            self.reason = '已暂停，系统鼠标关闭'
        return self.snapshot()

    def tick(self):
        if not self._native_ready or self._closed:
            return
        self._maintain_startup_link()
        if self.tracking or self.sequence is not None:
            if not self._alive():
                self.pause()
                self.reason = '原生窗口心跳过期，已停止交互'
                return
            expected = (self.sequence.geometry if self.sequence else
                        self.profile['feature_contract']['display_geometry'])
            if self._screen_geometry != expected or (self.sequence and self.sequence.capture_started and not self._capture_ready()):
                self.pause()
                self.reason = '显示或目标窗口环境改变，请恢复后重新开始'
                return
        if self.sequence and time.monotonic() - self.sequence.started > 12:
            raise ValueError('采集目标超时；目标未呈现或有效样本不足')
        if self.sequence and self.sequence.kind == 'calibration':
            obs = self.camera.latest()
            age_ns = time.monotonic_ns() - obs.capture_monotonic_ns
            if obs.valid and not 0 <= age_ns <= FRAME_TTL_NS:
                obs = replace(obs, valid=False, reason='stale_frame', raw_gx=None, raw_gy=None)
            self.observation = obs
            if obs.frame_id == self.last_frame_id:
                return
            self.last_frame_id = obs.frame_id
            if self.sequence.observe(obs):
                seq = self.sequence
                profile = fit_profile(seq.groups, camera_size=seq.camera_size, geometry=seq.geometry)
                profile = _bind_native_profile(profile, seq.geometry)
                run = self.output / ('native_calibration_' + uuid.uuid4().hex[:12])
                run.mkdir()
                (run / 'samples.json').write_text(json.dumps(seq.groups, indent=2) + '\n', encoding='utf-8')
                self.profile, self.profile_path = profile, run / 'profile.json'
                save_profile(profile, self.profile_path)
                self.sequence = None
                if self._calibration_workflow:
                    self.connect_board()
                    if self.simple_controls:
                        self._calibration_workflow = False
                        self._auto_arm_pending = True
                        self._arm_after_frame_id = self.last_frame_id
                        self.reason = '九点标定完成；等待退出全屏及新鲜 FPGA 坐标后开启鼠标'
                    else:
                        self.start_validation()
                        self.reason = '标定已保存；进行五点独立评估，继续注视圆点'
                else:
                    self.tracking, self.paused = False, True
                    self.disconnect()
            return
        # The proven mapping/freshness/reference checks are shared with the
        # existing controller; its geometry equality now compares native data.
        collecting = self.sequence is not None
        confirmation = self.confirm_enabled
        if collecting:
            self.confirm_enabled = False
        try:
            super().tick()
            if self.client is None or not self.client.connected:
                self.mouse.disarm('FPGA 连接已关闭')
                self.confirm_enabled = False
                self._auto_arm_pending = False
                self.mouth.reset()
            if collecting and self.sequence is None and self.tracking:
                self._calibration_workflow = False
                self._auto_arm_pending = self.simple_controls
                self._arm_after_frame_id = self.last_frame_id
                self.reason = '独立评估完成'
            if self.tracking and not self._alive():
                self.pause()
                self.reason = '网络返回时窗口心跳已过期，结果已丢弃'
        finally:
            if collecting:
                self.confirm_enabled = confirmation
        if self.simple_controls:
            # Wait for the target window to return to the normal preview and
            # for a subsequent fresh FPGA result before starting OS control.
            if (self._auto_arm_pending and not self._capture_fullscreen and
                    self.sequence is None and self.point is not None and
                    self.last_frame_id > self._arm_after_frame_id and
                    time.monotonic() - self.last_result_at <= .25):
                self._auto_arm_pending = False
                try:
                    self.arm_mouse()
                    self.confirm_enabled = True
                    self.confirm_action = 'click'
                    self.mouth.reset()
                except ValueError as exc:
                    self.reason = str(exc)
                    self.error = str(exc)
            obs = self.observation
            action = None
            if obs is not None and self.mouse.enabled and obs.frame_id != self._last_mouth_frame_id:
                self._last_mouth_frame_id = obs.frame_id
                action = self.mouth.update(obs)
            if action and self._mouse_context_live() and self.point is not None:
                age = (time.monotonic_ns() - obs.capture_monotonic_ns) / 1e9
                self._mouse_event_id += 1
                self.mouse.apply(self.point, *MAPPING_SIZE, age, action, self._mouse_event_id)
            if not self.mouse.enabled:
                self.mouth.reset()

    def snapshot(self):
        result = super().snapshot()
        result.update(native=True, screen_geometry=copy.deepcopy(self._screen_geometry),
                      capture_ready=self._capture_ready(),
                      native_visible=self._native_visible, native_focused=self._native_focused,
                      link_mode=self._link_mode,
                      board_link_error=self.board_link_error)
        if self._link_mode == 'connected_waiting_calibration':
            result['implementation']['mapping'] = 'connected; personal calibration pending'
        result['implementation']['mouth'] = 'PC same-frame mouth gesture detector'
        return result

    def close(self):
        self._closed = True
        self._startup_monitor = False
        self._auto_arm_pending = False
        return super().close()
