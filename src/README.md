# 设计源码

`hardware_interaction_project` 保持原工程内部相对目录，供运行入口和构建脚本直接引用。

| 内容 | 位置 |
| --- | --- |
| 当前交互RTL | `hardware_interaction_project/hardware_interaction/rtl/` |
| 完整IP源码与约束 | `hardware_interaction_project/hardware_interaction/build_source/` |
| PC应用与ZED接口 | `hardware_interaction_project/gimbal_uart_stage8/runtime/` |
| PS服务与驱动 | 各stage的 `software/` 以及 `hardware_interaction/board_manager.py` |
| 项目启动入口 | `hardware_interaction_project/launch_hardware_interaction.py` |

HLS目录保持为空；本项目硬件实现采用RTL。
