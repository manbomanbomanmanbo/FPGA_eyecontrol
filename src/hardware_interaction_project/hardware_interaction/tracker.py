"""PC extracts geometry; PL decides blink edges, rearm and mouth hysteresis."""
import time
import cv2
import numpy as np
from interaction_runtime import FpgaInteractionTracker
from interaction_stage6.runtime_adapter import InteractionFailure

class HardwareInteractionTracker(FpgaInteractionTracker):
    def _eye_observation(self, left=0.0, right=0.0, *, valid=True, stamp=None):
        result=self.interaction.observe_eyes(left,right,self.tracking_generation,valid=valid,stamp=stamp)
        # Mirrors for the existing UI/logging. No host transition decisions.
        self.blink_rearm_required=result['rearm_required']
        self.blink_active=result['rearm_required']
        return result

    def _invalidate_tracking(self, reason):
        super()._invalidate_tracking(reason)
        if hasattr(self,'interaction'):
            self._eye_observation(valid=False)

    def detect_blink(self, frame):
        stamp=self.interaction.observation_ms()
        self.last_blink_observation=None
        if self.current_action=='mouth' and time.time()-self.last_action_time<self.action_cooldown:
            self._eye_observation(valid=False,stamp=stamp)
            return None
        if not hasattr(self,'calib_count'):
            self.base_ear_l,self.base_ear_r=5.0,5.0
            self.calib_count=0
        try:
            results=self.face_mesh.process(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
            if not results.multi_face_landmarks:
                self._eye_observation(valid=False,stamp=stamp)
                return None
            face=results.multi_face_landmarks[0]
            h,w=frame.shape[:2]
            def geometry(top,bottom):
                t=np.mean([[face.landmark[i].x*w,face.landmark[i].y*h] for i in top],axis=0)
                b=np.mean([[face.landmark[i].x*w,face.landmark[i].y*h] for i in bottom],axis=0)
                return np.linalg.norm(t-b),(t[1]+b[1])/2
            ear_l,center_l=geometry([159,158],[145,153])
            ear_r,center_r=geometry([386,385],[374,380])
            if not np.all(np.isfinite((ear_l,ear_r,center_l,center_r))):
                raise ValueError('Invalid eyelid geometry')
            if self.calib_count<30:
                self.base_ear_l,self.base_ear_r=ear_l,ear_r
                self.base_y=(center_l+center_r)/2
                self.calib_count+=1
                self._eye_observation(valid=False,stamp=stamp)
                return None
            if not np.all(np.isfinite((self.base_ear_l,self.base_ear_r,self.base_y))) or min(self.base_ear_l,self.base_ear_r)<=0:
                raise ValueError('Invalid eyelid baseline')
            current_y=(center_l+center_r)/2
            if current_y-self.base_y>h*0.02:
                self._eye_observation(valid=False,stamp=stamp)
                return None
            drop_l=(self.base_ear_l-ear_l)/self.base_ear_l
            drop_r=(self.base_ear_r-ear_r)/self.base_ear_r
            result=self._eye_observation(float(drop_l),float(drop_r),stamp=stamp)
            self.last_blink_observation={
                'classifier':'PL_EYELID_V3','ear_l':float(ear_l),'ear_r':float(ear_r),
                'base_ear_l':float(self.base_ear_l),'base_ear_r':float(self.base_ear_r),
                'drop_l':float(drop_l),'drop_r':float(drop_r),'closed_threshold':0.30,
                'reopen_threshold':0.20,'rearm_required':result['rearm_required'],
                'reopen_frames':result['reopen_frames'],
                'reopen_elapsed_seconds':result['reopen_elapsed_ms']/1000,
                'reopen_confirmed':bool(result['flags']&16),
                'pl_sequence':result['seq'],'observation_ms':stamp}
            if result['update_baseline']:
                self.base_ear_l=0.995*self.base_ear_l+0.005*ear_l
                self.base_ear_r=0.995*self.base_ear_r+0.005*ear_r
                self.base_y=0.995*self.base_y+0.005*current_y
            return {0:None,1:'double',2:'right'}[result['class']]
        except InteractionFailure:
            raise
        except Exception:
            self.last_blink_observation=None
            self._eye_observation(valid=False,stamp=stamp)
            return None

    def reset_mouth_scroll(self):
        if getattr(self,'_mouth_hw_live',False):
            self.interaction.observe_mouth(0.0,self.tracking_generation,valid=False)
            self._mouth_hw_live=False
        super().reset_mouth_scroll()

    def detect_mouth_movement(self, frame):
        stamp = self.interaction.observation_ms()
        try:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb_frame)
            if not results.multi_face_landmarks:
                self.reset_mouth_scroll()
                return None, None, False, None

            face_landmarks = results.multi_face_landmarks[0]
            h, w = frame.shape[:2]

            # 1. 获取参考锚点
            nose = face_landmarks.landmark[1]  # 鼻尖
            l_eye = face_landmarks.landmark[33]  # 物理左外眼角 (用于校准左脸)
            r_eye = face_landmarks.landmark[263]  # 物理右外眼角 (用于校准右脸)
            l_mouth = face_landmarks.landmark[61]  # 物理左嘴角
            r_mouth = face_landmarks.landmark[291]  # 物理右嘴角

            # 2. 计算当前两侧脸的“基础宽度”（眼角到鼻尖）
            # 这是为了抵消转头。头转动时，这个分母会随之缩小
            base_w_l = abs(l_eye.x - nose.x) + 1e-6
            base_w_r = abs(r_eye.x - nose.x) + 1e-6

            # 3. 计算嘴角的“拉伸率”
            # 撇嘴时，嘴角会向外侧（远离鼻尖）移动
            stretch_l = abs(l_mouth.x - nose.x) / base_w_l
            stretch_r = abs(r_mouth.x - nose.x) / base_w_r

            # 4. 动态基准（Neutral Base）
            if not hasattr(self, 'base_stretch_l'):
                self.base_stretch_l, self.base_stretch_r = stretch_l, stretch_r

            # 闭嘴且没大幅度动作时，缓慢校准基准（消除坐姿不正）
            if abs(stretch_l - self.base_stretch_l) < 0.05:
                self.base_stretch_l = 0.98 * self.base_stretch_l + 0.02 * stretch_l
            if abs(stretch_r - self.base_stretch_r) < 0.05:
                self.base_stretch_r = 0.98 * self.base_stretch_r + 0.02 * stretch_r

            # 5. 计算最终偏差
            # 如果左侧拉伸率增加，说明在向左撇嘴
            diff_l = stretch_l - self.base_stretch_l
            diff_r = stretch_r - self.base_stretch_r

            # 6. 张嘴状态保持：启动和停止用不同阈值，避免边界抖动打断滚动。
            upper = face_landmarks.landmark[13]
            lower = face_landmarks.landmark[14]
            mouth_span = abs(r_mouth.x - l_mouth.x)
            mouth_ratio = (lower.y - upper.y) / (mouth_span + 1e-6)
            if mouth_span <= 1e-6 or not np.isfinite(mouth_ratio):
                self.reset_mouth_scroll()
                return None, None, False, None
            self.mouth_open_ratio = float(mouth_ratio)
            result = self.interaction.observe_mouth(mouth_ratio, self.tracking_generation, stamp=stamp)
            self.mouth_is_open = result["open"]
            self._mouth_hw_live = True

            # 返回 diff_l 和 diff_r，供下一步判断方向
            return diff_l, diff_r, self.mouth_is_open, w * 0.1

        except InteractionFailure:
            raise
        except Exception as e:
            self.reset_mouth_scroll()
            return None, None, False, None
