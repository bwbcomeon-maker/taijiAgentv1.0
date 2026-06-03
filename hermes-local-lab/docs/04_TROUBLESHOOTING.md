# 04_TROUBLESHOOTING

## 已遇到并解决的问题

### 1. `uv pip install` 首次指定环境方式不正确

现象：

```text
error: No virtual environment found; run `uv venv` to create an environment, or pass `--system`
```

原因：

- 初次对 WebUI requirements 执行 `uv pip install` 时使用了不适合该命令的 `UV_PROJECT_ENVIRONMENT=...` 形式。

处理：

```bash
uv pip install --python /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-agent/venv/bin/python \
  -r /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/requirements.txt
```

结果：

- 成功确认 WebUI 的 `pyyaml`、`cryptography` 依赖已存在于本次 Agent venv 中。

### 2. 默认 Agent API Server 端口 `8642` 已被占用

现象：

```text
127.0.0.1:8642 (LISTEN)
/Users/bwb/Documents/工作/taiji-agent/runtime/home/taiji-agent/hermes-home/hermes-agent/venv/bin/python ... hermes gateway
```

原因：

- 本机已有另一个 taiji-agent runtime gateway 正在监听默认 API Server 端口。

处理：

- 本次 lab 不停止、不误杀该进程。
- 改用独立端口 `18642` 运行 Hermes Agent API Server。

### 3. `nohup ... &` 启动后进程被当前执行环境清理

现象：

- `start-agent.sh` 和 `start-webui.sh` 初次显示 health ready，但随后 pid 消失，健康检查失败。

原因：

- 在当前 Codex 执行环境里，普通 `nohup ... &` 没有充分脱离 shell 进程组。

处理：

- 将启动脚本改为通过 Python `subprocess.Popen(..., start_new_session=True)` 启动服务。

结果：

- Agent 与 WebUI 进程父进程变为 `1`，并持续监听：
  - Agent API Server: `127.0.0.1:18642`
  - WebUI: `127.0.0.1:18787`

## 当前未解决/限制

### 真实模型推理测试未执行

原因：

- 本次 lab 使用独立 `HERMES_HOME=/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/hermes-home`，未导入用户已有真实模型供应商 API Key。
- `.env.example` 只列出变量名，不写入真实 key。

下一步：

- 如需真实对话测试，在 `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/.env` 中配置模型供应商 key 和 Hermes provider/model，再运行：

```bash
RUN_MODEL_TEST=1 /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/scripts/health-check.sh
```

## 日志位置

| 服务 | 日志 |
| --- | --- |
| Agent wrapper stdout/stderr | `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/logs/hermes-agent.log` |
| Agent gateway runtime log | `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/hermes-home/logs/gateway.log` |
| Agent errors log | `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/hermes-home/logs/errors.log` |
| WebUI stdout/stderr | `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/logs/hermes-webui.log` |
