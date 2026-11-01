"""Root-only reversible switch; saved original service files are never edited."""
import fcntl
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
UNIT = 'hardware-interaction.service'
ORIGINAL = 'zed2i-fpga.service'

def command(*args, check=True):
    result = subprocess.run(args, check=False, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, universal_newlines=True)
    if check and result.returncode:
        detail = (result.stderr or result.stdout or '').strip()
        raise RuntimeError('Command failed (%d): %s\n%s' %
                           (result.returncode, ' '.join(args), detail))
    return result

def active(unit):
    return command('systemctl','is-active',unit,check=False).returncode == 0

def clients():
    out = command('ss','-Htn','state','established').stdout
    result=[]
    for line in out.splitlines():
        columns=line.split()
        if len(columns)>=4 and any(columns[2].endswith(':'+str(p)) for p in range(8765,8771)):
            result.append(line)
    return result

def wait_ready():
    for _ in range(50):
        if not active(UNIT):
            time.sleep(.2)
            continue
        try:
            with socket.create_connection(('192.168.2.99',8770),.3) as sock:
                sock.settimeout(2)
                line=b''
                while not line.endswith(b'\n') and len(line)<1024:
                    block=sock.recv(1)
                    if not block:break
                    line+=block
                info=json.loads(line)
                if info.get('project')=='hardware_interaction' and info.get('abi')==3:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(.2)
    journal = command('journalctl','-u',UNIT,'-n','30','--no-pager','-o','cat',check=False)
    raise RuntimeError('Hardware interaction service did not become ready:\n'+journal.stdout.strip())

def ensure():
    if active(UNIT):
        raise RuntimeError('硬件交互已运行；请先关闭已有硬件交互窗口')
    if clients():
        raise RuntimeError('原眼控或板端程序正在使用 FPGA，请先正常关闭后再启动硬件交互')
    from pynq import MMIO
    uart=MMIO(0x43c40000,0x10000)
    if uart.read(8)!=0x47554131 or uart.read(12)!=0x10000:
        raise RuntimeError('Current board is not the expected Stage8 design')
    # Read-only guard. No STOP, DISARM, initialization or motor command here.
    if uart.read(0xb4)&1:
        raise RuntimeError('UART controller is armed; close the owning application first')
    manifest=json.loads((ROOT/'hardware_interaction/release.json').read_text())
    for name,digest in manifest['files'].items():
        p=ROOT/name
        if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Board package hash mismatch: '+name)
    for name in ('hardware_interaction.bit','hardware_interaction.hwh'):
        p=ROOT/'hardware_interaction/artifacts'/name
        if hashlib.sha256(p.read_bytes()).hexdigest()!=manifest[name]:
            raise RuntimeError('Hardware interaction release hash mismatch: '+name)
    original_active=active(ORIGINAL)
    if not original_active:
        raise RuntimeError('Original FPGA service is not active; inspect before switching')
    command('systemctl','stop',ORIGINAL)
    try:
        command('systemctl','reset-failed',UNIT,check=False)
        command('systemd-run','--unit=hardware-interaction','--property=Type=simple',
                '--property=TimeoutStopSec=20',
                '--property=ExecStopPost=/bin/systemctl start '+ORIGINAL,
                '--property=WorkingDirectory='+str(ROOT),
                '/usr/bin/python3','-B','-u',str(ROOT/'gimbal_uart_stage8/software/gimbal_uart_stage8/board_server.py'),
                '--bit',str(ROOT/'hardware_interaction/artifacts/hardware_interaction.bit'),
                '--bit-sha256',manifest['hardware_interaction.bit'],
                '--hwh-sha256',manifest['hardware_interaction.hwh'])
        wait_ready()
    except BaseException:
        command('systemctl','stop',UNIT,check=False)
        command('systemctl','start',ORIGINAL)
        raise
    print(json.dumps({'state':'hardware_interaction_ready','original_restores_on_stop':True}))

def restore():
    if clients():
        raise RuntimeError('仍有程序连接板卡，暂不切换 FPGA；先正常关闭相关窗口')
    command('systemctl','stop',UNIT,check=False)
    command('systemctl','start',ORIGINAL)
    for _ in range(50):
        if active(ORIGINAL):
            try:
                with socket.create_connection(('192.168.2.99',8769),.3):
                    print(json.dumps({'state':'original_restored'}));return
            except OSError:pass
        time.sleep(.2)
    raise RuntimeError('Original service has not recovered; inspect journal')

if __name__=='__main__':
    with open('/tmp/fpga_variant_switch.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        {'ensure':ensure,'restore':restore}[sys.argv[1]]()
