# Hermes Local Lab

## 目标

在本目录以源码方式运行 Hermes Agent 和 Hermes WebUI，提供本地 WebUI、Agent API、写作工作流和模型配置验证环境。

## 目录结构

```text
hermes-local-lab/
  sources/hermes-agent/
  sources/hermes-webui/
  custom-skills/
  vendor/
  scripts/
  docs/
  hermes-home/   # 本地运行状态，Git 忽略
  workspace/     # 本地生成内容，Git 忽略
  logs/          # 本地日志，Git 忽略
  tmp/           # 本地临时文件，Git 忽略
  .env.example
  README_LOCAL_RUN.md
```

## 当前源码快照

| 项目 | 上游仓库 | 当前本地提交 |
| --- | --- | --- |
| Hermes Agent | https://github.com/NousResearch/hermes-agent | `2a11a8c69 feat: support lab workspace runtime resolution` |
| Hermes WebUI | https://github.com/nesquena/hermes-webui | `a5b835c1 feat: add writing workflow and model config` |

## 环境依赖

- macOS 或 Linux
- Git
- `uv`
- Python 3.11

## 首次安装

```bash
cd hermes-local-lab
./scripts/setup-local.sh
```

该脚本会在 `sources/hermes-agent/venv` 创建本地虚拟环境，并安装 Agent 与 WebUI 依赖。`venv` 不会提交到 GitHub。

## 启动

```bash
cd hermes-local-lab
./scripts/start-agent.sh
./scripts/start-webui.sh
```

默认访问地址：

- Hermes Agent API Server: http://127.0.0.1:18642
- Hermes WebUI: http://127.0.0.1:18787
- Agent health: http://127.0.0.1:18642/health
- WebUI health: http://127.0.0.1:18787/health

## 健康检查

```bash
cd hermes-local-lab
./scripts/health-check.sh
```

健康检查会验证源码目录、命令入口、进程、端口、HTTP health endpoint、WebUI 首页和网关诊断。真实模型推理测试只有在配置模型 API Key 并设置 `RUN_MODEL_TEST=1` 后才会执行。

## 配置

如需覆盖端口、工作目录或模型供应商 key：

```bash
cd hermes-local-lab
cp .env.example .env
```

然后编辑 `.env`。`.env` 包含本机密钥和路径，已被 Git 忽略，不能提交。

脚本默认会按仓库当前位置生成：

- `hermes-home/`
- `workspace/`
- `logs/`
- `tmp/`

这些目录都是运行产物，不会上传到 GitHub。

## 停止

```bash
cd hermes-local-lab
./scripts/stop-all.sh
```

## 常见问题

- 如果端口占用，复制 `.env.example` 为 `.env` 后修改 `AGENT_API_PORT`、`API_SERVER_PORT`、`WEBUI_PORT` 和 `HERMES_WEBUI_PORT`。
- 如果 WebUI 能打开但不能真实聊天，先确认 `.env` 是否配置了模型供应商 key，并确认 Agent API health 正常。
- 如果重新 clone 到其他设备，需要重新运行 `./scripts/setup-local.sh`，不要复制旧机器的 `venv`。
