"""Shared camera and FPGA session engine; no web server or browser frontend."""
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid
from .calibration import (TRAIN_TARGETS,VALIDATION_TARGETS,fit_profile,synthetic_profile,
    encode_features,integer_reference,evaluate_targets,save_profile)
from .vision import CameraWorker
from .control import MouseGate
from mock_link.host_client import HostClient

class CaptureSequence:
    def __init__(self,kind,geometry):
        self.kind=kind;self.geometry=geometry;self.index=0;self.groups=[]
        self.targets=TRAIN_TARGETS if kind=='calibration' else VALIDATION_TARGETS
        self.started=time.monotonic();self.samples=[];self.ids=[];self.attempts=0

    def status(self):
        return {'kind':self.kind,'index':self.index+1,'total':len(self.targets),
                'target':self.targets[self.index], 'samples':len(self.samples),
                'settling':time.monotonic()-self.started<0.9,'geometry':self.geometry}

    def observe(self,obs,prediction=None):
        elapsed=time.monotonic()-self.started
        if elapsed>12:raise ValueError('此目标没有采集到足够有效样本，请调整光照、距离或重新标定')
        if elapsed<0.9:return False
        self.attempts+=1
        if not obs.valid:return False
        value=[obs.raw_gx,obs.raw_gy] if self.kind=='calibration' else prediction
        if value is None:return False
        if obs.frame_id in self.ids:return False
        self.samples.append(value);self.ids.append(obs.frame_id)
        if len(self.samples)<24:return False
        key='features' if self.kind=='calibration' else 'predictions'
        self.groups.append({'target':list(self.targets[self.index]),key:self.samples,
            'frame_ids':self.ids,'attempted_frames':self.attempts})
        self.index+=1
        if self.index==len(self.targets):return True
        self.started=time.monotonic();self.samples=[];self.ids=[];self.attempts=0
        return False

class Workbench:
    def __init__(self,output,board_host='192.168.2.99',board_port=8765,camera_index=0):
        self.output=Path(output);self.output.mkdir(parents=True,exist_ok=True)
        self.board_host=board_host;self.board_port=board_port
        self.camera=CameraWorker(camera_index=camera_index)
        self.lock=threading.RLock();self.stop_event=threading.Event();self.mouse=MouseGate()
        self.client=None;self.profile=None;self.profile_path=None;self.sequence=None
        self.source='camera';self.tracking=False;self.point=None;self.raw_point=None
        self.error='';self.reason='请启动摄像头';self.last_frame_id=-1;self.observation=None
        self.mapping_count=0;self.mapping_failures=0;self.roundtrip_ms=None;self.frame_to_result_ms=None
        self.last_result_at=0.0;self.confirm_enabled=False;self.confirm_action='click';self.last_blink_id=0
        self._mouse_event_id=0
        self.actions=[];self.virtual_scroll=0;self.virtual_clicks=0;self.paused=True;self.selected_message=None
        self.ui_seen=0.0;self.ui_geometry=None;self.ui_active=False
        self.thread=threading.Thread(target=self.loop,name='lingmou-workbench',daemon=True)
        self.thread.start()

    def disconnect(self):
        if self.client:self.client.disconnect()
        self.point=None;self.raw_point=None;self.mouse.disarm('连接已关闭')

    def connect(self):
        if not self.profile:raise ValueError('请先完成个人标定，或启动明确标记的合成演示')
        self.disconnect()
        self.client=HostClient(host=self.board_host,port=self.board_port,timeout_s=.3,
                               result_ttl_s=.25,calibration=self.profile['wire'])
        self.client.connect();self.error='';self.tracking=True;self.paused=False

    def action(self,name,data):
        # Cancellation can interrupt the socket without waiting for an in-flight mapping.
        if name in ('pause','disconnect','camera_stop','mouse_off'):
            self.mouse.disarm('用户停止')
            if name!='mouse_off' and self.client:self.client.disconnect()
        with self.lock:
            if name=='ui_heartbeat':
                self.ui_seen=time.monotonic();self.ui_geometry=data.get('geometry',{})
                self.ui_active=data.get('visible') is True and data.get('focused') is True
            elif name=='camera_start':
                self.disconnect();self.source='camera';self.tracking=False;self.paused=True
                self.sequence=None
                if self.profile and self.profile['source']!='camera':self.profile=None
                self.last_frame_id=-1;self.error='';self.camera.start();self.reason='摄像头启动中'
            elif name=='camera_stop':
                self.tracking=False;self.paused=True;self.sequence=None;self.disconnect()
                if not self.camera.stop():self.error='摄像头驱动尚未返回，未宣称已关闭'
                self.reason='摄像头已停止'
            elif name=='synthetic':
                self.disconnect();self.sequence=None;self.tracking=False;self.paused=True
                if not self.camera.stop():
                    self.reason='摄像头尚未关闭';raise ValueError('摄像头线程尚未停止，不能启动合成模式')
                self.source='synthetic'
                self.profile=synthetic_profile();self.profile_path=None;self.last_frame_id=-1
                self.connect();self.reason='合成输入，真实FPGA映射；不是真人效果'
            elif name=='connect':
                if self.profile and self.profile['source']=='camera' and data.get('geometry',{})!=self.profile['feature_contract']['display_geometry']:
                    raise ValueError('请恢复标定时的全屏尺寸后连接')
                self.connect()
            elif name in ('pause','disconnect'):
                self.tracking=False;self.paused=True;self.sequence=None;self.disconnect();self.reason='已暂停'
            elif name=='calibrate':
                if self.source!='camera' or not self.camera.status()['running']:
                    raise ValueError('先启动真实摄像头')
                if not data.get('geometry',{}).get('fullscreen'):raise ValueError('请进入全屏标定')
                self.disconnect();self.tracking=False;self.paused=False;self.error=''
                self.sequence=CaptureSequence('calibration',data.get('geometry',{}));self.reason='依次注视九个目标'
            elif name=='validate':
                if not self.profile or self.profile['source']!='camera' or not self.client or not self.client.connected:
                    raise ValueError('先完成真实标定并连接FPGA')
                if data.get('geometry',{})!=self.profile['feature_contract']['display_geometry']:
                    raise ValueError('评估必须使用与标定相同的显示尺寸与全屏状态')
                self.sequence=CaptureSequence('validation',data.get('geometry',{}));self.paused=False
                self.reason='独立目标评估，结果不会重新拟合标定'
            elif name=='confirm':
                self.confirm_enabled=bool(data.get('enabled',False));self.confirm_action=data.get('mode','click')
                if self.confirm_action not in ('click','scroll_up','scroll_down'):raise ValueError('未知动作')
            elif name=='synthetic_blink':
                if self.source!='synthetic':raise ValueError('模拟确认只用于合成演示')
                if self.point is None or time.monotonic()-self.last_result_at>.25:raise ValueError('当前没有新鲜坐标')
                self.virtual_action('synthetic_button',0)
            elif name=='mouse_on':
                if not self.profile or not self.client or not self.client.connected:raise ValueError('未连接有效标定')
                if self.paused or self.sequence or self.point is None or time.monotonic()-self.last_result_at>.25:
                    raise ValueError('当前没有实时交互坐标，或尚在标定/评估')
                if not self.ui_active or time.monotonic()-self.ui_seen>.75 or self.ui_geometry!=data.get('geometry',{}):
                    raise ValueError('工作台必须保持可见、获得焦点且尺寸一致')
                self.mouse.arm(self.profile,data.get('geometry',{}))
            elif name=='mouse_off':self.mouse.disarm('用户停止')
            elif name=='environment_fixture':
                from .stereo import synthetic_fixture
                return {'fixture':synthetic_fixture()}
            else:raise ValueError('未知操作')
        return self.snapshot()

    def virtual_action(self,kind,event_id):
        if self.confirm_action=='click':
            self.virtual_clicks+=1
            if self.point:
                x,y=self.point
                messages=('我想喝水','请调整坐姿','我需要帮助','我想休息')
                for i,message in enumerate(messages):
                    left=180+(i%2)*850;top=240+(i//2)*350
                    if left<=x<=left+710 and top<=y<=top+265:
                        self.selected_message=message;break
        elif self.confirm_action=='scroll_up':self.virtual_scroll=max(-30,self.virtual_scroll-1)
        elif self.confirm_action=='scroll_down':self.virtual_scroll=min(30,self.virtual_scroll+1)
        event={'action':self.confirm_action,'kind':kind,'event_id':event_id,'point':self.point,'selected_message':self.selected_message,
               'pc_unix_s':time.time(),'source':self.source}
        self.actions.append(event);self.actions=self.actions[-30:]
        with (self.output/'interaction_events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n')

    def snapshot(self):
        with self.lock:
            if self.mouse.enabled and time.monotonic()>=self.mouse.until:self.mouse.disarm('30秒授权已结束')
            if time.monotonic()-self.last_result_at>.25:self.point=None
            p=self.profile
            return {'application':'lingmou_workbench_v1','source':self.source,'camera':self.camera.status(),
                'observation':asdict(self.observation) if self.observation else None,
                'connected':bool(self.client and self.client.connected),'board_host':self.board_host,
                'point':self.point,'raw_point':self.raw_point if self.point else None,
                'error':self.error,'reason':self.reason,'paused':self.paused,
                'sequence':self.sequence.status() if self.sequence else None,
                'profile':None if p is None else {'source':p['source'],'id':p['wire']['calibration_id'],
                    'fit':p['fit'],'validation':p['validation'],'geometry':p['feature_contract']['display_geometry'],
                    'path':str(self.profile_path) if self.profile_path else None},
                'mapping_count':self.mapping_count,'mapping_failures':self.mapping_failures,
                'roundtrip_ms':self.roundtrip_ms,'frame_to_result_ms':self.frame_to_result_ms,
                'timing_note':'PC计时，含识别/网络/驱动开销；不是FPGA电路延迟',
                'confirm_enabled':self.confirm_enabled,'confirm_action':self.confirm_action,
                'mouse_enabled':self.mouse.enabled,'mouse_reason':self.mouse.reason,
                'virtual_clicks':self.virtual_clicks,'virtual_scroll':self.virtual_scroll,'selected_message':self.selected_message,
                'last_action':self.actions[-1] if self.actions else None,
                'implementation':{'landmarks':'PC','blink':'PC','filter':'PC adaptive EMA',
                                  'mapping':'real FPGA via TCP v2','stereo':'geometry fixture only','gimbal':'not connected'}}

    def loop(self):
        while not self.stop_event.wait(.035):
            try:
                with self.lock:self.tick()
            except Exception as exc:
                with self.lock:
                    self.error=str(exc);self.sequence=None;self.tracking=False;self.paused=True
                    self.mapping_failures+=1;self.disconnect()

    def tick(self):
        if self.source=='camera' and (self.tracking or self.sequence):
            expected=self.sequence.geometry if self.sequence else self.profile['feature_contract']['display_geometry']
            if not self.ui_active or time.monotonic()-self.ui_seen>.75 or self.ui_geometry!=expected:
                self.mouse.disarm('页面已离开或显示环境改变');self.sequence=None
                self.tracking=False;self.paused=True;self.disconnect();self.reason='工作台失去焦点或显示环境改变，已暂停'
                return
        if self.sequence and time.monotonic()-self.sequence.started>12:
            raise ValueError('采集目标超时；相机中断或有效样本不足，请重新开始')
        if self.source=='synthetic':
            if not self.tracking:return
            from .vision import FrameObservation
            now=time.monotonic();angle=now*.7
            x,y=.5+.32*math.cos(angle),.5+.30*math.sin(angle)
            obs=FrameObservation(self.last_frame_id+1,time.monotonic_ns(),time.monotonic_ns(),True,'synthetic',
                .40+.18*x,-.06+.15*y,.25,.25,0,None,640,480)
        else:obs=self.camera.latest()
        self.observation=obs
        if obs.frame_id==self.last_frame_id:
            if not obs.valid:
                self.point=None;self.raw_point=None
                if obs.reason!='eyes_closed':self.mouse.disarm('检测失效或帧过期')
            return
        self.last_frame_id=obs.frame_id
        if self.sequence and self.sequence.kind=='calibration':
            if self.sequence.observe(obs):
                seq=self.sequence
                profile=fit_profile(seq.groups,camera_size=(obs.width,obs.height),geometry=seq.geometry)
                run=self.output/('calibration_'+uuid.uuid4().hex[:12]);run.mkdir()
                (run/'samples.json').write_text(json.dumps(seq.groups,indent=2)+'\n')
                self.profile=profile;self.profile_path=run/'profile.json';save_profile(profile,self.profile_path)
                self.sequence=None;self.connect();self.reason='标定完成；请继续独立目标评估'
            return
        if not self.tracking:return
        if not obs.valid:
            self.point=None;self.raw_point=None;self.reason='无效：'+obs.reason
            if obs.reason!='eyes_closed':self.mouse.disarm('检测失效')
            if self.sequence:self.sequence.observe(obs)
            return
        if not self.client or not self.client.connected:
            self.point=None;self.raw_point=None;self.tracking=False;self.reason='连接关闭，请显式重新连接';return
        try:q=encode_features(obs.raw_gx,obs.raw_gy,self.profile,obs.width,obs.height)
        except ValueError as exc:
            self.point=None;self.raw_point=None;self.reason=str(exc)
            self.mouse.disarm('特征超范围或摄像头配置改变');self.sequence=None;return
        if obs.feature_source!=self.profile['feature_contract']['feature_source']:
            raise ValueError('特征来源版本改变，请重新标定')
        sent=time.perf_counter_ns();result=self.client.submit(*q)
        elapsed_ms=(time.perf_counter_ns()-sent)/1e6
        done=time.monotonic();age=(time.monotonic_ns()-obs.capture_monotonic_ns)/1e9
        if result.status!='ok':raise ValueError('板端拒绝：'+result.status)
        expected=integer_reference(*q,self.profile['wire'])
        if (result.screen_x,result.screen_y)!=expected:raise RuntimeError('FPGA与精确整数参考不一致，停止交互')
        self.mapping_count+=1;self.roundtrip_ms=elapsed_ms;self.frame_to_result_ms=age*1000
        if age>.25:
            self.point=None;self.raw_point=None;self.reason='输出已超过250ms有效期';self.mouse.disarm('帧过期');return
        point=[result.screen_x,result.screen_y];self.raw_point=point
        if self.point:
            distance=math.hypot(point[0]-self.point[0],point[1]-self.point[1]);alpha=.65 if distance>70 else .3
            self.point=[self.point[i]+alpha*(point[i]-self.point[i]) for i in range(2)]
        else:self.point=list(point)
        self.last_result_at=done;self.reason='真实板端映射有效' if self.source=='camera' else '合成输入 · 真实FPGA映射'
        if self.sequence and self.sequence.kind=='validation':
            if self.sequence.observe(obs,point):
                seq=self.sequence;self.profile['validation']=evaluate_targets(seq.groups,1920,1080)
                (self.profile_path.parent/'validation_samples.json').write_text(json.dumps(seq.groups,indent=2)+'\n')
                save_profile(self.profile,self.profile_path);self.sequence=None;self.reason='独立目标评估完成'
        action=None
        duration=getattr(obs,'blink_duration_ms',None)
        if obs.blink_event and obs.blink_event_id>self.last_blink_id:
            self.last_blink_id=obs.blink_event_id
            if self.confirm_enabled and duration is not None and 300<=duration<=800 and not self.sequence:
                action=self.confirm_action;self.virtual_action('camera_blink_candidate',obs.blink_event_id)
        if action:self._mouse_event_id+=1
        self.mouse.apply(self.point,1920,1080,age,action,self._mouse_event_id)

    def close(self):
        self.stop_event.set();self.mouse.disarm('关闭');self.disconnect();self.camera.stop();self.thread.join(2)

