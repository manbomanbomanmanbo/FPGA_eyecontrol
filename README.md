> **重要：本项目使用的 FPGA 开发工具版本为 AMD Vivado 2024.2。请使用 Vivado 2024.2 执行本说明中的工程构建与复现步骤。**



# 基于AMD的双模态视觉交互系统



本项目结合眼部与嘴部视觉特征实现屏幕交互，并通过ZED双目选点和FPGA云台控制完成环境目标指向。PC负责视觉识别、校准、ZED采集与双目处理以及Windows输入；PS负责通信和MMIO调度；PL负责映射、交互状态、选定像素XYZ、云台计算与UART输出。



## 目录



```text

全部代码包/

├── README.md

├── src/       设计源码、RTL、PC及PS软件

├── sim/       仿真目录

├── build/     构建入口、综合实现报告和检查点

├── board/     位流、运行入口和环境依赖

├── data/      数据目录

├── skill/     最后一版fpga-design技能包

└── report/    技术文档

```



`src/hls/` 和 `board/measurements/` 保持为空，分别预留HLS源码和独立角度/落点测量文件。源码和工具报告按原文件内容保存。



## 1. 准备设备与软件



- Windows电脑、PYNQ-Z2、microSD卡、板卡配套电源、读卡器及网线。

- 普通USB摄像头、ZED 2i及USB3连接。

- 云台和兼容项目协议的串口控制器。本套配置对应WL-STEPDRI2-V1配套驱动板，命令模型为6400步/转、32细分。

- 64位Python 3.12、匹配本机GPU与驱动的ZED SDK。随包环境快照对应ZED SDK 5.5.0、Python API 5.5。

- 重新构建RTL时安装Vivado 2024.2及xc7z020器件支持；运行随包位流可直接进入部署步骤。



解压到例如 `C:/fpga_eye/全部代码包`。下列PowerShell命令在同一窗口执行：



```powershell

$Package = 'C:/fpga_eye/全部代码包'

$Project = "$Package/src/hardware_interaction_project"

Set-Location -LiteralPath $Package

```



## 2. 安装板端系统和网络



1. 从PYNQ官方取得适配PYNQ-Z2的镜像，使用镜像写入工具写入microSD卡并完成校验。保存镜像名称、版本和文件哈希。

2. 断电插卡，按板卡说明设置SD启动，连接电源启动。

3. 用镜像提供的初始网络方式或串口登录，在板端运行 `ip -br addr` 查看网卡。本文统一使用板卡IP `192.168.2.99/24`。

4. 如需临时添加该地址，确认有线网卡名称后运行以下命令；已存在该地址时直接继续：



```bash

sudo ip link set eth0 up

sudo ip addr add 192.168.2.99/24 dev eth0

/usr/bin/python3 -c "import pynq; from pynq import Overlay,MMIO; print(pynq.__version__)"

command -v systemd-run

command -v ss

```



`eth0`按实际网卡名填写；临时网络配置在重启后重新检查。选择能由 `/usr/bin/python3` 导入PYNQ的镜像运行本套板端服务。



5. 将PC有线网卡设为 `192.168.2.10`、子网掩码 `255.255.255.0`，网线连接板卡。直连场景的该网卡可不填网关和DNS。

6. 在PC执行：



```powershell

Test-NetConnection 192.168.2.99 -Port 22

ssh xilinx@192.168.2.99

```



核对首次连接的主机密钥指纹，再接受并登录，建立 `known_hosts`。在板端使用 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` 查看对应指纹。登录后运行 `sudo -v` 确认sudo权限，再执行 `exit` 返回PC。



账号与密码按实际镜像配置。在PC设置SSH连接凭据：



```powershell

$env:HARDWARE_INTERACTION_SSH_USER = 'xilinx'

$sshCredential = Get-Credential -UserName $env:HARDWARE_INTERACTION_SSH_USER -Message '输入板端SSH密码'

$env:HARDWARE_INTERACTION_SSH_PASSWORD = $sshCredential.GetNetworkCredential().Password

```



项目板端目录为 `/home/xilinx/jupyter_notebooks/hardware_interaction_20261026`。使用其他账号时，先由管理员创建该目录并设置相应访问权限。



## 3. 创建眼控Python环境



```powershell

py -3.12 -m venv "$Project\.venv"

$MainPython = "$Project\.venv\Scripts\python.exe"

& $MainPython -m pip install --upgrade pip

& $MainPython -m pip install -r "$Package\board\requirements-main.txt"

& $MainPython -c "import cv2,numpy,mediapipe,pyautogui,keyboard,paramiko,serial,tkinter; import mediapipe as mp; assert hasattr(mp,'solutions'); print('MAIN_ENV_PASS')"

```



该环境使用NumPy 1.26.4、MediaPipe 0.10.21和OpenCV contrib 4.11.0.86，和ZED环境分开。环境完整版本清单位于 `board/environment/`。



## 4. 配置ZED环境



1. 安装ZED SDK及其匹配的GPU驱动/CUDA组件，接入ZED 2i。

2. 使用ZED Explorer检查双目画面，使用ZED Depth Viewer检查深度，完成SDK要求的模型初始化。

3. 创建独立环境，并用所安装SDK的Python API安装脚本安装对应版本：



```powershell

py -3.12 -m venv 'C:\fpga_eye\zed_env'

$ZedPython = 'C:\fpga_eye\zed_env\Scripts\python.exe'

& $ZedPython -m pip install --upgrade pip

$ZedApiInstaller = Read-Host '填写所安装ZED SDK的get_python_api.py完整路径'

& $ZedPython $ZedApiInstaller

& $ZedPython -m pip install opencv-python PyAutoGUI

& $ZedPython -c "import cv2,numpy,pyautogui,pyzed.sl as sl; print('ZED SDK',sl.Camera.get_sdk_version())"

```



Python API安装按对应SDK提示完成。保持ZED解释器与SDK版本匹配。Windows隐私设置允许桌面程序访问摄像头，关闭占用相机的其他程序。



## 5. 本地入口检查



连接面向使用者的普通摄像头，确认当前程序使用的摄像头编号0对应该设备。



```powershell

& $MainPython -c "import cv2; c=cv2.VideoCapture(0); ok,f=c.read(); print('CAMERA_0',ok); c.release()"

& $MainPython -B "$Project/launch_hardware_interaction.py" --check

& $MainPython -B "$Package/board/run_fresh_board.py" --project $Project --zed-python $ZedPython --check

```



确认环境导入成功、摄像头可以读取，并查看程序入口参数。



## 6. 全新板卡部署及PL自检



确认板端没有其他眼控版本占用8765—8770端口，然后执行：



```powershell

& $MainPython -B "$Package\board\run_fresh_board.py" --project $Project --zed-python $ZedPython --validate-board

```



脚本核对发布清单，上传独立目录，启动 `hardware-interaction-fresh.service`，加载配套位流，保持心跳，并执行7次眼部和4次嘴部合成观测检查。自检模式不打开相机、不发送Windows输入、不发送电机动作。完成后停止本次临时服务。



返回 `BOARD_SELFTEST_PASS` 后进入下一步。板端日志查看方式：



```powershell

ssh xilinx@192.168.2.99 'sudo journalctl -u hardware-interaction-fresh.service -n 80 --no-pager'

```



已经部署原Stage8系统的板卡，使用原项目入口进行服务切换：



```powershell

& $MainPython -B "$Project\launch_hardware_interaction.py" --validate-board

& $MainPython -B "$Project\launch_hardware_interaction.py" --zed-python $ZedPython --no-gimbal

```



该入口要求原 `zed2i-fpga.service` 及Stage8位流已配置。两种部署路径按设备当前配置选择，同一块板卡只运行一个版本。



## 7. 复现屏幕交互



```powershell

& $MainPython -B "$Package\board\run_fresh_board.py" --project $Project --zed-python $ZedPython

```



此命令默认运行眼控与ZED流程，不连接云台控制会话。



1. 按窗口提示设置环境零点。屏幕交互阶段记录所填参数；使用实际云台时填写实测安装值。

2. 保持头部位置稳定，使普通摄像头清楚看到眼睛和嘴部。

3. 将输入焦点放到眼控预览窗口，按 `C` 进行九点校准，依次注视目标中心，等待采样完成。

4. 在测试窗口检查视线移动、一次眨眼单击和连续两次眨眼双击。单击等待配对窗口结束后输出。

5. 校准后检查嘴部方向选择、张嘴滚动和合嘴停止。`M`同时切换移动与滚动总开关。

6. 使用姿态变化后按 `C`重新校准，分别记录动作成功、漏检及误触发次数。



## 8. 复现三维选点



按 `Z`进入环境模式，将视线光标移到ZED画面内的静止目标，通过项目眨眼确认选点。查看返回的XYZ与距离，保存对应像素和状态。选择有纹理且深度有效的目标位置。界面环境零点和目标XYZ使用毫米，ZED内部和部分计算接口使用米，参数按界面和接口定义填写。



`ESC`返回普通眼控；`Q`退出。等待脚本结束板端服务后再断电。



## 9. 接线并启用实际云台



退出程序并断电接线，核对板卡连接器方向和控制器说明。



| PYNQ-Z2 | 控制器 |

| --- | --- |

| PMODB物理1脚TX，W14 | RX |

| PMODB物理2脚RX，Y14 | TX |

| PMODB物理5或11脚GND | GND |



接口为3.3V TTL UART，约115200 baud、8N1；控制器电源按其要求独立连接。PMOD与控制器共地，控制器接口+5V不接入PMOD。核对控制器指令协议、方向和6400步/转配置。



1. 固定相机和云台，按项目轴对齐安装方式建立已知机械初始位置。

2. 复制 `src/hardware_interaction_project/gimbal_uart_stage8/runtime/physical_launch/interface_profile.json` 为本机接口记录，填写本机控制器与接线依据。

3. 将下例 `$Profile` 设置为该记录路径。仅当两轴当前位置对应命令零位时使用0、0；seed是当前位置声明，机械零位按实物建立。



```powershell

$Profile = 'C:\fpga_eye\my_interface_profile.json'

& $MainPython -B "$Package\board\run_fresh_board.py" --project $Project --zed-python $ZedPython --physical-gimbal --profile $Profile --seed-pan-steps 0 --seed-tilt-steps 0

```



4. 环境零点填写两转轴交点相对相机原点的实测位置，单位mm，X右正、Y下正、Z前正。

5. 使用激光指向头时，在安装补偿窗口填写光孔相对转轴交点的实测偏移，单位mm，X右正、Y上正、Z前正。区分两个窗口的Y轴方向。

6. 完成校准并按 `Z`进入环境模式，从正前方附近固定目标开始选点，等一次动作完成后再选下一点。

7. 测量目标与实际落点偏差，并在不同距离、方位和正反运动方向重复操作，保存指令记录和实物测量值到 `board/measurements/`。



## 10. 退出与记录



快捷键：`C`校准、`M`移动/滚动开关、`Z`环境模式、`ESC`返回、`Q`退出。除全局ESC外，按键操作时让预览窗口获得焦点。



正常退出后停止本次临时板端服务，再关闭设备。若需手动清理本次冷启动会话，先确认PC程序退出，再运行：



```powershell

ssh xilinx@192.168.2.99 'sudo systemctl stop hardware-interaction-fresh.service'

```



记录使用的镜像、软件版本、位流哈希、安装尺寸、测试条件与功能结果。用 `pip freeze`导出两个Python环境；用 `Get-FileHash -Algorithm SHA256`核对位流。



## 11. RTL构建



```powershell

& $MainPython "$Package\build\rebuild.py" --destination 'C:\fpga_build_run_01' --vivado 'E:\vivado\Vivado\2024.2\bin\vivado.bat'

```



输出目录须为新的纯英文路径。构建生成bit、hwh、设计检查点和报告。新发布使用成对bit/hwh，并以相应SHA-256更新发布清单，按前述步骤进行自检和功能操作。



## 12. 内容安排



`report/技术文档.pdf`为项目技术文档。`src/hls/`保留为空目录，硬件源码为RTL；`board/measurements/`留作独立角度/落点测量文件目录。`sim/`、`data/`和 `board/results/`功能都通过。其余内容包含设计源码、构建脚本、工具报告、DCP、运行入口、位流及技能包。

