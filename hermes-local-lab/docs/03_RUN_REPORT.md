# 03_RUN_REPORT

## 执行时间

- 开始：2026-05-29 17:30 CST
- 报告生成：2026-05-29 19:06 CST
- 迁移复核：2026-05-29 19:27 CST

## 本机环境摘要

- OS: macOS 26.5, build 25F71
- CPU: arm64
- Shell: `/bin/zsh`
- git: 2.52.0
- Python: system `python3` 3.13.6；Agent venv 使用 Python 3.11.15
- uv: 0.11.3
- Node.js: v25.6.0
- Docker: CLI 可用，本阶段未使用

## 工作目录

- 目标目录：`/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab`
- 创建状态：成功
- 迁移状态：已按要求从 `/Users/bwb/Projects/hermes-local-lab` 转移到当前项目目录
- 子目录：`sources/`, `scripts/`, `logs/`, `docs/`, `tmp/`, `hermes-home/`, `workspace/`

## 关键执行命令

```bash
mkdir -p /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/{sources,scripts,logs,docs,tmp}
git clone --branch v2026.5.29 https://github.com/NousResearch/hermes-agent.git /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-agent
git clone --branch v0.51.157 https://github.com/nesquena/hermes-webui.git /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui

cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-agent
uv venv venv --python 3.11
UV_NO_CONFIG=1 UV_PROJECT_ENVIRONMENT="$PWD/venv" uv sync --extra all --locked
venv/bin/hermes --help
venv/bin/hermes --version

uv pip install --python /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-agent/venv/bin/python \
  -r /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/requirements.txt

/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/scripts/start-agent.sh
/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/scripts/start-webui.sh
/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/scripts/health-check.sh
```

迁移到当前项目目录后的修复命令：

```bash
/Users/bwb/Projects/hermes-local-lab/scripts/stop-all.sh
mv /Users/bwb/Projects/hermes-local-lab /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab

cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-agent
rm -rf venv
uv venv venv --python 3.11
UV_NO_CONFIG=1 UV_PROJECT_ENVIRONMENT=/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-agent/venv uv sync --extra all --locked
uv pip install --python /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-agent/venv/bin/python \
  -r /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/requirements.txt
```

## Hermes Agent

| 项目 | 结果 |
| --- | --- |
| 源码 clone | 成功 |
| 仓库 | https://github.com/NousResearch/hermes-agent |
| 选择版本 | Hermes Agent v0.15.1 |
| tag | v2026.5.29 |
| commit | e71a2bd11b733f3be7cf99deafde0066c343d462 |
| 工作区 | `git status --short` 干净 |
| 依赖安装 | 成功，`uv sync --extra all --locked` |
| CLI help/version | 成功 |
| 启动形态 | `hermes gateway run` + API Server |
| 服务地址 | http://127.0.0.1:18642 |
| health | `GET /health` 返回 HTTP 200 |
| capabilities | `GET /v1/capabilities` 返回 HTTP 200 |
| wrapper 日志 | `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/logs/hermes-agent.log` |
| runtime 日志 | `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/hermes-home/logs/gateway.log` |

## Hermes WebUI

| 项目 | 结果 |
| --- | --- |
| 源码 clone | 成功 |
| 仓库 | https://github.com/nesquena/hermes-webui |
| 选择版本 | v0.51.157 |
| tag | v0.51.157 |
| commit | cf003ae98699263aef05a99291daf10aee717809 |
| 工作区 | `git status --short` 干净 |
| 项目类型 | Python server + vanilla JS；不是 Node/Vite 项目 |
| 依赖安装 | 成功，requirements 已确认安装在 Agent venv |
| 启动方式 | 使用 Agent venv 的 Python 运行 WebUI `server.py` |
| 访问地址 | http://127.0.0.1:18787 |
| health | `GET /health` 返回 HTTP 200 |
| 首页访问 | HTTP 200，HTML 包含 Hermes title/app 内容 |
| Agent 连接 | `/api/health/agent` 返回 `alive: true`，`gateway_chat.enabled: true` |
| 日志 | `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/logs/hermes-webui.log` |

## 连通性测试

| 测试项 | 结果 |
| --- | --- |
| Agent source exists | 通过 |
| Agent commit locked | 通过 |
| Agent `hermes --help` | 通过 |
| Agent `hermes --version` | 通过 |
| Agent 服务进程 | 通过，PID 32288 |
| Agent 端口监听 | 通过，127.0.0.1:18642 |
| Agent health/status/version 类接口 | 通过，`/health` 与 `/v1/capabilities` |
| WebUI source exists | 通过 |
| WebUI commit locked | 通过 |
| WebUI 服务进程 | 通过，PID 32380 |
| WebUI 页面访问 | 通过，HTTP 200 |
| WebUI HTML 关键内容 | 通过，包含 Hermes |
| 本地浏览器访问 | 通过，Codex in-app Browser 打开 `http://127.0.0.1:18787/`，页面 title 为 `Hermes` |
| WebUI 连接 Agent | 通过，`/api/health/agent` 显示 gateway_chat 已启用且 Agent alive |
| 真实模型对话测试 | 未执行 |

真实模型对话测试未执行原因：

- 本阶段使用隔离 `HERMES_HOME=/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/hermes-home`。
- 未在 lab `.env` 中配置真实模型供应商 API Key。
- 因此记录为：服务已启动，WebUI 可访问，但未配置模型 API Key，未进行真实模型推理测试。

## 已解决的问题

- 默认 Agent API Server 端口 `8642` 被既有 taiji-agent gateway 占用，已改用 `18642`。
- `nohup ... &` 后进程被执行环境清理，已改为 Python `subprocess.Popen(..., start_new_session=True)`。
- WebUI requirements 首次 uv 命令形式不正确，已改用 `uv pip install --python <agent-venv-python> -r requirements.txt`。
- 迁移目录后，Agent venv 的 `bin/hermes` shebang 仍指向旧路径，已在新目录重建 venv 并重新安装依赖。

## 未解决的问题

- 未配置真实模型供应商 API Key，因此无法完成真实 LLM 推理测试。

## 下一步建议

1. 如需真实对话，在 `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/.env` 配置模型供应商 key 和 provider/model。
2. 运行 `RUN_MODEL_TEST=1 /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/scripts/health-check.sh` 做最小真实对话测试。
3. 本阶段完成前不要进行 UI 改造、品牌替换、桌面壳封装或核心逻辑改动。

## 本阶段明确未做事项

- 未做 UI 改造
- 未做品牌替换
- 未做桌面壳封装
- 未做 Electron
- 未做 Tauri
- 未改 Hermes Agent 核心代码
- 未改 Hermes WebUI 功能逻辑
