# 上板与运行

完整环境配置和操作顺序见根目录 `README.md`。

- `artifacts/`：配套位流与HWH。
- `run_fresh_board.py`：全新板卡部署、生命周期心跳、自检及PC启动入口，默认使用不连接云台的模式。
- `requirements-main.txt`：眼控Python环境依赖。
- `environment/`：眼控与ZED环境版本快照。
- `measurements/`：留作独立角度与落点测量文件目录。

串口接线：PMODB物理1脚TX接控制器RX，物理2脚RX接控制器TX，物理5或11脚GND共地；3.3V TTL，约115200 baud，8N1。控制器电源按其配套要求连接。
