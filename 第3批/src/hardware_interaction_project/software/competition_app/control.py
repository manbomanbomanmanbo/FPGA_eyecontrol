"""Opt-in OS mouse adapter. Virtual interaction is the default."""
import time
import math
import threading

class MouseGate:
    def __init__(self, backend=None):
        self.backend=backend; self.enabled=False; self.until=0.0; self.reason='disabled'
        self.last_event_id=0
        self.screen_size=None
        self.generation=0;self.lock=threading.RLock()

    def disarm(self,reason='用户暂停'):
        # Publish cancellation before waiting for an in-progress native call.
        self.enabled=False;self.until=0;self.generation+=1
        with self.lock:self.reason=reason

    def arm(self,profile,geometry):
        self.disarm('重新检查授权条件')
        evidence=profile.get('validation')
        if profile.get('source')!='camera' or not evidence or evidence.get('kind')!='held_out_requested_fixation_targets':
            raise ValueError('系统鼠标需要真实摄像头标定和独立目标评估')
        if not all(math.isfinite(evidence[k]) and evidence[k]>=0 for k in ('median_error_px','p90_error_px')) or evidence['median_error_px']>120 or evidence['p90_error_px']>240:
            raise ValueError('定位误差尚大，仅开放虚拟交互，请改善标定')
        expected=profile['feature_contract']['display_geometry']
        if not geometry.get('fullscreen') or geometry!=expected:
            raise ValueError('请保持标定时的同一显示器、全屏尺寸和缩放')
        if geometry.get('screen_origin')!=[0,0]:
            raise ValueError('当前原型仅开放主屏全屏鼠标，请在主显示器重新标定')
        if self.backend is None:
            import pyautogui
            self.backend=pyautogui
        self.backend.FAILSAFE=True
        self.backend.PAUSE=0
        size=self.backend.size()
        if list(size)!=geometry.get('physical_size'):
            raise ValueError('桌面物理像素与标定记录不同，未启用鼠标')
        self.screen_size=tuple(size)
        self.enabled=True;self.until=time.monotonic()+30;self.reason='enabled_30_seconds'

    def apply(self,point,width,height,frame_age_s,action=None,event_id=0):
        if not self.enabled:return False
        generation=self.generation
        if time.monotonic()>=self.until or not math.isfinite(frame_age_s) or not 0<=frame_age_s<=0.25 or point is None:
            self.disarm('过期或无效结果');return False
        try:
            sw,sh=self.backend.size()
            if (sw,sh)!=self.screen_size:
                self.disarm('屏幕物理尺寸改变');return False
            with self.lock:
                if not self.enabled or self.generation!=generation or time.monotonic()>=self.until:return False
                self.backend.moveTo(point[0]/(width-1)*(sw-1),point[1]/(height-1)*(sh-1),duration=0)
            with self.lock:
                if not self.enabled or self.generation!=generation or time.monotonic()>=self.until:return False
                if action and event_id>self.last_event_id:
                    self.last_event_id=event_id
                    if action=='click':self.backend.click()
                    elif action=='scroll_up':self.backend.scroll(3)
                    elif action=='scroll_down':self.backend.scroll(-3)
                    else:raise ValueError('Unknown interaction')
            return True
        except Exception as exc:
            self.disarm('鼠标停止：'+type(exc).__name__);return False
