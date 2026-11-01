"""独立硬件交互入口：首次上传、空闲切换、自检、退出恢复原板端。"""
from pathlib import Path
import json
import hashlib
import shlex
import socket
import subprocess
import sys
import threading

ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT/'hardware_interaction'),str(ROOT),
              str(ROOT/'interaction_fpga_stage6/software')]
from ssh_tools import connection,checked
REMOTE='/home/xilinx/jupyter_notebooks/hardware_interaction_20261026'

def board(action):
    with connection() as client:
        print(checked(client,'/usr/bin/python3 -B '+shlex.quote(REMOTE+'/hardware_interaction/board_manager.py')+' '+action,sudo=True).strip(),flush=True)

def ensure_package():
    local=ROOT/'hardware_interaction/release.json'
    release=json.loads(local.read_text(encoding='utf8'))
    for name,digest in release['files'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:
            raise RuntimeError('本地发布文件发生变化：'+name)
    with connection() as client:
        with client.open_sftp() as sftp:
            try:
                with sftp.open(REMOTE+'/hardware_interaction/release.json','rb') as stream:remote=stream.read()
            except OSError:remote=b''
    if remote!=local.read_bytes():
        from deploy import main as deploy
        print('[硬件交互] 上传独立板端包。',flush=True);deploy()
    return release

class BoardLease:
    def __init__(self,release,host='192.168.2.99'):
        self.stop=threading.Event();self.error=None
        self.sock=socket.create_connection((host,8770),3);self.sock.settimeout(5)
        self.stream=self.sock.makefile('rb')
        info=json.loads(self.stream.readline(2048))
        if info.get('project')!='hardware_interaction' or info.get('abi')!=3 or info.get('bit_sha256')!=release['hardware_interaction.bit']:
            self.close();raise RuntimeError('板端发布身份不匹配')
        self.thread=threading.Thread(target=self.heartbeat,daemon=True);self.thread.start()
    def heartbeat(self):
        try:
            while not self.stop.wait(3):
                self.sock.sendall(b'ping\n')
                if self.stream.readline(128)!=b'pong\n':raise RuntimeError('Board lifecycle heartbeat failed')
        except Exception as e:self.error=e
    def close(self):
        self.stop.set()
        try:self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        self.stream.close();self.sock.close()
        if hasattr(self,'thread'):self.thread.join(timeout=6)

def selftest():
    from interaction_stage6.tcp import TCPClient
    from hardware_interaction.adapter import HardwareInteractionAdapter
    c=TCPClient();c.connect();a=HardwareInteractionAdapter(c)
    try:
        expected=[(0,0,0,0),(0,0,40,0),(.6,.6,80,1),(.6,.6,120,0),
                  (0,0,160,0),(0,0,200,0),(0,.7,240,2)]
        for left,right,ms,kind in expected:
            if a.observe_eyes(left,right,0,stamp=ms)['class']!=kind:
                raise RuntimeError('PL眨眼状态自检失败')
        for ratio,ms,opened in ((.45,0,True),(.3,40,True),(.2,80,False),(.3,120,False)):
            if a.observe_mouth(ratio,0,stamp=ms)['open']!=opened:
                raise RuntimeError('PL嘴部开闭自检失败')
        a.reset_observations()
        (ROOT/'evidence/last_startup_selftest.json').write_text(json.dumps({'state':'PASS','eye_samples':7,'mouth_samples':4,'camera_used':False,'windows_actions':False,'motor_commands':False},indent=2),encoding='utf8')
    finally:c.close()
    print('[硬件交互] PL眨眼和嘴部状态自检通过。',flush=True)

def main():
    entry=ROOT/'gimbal_uart_stage8/runtime/main_fpga_gimbal.py'
    if '--check' in sys.argv:
        subprocess.run([sys.executable,'-B',str(entry),'--help'],check=True)
        print('本地入口检查通过；未连接板卡、未打开摄像头。');return 0
    switched=False;lease=None;child=None
    try:
        release=ensure_package();board('ensure');switched=True
        lease=BoardLease(release);selftest()
        if '--validate-board' in sys.argv:return 0
        args=[sys.executable,'-B','-u',str(entry),'--physical-uart','--arm-gimbal',
              '--seed-pan-steps','0','--seed-tilt-steps','0','--interface-profile-file',
              str(ROOT/'gimbal_uart_stage8/runtime/physical_launch/interface_profile.json'),*sys.argv[1:]]
        child=subprocess.Popen(args,cwd=entry.parent)
        while True:
            try:return child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                if lease.error is not None:raise RuntimeError('板端生命周期连接失败') from lease.error
    except KeyboardInterrupt:return 130
    finally:
        if child is not None and child.poll() is None:
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=10)
        if lease is not None:lease.close()
        if switched:
            print('[硬件交互] 恢复原板端服务…',flush=True);board('restore')

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:
        print('[硬件交互] 运行失败：'+str(error)+'\n请保留上面的具体错误信息用于排查。',file=sys.stderr,flush=True)
        sys.exit(1)
