"""Original eye/ZED interface, with UART waveform and command state in PL."""
import argparse
from contextlib import ExitStack
import hashlib
import math
from pathlib import Path
import sys

_STAGE = Path(__file__).resolve().parents[1]
_PROJECT = _STAGE.parent
sys.path[:0] = [str(_PROJECT), str(_STAGE / "software"), str(_PROJECT / "interaction_fpga_stage6" / "software")]

from gimbal_uart_stage8.runtime_adapter import PLPanTilt, GimbalMotionPending
from gimbal_uart_stage8.protocol import TTL33_PROFILE
from laser_mount_setup import load_laser_offset, request_laser_offset
from main_fpga import (
    FpgaEyeControlSystem,
    FpgaEyeTracker,
    LegacyFpgaMapper,
    MouseController,
)
from interaction_stage6.tcp import TCPClient
from hardware_interaction.adapter import HardwareInteractionAdapter as InteractionAdapter
from interaction_runtime import FpgaInteractionMouse
from hardware_interaction.tracker import HardwareInteractionTracker as FpgaInteractionTracker


class GimbalFpgaEyeControlSystem(FpgaEyeControlSystem):
    def __init__(self, mapper, tracker, mouse, zed_python, stereo_port,
                 gimbal_port=None, laser_mount_config=None,
                 laser_mount_setup=False, gimbal_planner=None):
        if gimbal_planner is None:
            raise RuntimeError("未注入 FPGA UART 控制接口")
        self.gimbal = None
        self.laser_mount_config = (Path(__file__).resolve().with_name("laser_mount.json")
                                   if laser_mount_config is None else Path(laser_mount_config))
        self.laser_mount_setup = laser_mount_setup
        self.laser_mount_configured = False
        self._trace_sink = getattr(mouse, "trace_event", None)
        source = Path(__file__).resolve()
        self._record_event("gimbal_entry_start", entry=str(source),
                           source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                           python=sys.executable, argv=sys.argv,
                           port=gimbal_port)
        try:
            self.gimbal = PLPanTilt(gimbal_planner, event_callback=self._record_event)
            super().__init__(mapper, tracker, mouse, zed_python, stereo_port)
        except BaseException as error:
            self._record_event("gimbal_init_failed", error=str(error))
            self.close()
            raise

    def _record_event(self, event, **details):
        if self._trace_sink is None:
            return

        def serialisable(value):
            if isinstance(value, float) and not math.isfinite(value):
                return repr(value)
            if isinstance(value, dict):
                return {str(key): serialisable(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [serialisable(item) for item in value]
            return value

        try:
            self._trace_sink(event, **serialisable(details))
        except Exception:
            pass

    def _sync_gimbal_origin(self):
        if self.gimbal is None or not self.zero_initialized:
            return False
        origin = (self.zero_x, self.zero_y, self.zero_z)
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) for value in origin):
            return False
        self.gimbal.offset_x = self.zero_x / 1000.0
        self.gimbal.offset_y = -self.zero_y / 1000.0
        self.gimbal.offset_z = self.zero_z / 1000.0
        return True

    def input_environment_zero(self):
        confirmed = super().input_environment_zero()
        if not confirmed or not self._sync_gimbal_origin():
            self.zero_initialized = False
            self._record_event("gimbal_origin_rejected", confirmed=bool(confirmed),
                               origin_mm=[self.zero_x, self.zero_y, self.zero_z])
            return False
        self._record_event("gimbal_origin_confirmed",
                           origin_mm=[self.zero_x, self.zero_y, self.zero_z],
                           offsets_metres=[self.gimbal.offset_x,
                                           self.gimbal.offset_y,
                                           self.gimbal.offset_z])
        try:
            laser_offset = load_laser_offset(self.laser_mount_config)
        except (ValueError, OSError) as error:
            self._record_event("gimbal_laser_config_error", error=str(error))
            laser_offset = None
        if self.laser_mount_setup:
            laser_offset = request_laser_offset(self.laser_mount_config, laser_offset)
        if laser_offset is not None:
            self.gimbal.configure_laser_offset(*laser_offset)
            self.laser_mount_configured = True
            self._record_event("gimbal_laser_mount_configured", laser_offset_metres=laser_offset,
                               measurement_in_this_session=False)
        else:
            self._record_event("gimbal_laser_mount_unmeasured")
        return True

    @staticmethod
    def _target_in_metres(response):
        if not isinstance(response, dict) or "error" in response:
            return None
        coordinates = [response.get(key) for key in ("x_mm", "y_mm", "z_mm")]
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               for value in coordinates):
            return None
        x_mm, y_mm, z_mm = coordinates
        if not all(math.isfinite(value) for value in coordinates) or z_mm <= 0:
            return None
        # ZED replies are millimetres / IMAGE Y-down; control.py uses metres / Y-up.
        return x_mm / 1000.0, -y_mm / 1000.0, z_mm / 1000.0

    def send_zed_command(self, cmd_dict):
        self._record_event("gimbal_zed_request", command=cmd_dict,
                           coord_output_mode=self.coord_output_mode,
                           gimbal_active=self.gimbal is not None)
        reader = getattr(self.gimbal, "read_pending_reply", None)
        if reader is not None:
            reader()
        response = super().send_zed_command(cmd_dict)
        self._record_event("gimbal_zed_reply", command=cmd_dict, response=response)
        if (self.gimbal is None
                or not self.coord_output_mode
                or cmd_dict.get("cmd") != "get_coord"):
            self._record_event("gimbal_target_skipped", command=cmd_dict,
                               reason="inactive_or_not_environment_target",
                               gimbal_active=self.gimbal is not None,
                               coord_output_mode=self.coord_output_mode)
            return response
        target = self._target_in_metres(response)
        if target is None:
            self._record_event("gimbal_target_rejected", reason="invalid_zed_coordinates",
                               response=response)
            print("[激光云台] ZED 未返回有效目标坐标，不发送运动指令。")
            return response
        if not self._sync_gimbal_origin():
            self._record_event("gimbal_target_rejected", reason="invalid_or_unconfirmed_origin",
                               target_metres=target,
                               origin_mm=[self.zero_x, self.zero_y, self.zero_z],
                               zero_initialized=self.zero_initialized)
            print("[激光云台] 尚未设置有效的云台安装位置，不发送运动指令。")
            return response
        if not self.gimbal.client.owns_arm:
            self._record_event("gimbal_target_skipped", reason="explicit_arm_required",
                               target_metres=target)
            print("[激光云台] 尚未明确 ARM，已取得目标坐标，云台保持停用。")
            return response
        self._record_event("gimbal_target_confirmed", target_metres=target,
                           origin_mm=[self.zero_x, self.zero_y, self.zero_z],
                           laser_mount_configured=self.laser_mount_configured)
        try:
            self.gimbal.goto_point(*target)
        except GimbalMotionPending as error:
            self._record_event("gimbal_target_skipped", reason="previous_motion_pending",
                               target_metres=target, error=str(error))
        except ValueError as error:
            self._record_event("gimbal_target_rejected", reason="invalid_or_unreachable_target",
                               target_metres=target, error=str(error))
            print(f"[激光云台] 不执行当前目标：{error}")
        except Exception as error:
            self._record_event("gimbal_disabled", target_metres=target, error=str(error))
            print(f"[激光云台] 命令发送失败，本次运行已停用云台：{error}")
            self._close_gimbal()
        return response

    def _close_gimbal(self):
        gimbal, self.gimbal = self.gimbal, None
        if gimbal is None:
            return
        self._record_event("gimbal_close_start")
        diagnostic_error = None
        reader = getattr(gimbal, "read_pending_reply", None)
        if reader is not None:
            try:
                reader()
            except Exception as error:
                diagnostic_error = error
                self._record_event("gimbal_final_diagnostics_failed", error=str(error))
        try:
            gimbal.close()
        except Exception as error:
            self._record_event("gimbal_close_failed", error=str(error))
            print(f"[云台] FPGA 停止/释放失败：{error}")
            raise
        if diagnostic_error is not None:
            self._record_event("gimbal_close_unconfirmed", error=str(diagnostic_error))
            raise RuntimeError("退出时云台诊断失败；不能确认本次停止状态") from diagnostic_error
        self._record_event("gimbal_close_finished", cleanup_completed=True)

    def close(self):
        try:
            self._close_gimbal()
        finally:
            super().close()


def main():
    parser = argparse.ArgumentParser(
        description="FPGA 眼控、ZED 2i 与 PL 直接 UART 云台")
    parser.add_argument("--board-host", default="192.168.2.99")
    parser.add_argument("--gaze-port", type=int, default=8765)
    parser.add_argument("--stereo-port", type=int, default=8766)
    parser.add_argument("--interaction-port", type=int, default=8767)
    parser.add_argument("--zed-python", default=r"E:\zed_workspace\envs\zed\python.exe")
    parser.add_argument("--gimbal-fpga-port", type=int, default=8769)
    parser.add_argument("--arm-gimbal", action="store_true", help="明确建立给定命令初始状态并ARM")
    parser.add_argument("--seed-pan-steps", type=int)
    parser.add_argument("--seed-tilt-steps", type=int)
    parser.add_argument("--physical-uart", action="store_true", help="使用外部UART，需要独立确认的接口记录")
    parser.add_argument("--interface-profile-file", type=Path)
    parser.add_argument("--no-gimbal", action="store_true",
                        help="运行眼控和 ZED 流程但不连接或控制云台")
    parser.add_argument("--laser-mount-config", default=None)
    parser.add_argument("--laser-mount-setup", action="store_true",
                        help="重新填写激光安装偏移，保存后下次启动自动读取")
    parser.add_argument("--gimbal-speed", type=int, default=6, help="云台电机速度，RPM")
    args = parser.parse_args()
    if not all(1 <= port <= 65535 for port in
               (args.gaze_port, args.stereo_port, args.interaction_port, args.gimbal_fpga_port)):
        parser.error("端口必须在 1..65535 范围内")
    if not Path(args.zed_python).is_file():
        parser.error("ZED Python 解释器不存在: " + args.zed_python)
    if not 3 <= args.gimbal_speed <= 65535:
        parser.error("云台速度必须在 3..65535 RPM 内")
    profile = None
    if args.arm_gimbal:
        if args.seed_pan_steps is None or args.seed_tilt_steps is None:
            parser.error("ARM需要显式给定两轴命令状态seed；不是自动物理归零")
        if not all(-9102 <= value <= 9102 for value in (args.seed_pan_steps, args.seed_tilt_steps)):
            parser.error("seed范围为[-9102,9102]步")
        if args.physical_uart:
            import json
            if args.interface_profile_file is None:
                parser.error("物理ARM需要已保存的3.3V TTL确认记录")
            record = json.loads(args.interface_profile_file.read_text(encoding="utf8"))
            if (record.get("confirmed_3v3_ttl") is not True or record.get("profile") != TTL33_PROFILE
                    or not record.get("controller_model") or not record.get("wiring_evidence")):
                parser.error("接口记录必须含3.3V TTL确认、profile、控制器型号与接线证据")
            profile = TTL33_PROFILE

    print("启动项目：硬件交互（PL 眨眼/嘴部状态）+ ZED 2i" +
          ("（云台未连接）" if args.no_gimbal else " + FPGA UART 云台"))
    print("C：眼控校准；Z：进入环境目标模式；ESC：退出环境模式；Q：退出程序。")
    with ExitStack() as cleanups:
        mapper = LegacyFpgaMapper(args.board_host, args.gaze_port,
                                  result_max_age_s=None)
        cleanups.callback(mapper.close)
        mapper.start()
        client = TCPClient(args.board_host, args.interaction_port)
        cleanups.callback(client.close)
        client.connect()
        interaction = InteractionAdapter(client)
        mouse = FpgaInteractionMouse(interaction)
        tracker = FpgaInteractionTracker(mapper, mouse.get_screen_size(), interaction)
        cleanups.callback(tracker.face_mesh.close)
        if args.no_gimbal:
            system = FpgaEyeControlSystem(mapper, tracker, mouse, args.zed_python, args.stereo_port)
        else:
            from gimbal_uart_stage8.tcp import TCPClient as GimbalTCPClient
            gimbal_client = GimbalTCPClient(args.board_host, args.gimbal_fpga_port)
            cleanups.callback(gimbal_client.close)
            gimbal_client.connect()
            system = GimbalFpgaEyeControlSystem(
                mapper, tracker, mouse, args.zed_python, args.stereo_port,
                laser_mount_config=args.laser_mount_config,
                laser_mount_setup=args.laser_mount_setup, gimbal_planner=gimbal_client)
            cleanups.callback(system.close)
            system.gimbal.speed_rpm = args.gimbal_speed
            if args.arm_gimbal:
                system.gimbal.initialize_known_state(args.seed_pan_steps, args.seed_tilt_steps,
                                                      internal_test=not args.physical_uart, confirmed_profile=profile)
        if args.no_gimbal:
            cleanups.callback(system.close)
        system.run()


if __name__ == "__main__":
    main()
