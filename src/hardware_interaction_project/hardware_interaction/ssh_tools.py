"""SSH transport for the user's local PYNQ board; never logs credentials."""
from contextlib import contextmanager
from pathlib import Path
import os
import shlex
import paramiko

@contextmanager
def connection(host='192.168.2.99'):
    client = paramiko.SSHClient()
    client.load_host_keys(str(Path.home()/'.ssh/known_hosts'))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        # PYNQ factory account. A changed credential can be supplied in the environment.
        client.connect(host, username=os.environ.get('HARDWARE_INTERACTION_SSH_USER','xilinx'),
                       password=os.environ.get('HARDWARE_INTERACTION_SSH_PASSWORD','xilinx'),
                       look_for_keys=False, allow_agent=False, timeout=8, auth_timeout=8)
        yield client
    finally:
        client.close()

def checked(client, command, sudo=False, timeout=60):
    if sudo:
        command = "sudo -S -p '' -- /bin/bash -c " + shlex.quote(command)
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if sudo:
        stdin.write(os.environ.get('HARDWARE_INTERACTION_SSH_PASSWORD','xilinx')+'\n')
        stdin.flush()
    stdin.channel.shutdown_write()
    output = stdout.read().decode('utf8','replace')
    error = stderr.read().decode('utf8','replace')
    code = stdout.channel.recv_exit_status()
    if code:
        raise RuntimeError('Board command failed (%d): %s%s' % (code, output, error))
    return output
