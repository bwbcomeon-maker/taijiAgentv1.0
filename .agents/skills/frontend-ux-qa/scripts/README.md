# run-frontend-qa.sh

这个脚本只是前端 QA 的保守辅助工具，不能替代真实浏览器 QA、截图审查、可访问性检查和人工 UX 判断。

Codex 应优先使用项目原生命令和项目文档中规定的验证方式。只有在需要快速枚举并运行已有常见前端 scripts 时，才使用本脚本。

## 如何运行

```bash
.agents/skills/frontend-ux-qa/scripts/run-frontend-qa.sh
```

也可以指定包含 `package.json` 的目录：

```bash
.agents/skills/frontend-ux-qa/scripts/run-frontend-qa.sh hermes-local-lab/sources/hermes-webui
```

## 输出含义

- “跳过”：脚本没有运行该检查。
- “未配置”：`package.json` 中不存在对应 script。
- “未验证”：环境、工具或配置不足，无法执行检查。
- “失败”：已有检查脚本执行后返回非零状态。

如果存在的检查脚本运行失败，本脚本会以非零状态退出。

## 如何扩展

只在项目已经有稳定脚本时扩展检查项。不要让脚本自动安装依赖、修改 `package.json`、修改 lockfile、改业务文件或生成新的视觉基线。

## 为什么不会自动安装依赖

自动安装会改变工作区、lockfile 或依赖解析结果，可能污染前端 QA 证据。本脚本只报告当前环境能验证什么，不能把“安装后可能能跑”写成“已通过”。

## 为什么不能替代 UX QA

脚本只能运行已有自动化命令。它无法判断功能是否有可发现 UI 入口、信息层级是否清楚、页面是否适合长时间工作、截图是否达标、键盘路径是否合理，也不能替代真实浏览器交互。
