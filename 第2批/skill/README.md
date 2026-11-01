# 通用 FPGA 项目设计 Skill

本目录使用最后生成的 `fpga-design` 版本，包含技能正文、四份阶段参考文件和智能体显示配置。

- `fpga-design/SKILL.md`：技能入口。
- `fpga-design/references/`：架构、RTL验证、时序约束和上板调试指导。
- `fpga-design/agents/openai.yaml`：显示名称及默认调用提示词。
- `fpga-design.zip`：同版本安装包。

安装时将 `fpga-design` 文件夹复制到本机 Codex 技能目录，例如 `%USERPROFILE%\.codex\skills\fpga-design`，重新加载技能后使用。

调用示例：使用 $fpga-design，结合给定板卡和接口条件完成模块架构、RTL实现及仿真验证。
