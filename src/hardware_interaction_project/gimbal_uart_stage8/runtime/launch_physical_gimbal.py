"""Interactive physical-UART entry; hardware facts and position are never guessed."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

RUNTIME = Path(__file__).resolve().parent
ENTRY = RUNTIME / "main_fpga_gimbal.py"
PROFILE_PATH = RUNTIME / "physical_launch" / "interface_profile.json"
HISTORY_PATH = RUNTIME / "physical_launch" / "recovered_controller_history.json"
ID, ABI, TTL33_PROFILE = 0x47554131, 0x10000, 0x334C5454
WIRING = (
    "用户报告按接线图连接：PMODB 1(TX) -> 驱动器 RXD；"
    "PMODB 2(RX) <- 驱动器 TXD；PMODB 5 或 11(GND) -> 驱动器 GND；"
    "不把原串口板的 5V/3V3 电源线接入 PMODB。"
)


class SetupError(RuntimeError):
    pass


class LaunchCancelled(SetupError):
    pass


def _answer(input_fn, prompt):
    try:
        value = input_fn(prompt).strip()
    except EOFError as error:
        raise LaunchCancelled("输入已结束，取消启动。") from error
    if not value or value.upper() in {"Q", "QUIT", "N", "NO"} or value in {"取消", "否", "未知", "不清楚"}:
        raise LaunchCancelled("已取消启动。")
    return value


def validate_profile(record):
    if not isinstance(record, dict):
        raise SetupError("接口记录必须是 JSON 对象。")
    if record.get("confirmed_3v3_ttl") is not True:
        raise SetupError("接口记录没有明确确认 3.3V TTL 兼容。")
    if type(record.get("profile")) is not int or record["profile"] != TTL33_PROFILE:
        raise SetupError("接口记录的电平配置编号不正确。")
    for field in ("controller_model", "wiring_evidence", "interface_evidence"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise SetupError("接口记录缺少 " + field + "。")
    return record


def load_profile(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        return validate_profile(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError) as error:
        raise SetupError("接口记录不可读取；请用 --setup-interface 重新设置。") from error


def load_history(path):
    """Reuse identified historical facts without converting them to level proof."""
    if path is None or not Path(path).exists():
        return None
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise SetupError("找回的历史资料不可读取；没有据此确认接口电平。") from error
    if not isinstance(record, dict) or type(record.get("schema")) is not int or record["schema"] != 1:
        raise SetupError("找回的历史资料格式不正确。")
    if record.get("electrical_confirmation") != "UNRESOLVED":
        raise SetupError("历史资料只能预填已知事实，不能代替接口电平确认。")
    for field in ("controller_description", "wiring_report", "electrical_review"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise SetupError("历史资料缺少 " + field + "。")
    if not isinstance(record.get("evidence_sources"), list) or not record["evidence_sources"]:
        raise SetupError("历史资料缺少证据来源。")
    return record


def save_profile(path, record):
    validate_profile(record)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Only a completed human declaration reaches this function.
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        shutil.copy2(path, path.with_name(path.name + "." + stamp + ".bak"))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="interface_", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def collect_profile(input_fn=input, output_fn=print, *, history=None):
    output_fn("设置实际云台接口；不知道规格时输入 Q 退出，不能把未知写成已确认。")
    output_fn("必须确认驱动器 RXD 接受 3.3V TTL，回到 PMODB RX 的信号是 0～3.3V TTL。")
    if history is None:
        output_fn("如有电平转换器，记录转换器规格；以前 USB 串口可用本身不是电平依据。")
        model = _answer(input_fn, "控制器型号（查看实际标签）：")
        wiring = WIRING
        evidence_prompt = "电平确认依据（手册具体规格、测量或转换器规格）："
    else:
        model = history["controller_description"]
        wiring = history["wiring_report"]
        output_fn("已自动找回之前成功控制所用资料，不再要求重复填写型号或已报告的接线。")
        output_fn("控制器资料：" + model)
        output_fn(history["electrical_review"])
        output_fn(wiring)
        output_fn("历史记录保留为待核实资料，不会自动生成 3.3V 已确认记录。")
        evidence_prompt = "仅补充旧资料缺少的双向电平依据（具体规格、测量或转换器规格；不知道输入 Q）："
    evidence = _answer(input_fn, evidence_prompt)
    if _answer(input_fn, "确已满足上述双向电平条件，输入 3V3；不确定输入 Q：").upper() != "3V3":
        raise SetupError("没有确认接口电平，未保存接口记录。")
    if history is None:
        output_fn(wiring)
        if _answer(input_fn, "实际接线符合以上记录，输入 WIRED；有疑问输入 Q：").upper() != "WIRED":
            raise SetupError("没有确认实际接线，未保存接口记录。")
    record = {
        "confirmed_3v3_ttl": True,
        "profile": TTL33_PROFILE,
        "controller_model": model,
        "wiring_evidence": wiring,
        "interface_evidence": evidence,
        "confirmation_kind": "user_report_not_automatic_measurement",
        "confirmed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if history is not None:
        record["controller_identification_basis"] = "same_device_user_report_and_manual_association_not_label"
        record["historical_evidence"] = history
    return validate_profile(record)


def parse_steps(text):
    pieces = re.split(r"[\s,，]+", text.strip())
    if len(pieces) != 2 or any(re.fullmatch(r"[+-]?[0-9]+", piece) is None for piece in pieces):
        raise SetupError("请输入两个整数步数：横向 纵向；不接受角度、浮点数或空输入。")
    values = tuple(int(piece) for piece in pieces)
    if any(not -9102 <= value <= 9102 for value in values):
        raise SetupError("两轴初始步数必须在 [-9102, 9102] 内。")
    return values


def request_initial_steps(input_fn=input, output_fn=print):
    output_fn("每次启动都需要本次已知位置，不读取上次位置，也不会自动物理归零。")
    output_fn("Z：实际两轴已朝相机正前方并保持水平，作为本次 0° 起点，启用真实控制。")
    output_fn("S：实际起始步数已知，输入两轴步数后启用真实控制。Q：退出。")
    choice = _answer(input_fn, "配置和接线仍与显示记录一致时，选择 Z / S；否则退出重新设置：").upper()
    if choice == "Z":
        return 0, 0  # Only an explicit current-pose declaration chooses zero.
    if choice != "S":
        raise SetupError("请选择 Z、S 或 Q；没有默认零点。")
    values = parse_steps(_answer(input_fn, "本次已知初始步数（横向 纵向）："))
    if values == (0, 0):
        raise SetupError("0 0 必须通过 Z 明确确认本次水平、正前方姿态。")
    return values


def ensure_idle(snapshot):
    if (not isinstance(snapshot, dict) or type(snapshot.get("id")) is not int
            or type(snapshot.get("abi")) is not int or snapshot.get("id") != ID or snapshot.get("abi") != ABI):
        raise SetupError("板端不是预期的 Stage8 UART IP。")
    telemetry = snapshot.get("telemetry")
    if not isinstance(telemetry, dict):
        raise SetupError("板端状态快照缺少 telemetry。")
    state, status = snapshot.get("state"), snapshot.get("status")
    flags, last_stop = telemetry.get("flags"), telemetry.get("last_stop_status")
    if (type(state) is not int or not 0 <= state <= 15
            or type(status) is not int or not 0 <= status <= 20
            or type(flags) is not int or not 0 <= flags <= 63
            or type(last_stop) is not int or not 0 <= last_stop <= 0xffffffff):
        raise SetupError("板端状态快照无效。")
    if state & 1 or flags & (1 | 4 | 16):
        raise SetupError("板端已 ARM、BUSY、输出使能或正在停止；请先从原会话正常退出，不抢占现有任务。")
    if last_stop not in (0, 18):
        raise SetupError("板端保留看门狗、串行监视或未知异常；本启动器不会清除故障。")
    if flags & 8:
        # Normal STOP/DISARM sets sticky fault and invalidates the command pose.
        # An explicit new Z/S pose may recover only this documented terminal.
        # DONE+ERROR is state 6 because normal safety stop has status 18.
        if last_stop != 18 or flags & 2 or (state, status) not in ((0, 0), (6, 18)):
            raise SetupError("只允许已完整正常停止的状态 18；未知故障或矛盾位置状态不能继续。")
    elif state != 0 or status != 0:
        raise SetupError("板端存在未确认终态；请由原会话处理，启动器不消费其它结果。")
    return snapshot


def read_board_state(host, port):
    sys.path[:0] = [str(RUNTIME.parent / "software"),
                    str(RUNTIME.parent.parent / "interaction_fpga_stage6" / "software")]
    client = None
    try:
        from gimbal_uart_stage8.tcp import TCPClient
        client = TCPClient(host, port)
        client.connect()
        return client.query()
    except ImportError as error:
        raise SetupError("云台客户端依赖不能导入：" + str(error)) from error
    except (OSError, RuntimeError, ValueError) as error:
        raise SetupError("无法读取云台服务：请检查网络，或退出已占用服务的旧窗口。" + str(error)) from error
    finally:
        # This read-only session never owns ARM, so close sends no control.
        if client is not None:
            client.close()


def build_command(profile_path, steps, host, port, *, entry=ENTRY, executable=sys.executable):
    if len(steps) != 2 or any(type(value) is not int or not -9102 <= value <= 9102 for value in steps):
        raise SetupError("缺少本次显式给定的两轴整数初始步数。")
    if type(port) is not int or not 1 <= port <= 65535:
        raise SetupError("端口必须在 1..65535 内。")
    return [str(executable), "-B", "-u", str(Path(entry).resolve()),
            "--board-host", host, "--gimbal-fpga-port", str(port),
            "--arm-gimbal", "--physical-uart",
            "--interface-profile-file", str(Path(profile_path).resolve()),
            "--seed-pan-steps", str(steps[0]), "--seed-tilt-steps", str(steps[1])]


def run_child(command, *, cwd):
    process = subprocess.Popen(command, cwd=cwd)
    try:
        code = process.wait()
    except KeyboardInterrupt:
        # Windows console Ctrl+C is delivered to both processes. Give main's
        # ExitStack time to release its own ARM before a bounded fallback.
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        code = 130
    return subprocess.CompletedProcess(command, code)


def run_launch(profile_path=PROFILE_PATH, *, input_fn=input, output_fn=print,
               state_reader=read_board_state, runner=run_child, entry=ENTRY,
               executable=sys.executable, host="192.168.2.99", port=8769, force_setup=False,
               history_path=HISTORY_PATH):
    output_fn("FPGA 真实云台启动器：准备完成后启用物理 UART；转动目标由 Z 环境模式和双眼眨眼确认。")
    output_fn("先只读检查板端；本启动器不会自动重载位流、复位、停止或抢占其它会话。")
    ensure_idle(state_reader(host, port))
    record = None if force_setup else load_profile(profile_path)
    if record is None:
        history = None if force_setup else load_history(history_path)
        record = collect_profile(input_fn, output_fn, history=history)
        save_profile(profile_path, record)
        output_fn("接口记录已保存。它是用户提供的事实记录，不是程序完成了电气测量。")
    output_fn("控制器：" + record["controller_model"])
    output_fn("电平依据：" + record["interface_evidence"])
    output_fn(record["wiring_evidence"])
    output_fn("更换控制器、接线或电平转换器后，需用 --setup-interface 重新设置。")
    steps = request_initial_steps(input_fn, output_fn)
    # Check again after interaction; main's initializer also refuses ARM/BUSY.
    ensure_idle(state_reader(host, port))
    command = build_command(profile_path, steps, host, port, entry=entry, executable=executable)
    output_fn("本次初始命令状态（步）=" + str(steps) + "；进入真实 UART 云台程序。")
    output_fn("C 校准；Z 环境模式；双眼眨眼确认有效目标；ESC 返回；Q 正常退出。")
    output_fn("界面中的 X0/Y0/Z0 是相机与云台安装位置（mm），不是上述电机初始步数。")
    completed = runner(command, cwd=str(Path(entry).resolve().parent))
    return int(completed.returncode)


def main(argv=None):
    parser = argparse.ArgumentParser(description="新版 FPGA 真实云台启动器（位置不自动默认零点）")
    parser.add_argument("--board-host", default="192.168.2.99")
    parser.add_argument("--gimbal-fpga-port", type=int, default=8769)
    parser.add_argument("--interface-profile-file", type=Path, default=PROFILE_PATH)
    parser.add_argument("--setup-interface", action="store_true", help="重新收集接口事实并备份旧记录")
    parser.add_argument("--check", action="store_true", help="仅只读查服务和本地记录；无输入、无 ARM")
    args = parser.parse_args(argv)
    if not 1 <= args.gimbal_fpga_port <= 65535:
        parser.error("端口必须在 1..65535 内")
    try:
        if args.check:
            state = ensure_idle(read_board_state(args.board_host, args.gimbal_fpga_port))
            print("板端可连接；mode=" + str(state["telemetry"].get("mode")) +
                  "，flags=" + str(state["telemetry"]["flags"]) + "；本次未请求 ARM。")
            record = load_profile(args.interface_profile_file)
            if record is None:
                history = load_history(HISTORY_PATH)
                if history is None:
                    print("尚无电平确认记录；首次正常启动会询问型号、依据及实际接线。")
                else:
                    print("已自动找回控制器资料：" + history["controller_description"])
                    print(history["electrical_review"])
                    print("尚无双向电平确认记录；正常启动只需补充该依据，不再填写型号或已报告接线。")
                return 2
            print("已有接口记录：" + record["controller_model"] + "；每次启动仍须确认本次位置。")
            return 0
        return run_launch(args.interface_profile_file, host=args.board_host,
                          port=args.gimbal_fpga_port, force_setup=args.setup_interface)
    except LaunchCancelled as error:
        print(str(error) + " 本启动器没有启动云台主程序。")
        return 2
    except (SetupError, OSError) as error:
        print("启动未完成：" + str(error))
        return 1
    except KeyboardInterrupt:
        print("启动或主程序已中断；如主程序已经打开，请确认窗口正常退出。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
