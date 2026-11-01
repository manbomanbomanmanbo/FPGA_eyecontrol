"""Local OpenCV eye-control windows. No HTTP server and no browser required."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import time

import cv2
import numpy as np
CAMERA_WINDOW='Eye Control'
GRID_WINDOW='Calibration Grid'
CAMERA_SIZE=(1000,800)
GRID_SIZE=(1200,900)
CYAN=(255,255,0); ORANGE=(0,165,255); WHITE=(255,255,255)

def target_pixel(target,width,height):
    return (round(target[0]*(width-1)),round(target[1]*(height-1)))

def draw_camera(state,jpeg=None,error=''):
    """Original camera HUD; backend diagnostics remain in the log."""
    frame=None
    if jpeg:
        frame=cv2.imdecode(np.frombuffer(jpeg,np.uint8),cv2.IMREAD_COLOR)
    if frame is None:frame=np.full((CAMERA_SIZE[1],CAMERA_SIZE[0],3),(20,18,15),np.uint8)
    else:frame=cv2.resize(frame,CAMERA_SIZE)
    h,w=frame.shape[:2]
    overlay=frame.copy();cv2.rectangle(overlay,(0,0),(350,h),(15,12,10),-1)
    cv2.addWeighted(overlay,.6,frame,.4,0,frame)
    def label(value,xy,scale,color,thickness):
        cv2.putText(frame,value,xy,cv2.FONT_HERSHEY_DUPLEX,scale,(0,0,0),thickness+2,cv2.LINE_AA)
        cv2.putText(frame,value,xy,cv2.FONT_HERSHEY_DUPLEX,scale,color,thickness,cv2.LINE_AA)
    obs=state.get('observation') or {}
    eye=None
    if obs.get('valid') and obs.get('eye_position') and obs.get('width',0)>1 and obs.get('height',0)>1:
        ex,ey=obs['eye_position']
        eye=(round((obs['width']-1-ex)/(obs['width']-1)*(w-1)),round(ey/(obs['height']-1)*(h-1)))
        ex,ey=eye
        for dx,dy in ((-1,-1),(1,-1),(-1,1),(1,1)):
            cv2.line(frame,(ex+dx*30,ey+dy*30),(ex+dx*15,ey+dy*30),CYAN,3)
            cv2.line(frame,(ex+dx*30,ey+dy*30),(ex+dx*30,ey+dy*15),CYAN,3)
        cv2.circle(frame,eye,int(35+(time.time()*12)%15),CYAN,2)
    active=bool(state.get('mouse_enabled') and state.get('point') is not None)
    status='ACTIVE' if active else ('CALIBRATING' if state.get('sequence') else 'WAITING')
    if error or state.get('error') or state.get('board_link_error'):status='ERROR'
    label('STATUS:',(25,60),.8,WHITE,1);label(status,(150,60),1,CYAN if active else ORANGE,2)
    label('EYE POSITION',(25,140),.7,WHITE,1)
    label('X: '+(str(eye[0]) if eye else '-'),(40,190),1.2,CYAN,2)
    label('Y: '+(str(eye[1]) if eye else '-'),(40,240),1.2,CYAN,2)
    point=state.get('point');geometry=state.get('screen_geometry') or {}
    if point is not None:
        sw,sh=geometry.get('physical_size',(1920,1080))
        point=(round(point[0]/1919*(sw-1)),round(point[1]/1079*(sh-1)))
    label('SCREEN PROJ',(25,320),.7,WHITE,1)
    label('X: '+(str(point[0]) if point is not None else '-'),(40,370),1.2,ORANGE,2)
    label('Y: '+(str(point[1]) if point is not None else '-'),(40,420),1.2,ORANGE,2)
    return frame

def draw_grid(state,size=GRID_SIZE,capture=False):
    width,height=size;display=np.full((height,width,3),(20,18,15),np.uint8)
    sequence=state.get('sequence')
    if capture and sequence:
        x,y=target_pixel(sequence['target'],width,height)
        cv2.circle(display,(x,y),32,CYAN,3,cv2.LINE_AA)
        cv2.circle(display,(x,y),9,WHITE,-1,cv2.LINE_AA)
        return display
    # Reproduce the old preview layout on its original 1200x900 canvas.
    base=np.full((900,1200,3),(20,18,15),np.uint8)
    for index,(tx,ty) in enumerate(( (x,y) for y in (.12,.5,.88) for x in (.12,.5,.88))):
        x=int(tx*1200);y=int(ty*900*.6+80)
        cv2.circle(base,(x,y),15,(80,80,0),1,cv2.LINE_AA)
        cv2.circle(base,(x,y),4,CYAN,-1)
        cv2.putText(base,'CH_%d'%(index+1),(x+20,y+5),cv2.FONT_HERSHEY_SIMPLEX,.6,(120,120,120),1,cv2.LINE_AA)
    cv2.rectangle(base,(0,740),(1200,900),(35,30,25),-1)
    cv2.line(base,(0,740),(1200,740),CYAN,1)
    cv2.line(base,(600,770),(600,870),(70,70,70),1)
    font=cv2.FONT_HERSHEY_DUPLEX
    for value,xy,color in (
        ('CONTROL PANEL',(40,775),ORANGE),('> PRESS [C] TO CALIBRATE',(50,815),WHITE),
        ('> PRESS [Q]     TO EXIT',(50,860),WHITE),('GESTURE GUIDE',(640,780),ORANGE),
        ('> POUT L/R : CHANGE SCROLL DIR',(650,815),WHITE),
        ('> OPEN MOUTH: START SCROLLING',(650,860),WHITE)):
        cv2.putText(base,value,xy,font,.8,color,1,cv2.LINE_AA)
    cv2.rectangle(base,(0,0),(1200,90),(30,25,20),-1)
    cv2.putText(base,'MULTIMODAL VISION-BASED INTERACTION TERMINAL',(40,50),font,1,WHITE,2,cv2.LINE_AA)
    return base if size==GRID_SIZE else cv2.resize(base,size)

def handle_key(session,key,screen):
    """The only normal keyboard commands are C and Q."""
    if key in (ord('q'),ord('Q')):return False
    if key in (ord('c'),ord('C')):session.start_calibration(screen)
    return True

def physical_screen():
    if os.name=='nt':return (ctypes.windll.user32.GetSystemMetrics(0),ctypes.windll.user32.GetSystemMetrics(1))
    raise RuntimeError('This desktop build currently supports Windows primary display only')

def capture_focus():
    if os.name!='nt':return False
    user=ctypes.windll.user32
    user.FindWindowW.restype=ctypes.c_void_p;user.GetForegroundWindow.restype=ctypes.c_void_p
    return user.GetForegroundWindow()==user.FindWindowW(None,GRID_WINDOW)

def activate_grid():
    if os.name=='nt':
        user=ctypes.windll.user32
        user.FindWindowW.restype=ctypes.c_void_p;user.SetForegroundWindow.argtypes=[ctypes.c_void_p]
        user.SetForegroundWindow(user.FindWindowW(None,GRID_WINDOW))

def windows_open():
    try:return all(cv2.getWindowProperty(name,cv2.WND_PROP_VISIBLE)>=1 for name in (CAMERA_WINDOW,GRID_WINDOW))
    except cv2.error:return False

def main():
    # The daily entry uses the original application with the FPGA adapter.
    from .legacy_native import main as run_original
    return run_original()


def _historical_frontend():
    parser=argparse.ArgumentParser(description='Native eye-control windows with real PYNQ mapping')
    parser.add_argument('--board-host',default='192.168.2.99');parser.add_argument('--board-port',type=int,default=8765)
    parser.add_argument('--camera-index',type=int,default=0);parser.add_argument('--output',default=str(Path(__file__).resolve().parents[2]/'runtime_native'))
    args=parser.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    if os.name=='nt':
        try:ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:pass
    from .native_session import NativeSession
    session=NativeSession(out,args.board_host,args.board_port,args.camera_index,
                          simple_controls=True)
    screen=physical_screen();error='';capture=False;presented=None;status_written=0
    try:
        cv2.namedWindow(CAMERA_WINDOW,cv2.WINDOW_NORMAL);cv2.resizeWindow(CAMERA_WINDOW,800,600)
        cv2.namedWindow(GRID_WINDOW,cv2.WINDOW_NORMAL);cv2.resizeWindow(GRID_WINDOW,*GRID_SIZE)
        cv2.moveWindow(CAMERA_WINDOW,125,max(30,(screen[1]-700)//2))
        cv2.moveWindow(GRID_WINDOW,max(10,screen[0]-1265),max(30,(screen[1]-920)//2))
        startup_pending=True
        while True:
            if not windows_open():return
            state=session.snapshot();want_capture=bool(state.get('sequence'))
            if time.monotonic()-status_written>=1:
                status={'application':'eye_control_fpga_native','pid':os.getpid(),'running':True,
                        'source':state['source'],'camera_running':state['camera']['running'],
                        'mouse_enabled':state['mouse_enabled'],'connected':state['connected'],
                        'mapping_count':state['mapping_count'],'mapping_failures':state['mapping_failures'],
                        'link_mode':state['link_mode'],'board_link_error':state['board_link_error'],
                        'reason':state['reason'],'error':state['error']}
                tmp=out/'native_status.tmp';tmp.write_text(json.dumps(status,indent=2)+'\n');tmp.replace(out/'native_status.json')
                status_written=time.monotonic()
            if want_capture!=capture:
                capture=want_capture
                if capture:cv2.moveWindow(GRID_WINDOW,0,0)
                cv2.setWindowProperty(GRID_WINDOW,cv2.WND_PROP_FULLSCREEN,cv2.WINDOW_FULLSCREEN if capture else cv2.WINDOW_NORMAL)
                if not capture:cv2.resizeWindow(GRID_WINDOW,*GRID_SIZE)
                else:activate_grid()
                presented=None
            screen=physical_screen()
            camera_image=draw_camera(state,session.camera.preview_jpeg(),error)
            grid_image=draw_grid(state,screen if capture else GRID_SIZE,capture)
            cv2.imshow(CAMERA_WINDOW,camera_image);cv2.imshow(GRID_WINDOW,grid_image)
            key=cv2.waitKeyEx(30)
            if key>=0:key=key&255
            rect=cv2.getWindowImageRect(GRID_WINDOW)
            fullscreen=cv2.getWindowProperty(GRID_WINDOW,cv2.WND_PROP_FULLSCREEN)==cv2.WINDOW_FULLSCREEN
            session.heartbeat(screen,visible=True,focused=capture_focus(),capture_rect=rect,capture_fullscreen=fullscreen)
            if startup_pending:
                session.start_application();startup_pending=False
            token=(state.get('sequence') or {}).get('target_token')
            if capture and token and token!=presented and session.ack_target(token):
                presented=token
            try:
                if not handle_key(session,key,screen):break
                if key in (ord('c'),ord('C')):error=''
            except Exception as exc:error=str(exc)
    finally:
        session.close()
        stopped={'application':'eye_control_fpga_native','pid':os.getpid(),'running':False,
                 'camera_running':session.camera.status()['running'],'mouse_enabled':session.mouse.enabled,
                 'connected':bool(session.client and session.client.connected)}
        (out/'native_status.json').write_text(json.dumps(stopped,indent=2)+'\n')
        for name in (CAMERA_WINDOW,GRID_WINDOW):
            try:cv2.destroyWindow(name)
            except cv2.error:pass

if __name__=='__main__':main()
