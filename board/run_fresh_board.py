"""Fresh-PYNQ deployment entry with explicit project and ZED paths.

Default mode runs eye control and ZED without motor commands.
"""
import argparse
import importlib.util
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time

HOST = "192.168.2.99"
REMOTE = "/home/xilinx/jupyter_notebooks/hardware_interaction_20261026"
UNIT = "hardware-interaction-fresh.service"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--zed-python", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Local checks only")
    parser.add_argument("--validate-board", action="store_true", help="PL selftest only; no camera/motor")
    parser.add_argument("--physical-gimbal", action="store_true")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--seed-pan-steps", type=int)
    parser.add_argument("--seed-tilt-steps", type=int)
    args = parser.parse_args()
    root = args.project.resolve()
    entry = root / "gimbal_uart_stage8/runtime/main_fpga_gimbal.py"
    if not entry.is_file() or not args.zed_python.is_file():
        parser.error("Project entry or ZED Python does not exist")
    if args.physical_gimbal and (args.profile is None or not args.profile.is_file()
                                or args.seed_pan_steps is None or args.seed_tilt_steps is None):
        parser.error("Physical mode requires your verified interface profile and both known-position seeds")
    if args.check:
        subprocess.run([sys.executable, "-B", str(entry), "--help"], check=True)
        subprocess.run([str(args.zed_python), "-c",
                        "import cv2,numpy,pyautogui,pyzed.sl as sl; print('ZED SDK',sl.Camera.get_sdk_version())"],
                       check=True)
        print("LOCAL_CHECK_PASS: no SSH, camera, bitstream load or motor commands")
        return 0
    spec = importlib.util.spec_from_file_location("reproduction_project_launcher", root / "launch_hardware_interaction.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    checked, connection = launcher.checked, launcher.connection
    # Fresh-board route: refuse to interfere with any existing deployment.
    with connection() as client:
        for unit in ("zed2i-fpga.service", "hardware-interaction.service", UNIT):
            if checked(client, "systemctl is-active " + unit + " || true").strip() == "active":
                raise RuntimeError("Existing service is active: " + unit + "; do not use the fresh-board route")
        listeners = checked(client, "ss -H -ltn")
        for line in listeners.splitlines():
            fields = line.split()
            if len(fields) >= 4 and fields[3].rsplit(":", 1)[-1] in {str(p) for p in range(8765, 8771)}:
                raise RuntimeError("A required board port is already in use: " + line)
    (root / "evidence").mkdir(exist_ok=True)
    release = launcher.ensure_package()
    server = REMOTE + "/gimbal_uart_stage8/software/gimbal_uart_stage8/board_server.py"
    command = shlex.join([
        "systemd-run", "--unit=hardware-interaction-fresh", "--property=Type=simple",
        "--property=TimeoutStopSec=20", "--property=WorkingDirectory=" + REMOTE,
        "/usr/bin/python3", "-B", "-u", server,
        "--bit", REMOTE + "/hardware_interaction/artifacts/hardware_interaction.bit",
        "--bit-sha256", release["hardware_interaction.bit"],
        "--hwh-sha256", release["hardware_interaction.hwh"],
    ])
    started, lease, child = False, None, None
    try:
        with connection() as client:
            checked(client, "systemctl reset-failed " + UNIT + " || true", sudo=True)
            checked(client, command, sudo=True)
            started = True
        deadline = time.monotonic() + 20
        while True:
            try:
                with socket.create_connection((HOST, 8770), 1):
                    pass
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Service not ready; inspect journalctl -u " + UNIT)
                time.sleep(.25)
        lease = launcher.BoardLease(release)
        launcher.selftest()
        if args.validate_board:
            print("BOARD_SELFTEST_PASS: synthetic observations only; no camera or motor")
            return 0
        child_args = [sys.executable, "-B", "-u", str(entry), "--zed-python", str(args.zed_python)]
        if args.physical_gimbal:
            child_args += ["--physical-uart", "--arm-gimbal", "--interface-profile-file", str(args.profile),
                           "--seed-pan-steps", str(args.seed_pan_steps),
                           "--seed-tilt-steps", str(args.seed_tilt_steps), "--laser-mount-setup"]
        else:
            child_args += ["--no-gimbal"]
        child = subprocess.Popen(child_args, cwd=entry.parent)
        while True:
            try:
                return child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                if lease.error is not None:
                    raise RuntimeError("Board heartbeat failed") from lease.error
    finally:
        if child is not None and child.poll() is None:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)
        if lease is not None:
            lease.close()
        if started:
            with connection() as client:
                checked(client, "systemctl stop " + UNIT, sudo=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
