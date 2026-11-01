"""Upload an isolated package; never overwrite the original board tree."""
import hashlib
import json
from pathlib import Path
import shlex
import tarfile
from ssh_tools import connection,checked

ROOT=Path(__file__).resolve().parents[1]
REMOTE='/home/xilinx/jupyter_notebooks/hardware_interaction_20261026'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    release=json.loads((ROOT/'hardware_interaction/release.json').read_text(encoding='utf8'))
    for name,digest in release['files'].items():
        if sha(ROOT/name)!=digest:raise RuntimeError('Local package changed: '+name)
    archive=ROOT/'evidence/board_package.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for folder in ('hardware_interaction','gimbal_uart_stage8/software','gimbal_fpga_stage7/software',
                       'interaction_fpga_stage6/software','stereo_fpga_stage4/software','software'):
            for p in (ROOT/folder).rglob('*'):
                if p.is_file() and '__pycache__' not in p.parts and not any(x in p.parts for x in ('tests','build_source','scripts')) and p.suffix in ('.py','.json','.bit','.hwh','.txt'):
                    tar.add(p,arcname=p.relative_to(ROOT).as_posix())
    with connection() as c:
        if checked(c,'systemctl is-active hardware-interaction.service || true').strip()=='active':
            raise RuntimeError('硬件交互板端服务正在运行，请先正常退出')
        checked(c,'mkdir -p '+shlex.quote(REMOTE))
        with c.open_sftp() as sftp:sftp.put(str(archive),REMOTE+'/package.tar.gz')
        remote_sha=checked(c,'sha256sum '+shlex.quote(REMOTE+'/package.tar.gz')).split()[0]
        if remote_sha!=sha(archive):raise RuntimeError('Uploaded package hash mismatch')
        checked(c,'tar -xzf '+shlex.quote(REMOTE+'/package.tar.gz')+' -C '+shlex.quote(REMOTE))
        hashes=checked(c,'sha256sum '+shlex.quote(REMOTE+'/hardware_interaction/artifacts/hardware_interaction.bit')+' '+shlex.quote(REMOTE+'/hardware_interaction/artifacts/hardware_interaction.hwh'))
        (ROOT/'evidence/deployment.json').write_text(json.dumps({'remote':REMOTE,'sha256':remote_sha,'artifacts':hashes},indent=2),encoding='utf8')
        print('Isolated board package uploaded and hashes verified; original service unchanged.')

if __name__=='__main__':main()
