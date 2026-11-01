# 构建与实现

工具：Vivado 2024.2；器件：xc7z020clg400-1；设计时钟：100 MHz。

```powershell
& $MainPython .\build\rebuild.py --destination 'C:\fpga_build_run_01' --vivado 'E:\vivado\Vivado\2024.2\bin\vivado.bat'
```

输出目录使用新的纯英文路径。脚本从 `src/hardware_interaction_project/hardware_interaction/build_source/` 重建IP集成设计并执行综合、实现及位流输出。

- `reports/synthesis/`：综合资源报告。
- `reports/implementation/`：时序、DRC、资源报告和块设计Tcl。
- `checkpoints/`：综合与布线设计检查点。
- `original_run/`：原始构建Tcl与工具日志。

构建输出包含bit、hwh、dcp和报告。部署发布文件以配套bit/hwh及 `release.json` 中的SHA-256对应。
