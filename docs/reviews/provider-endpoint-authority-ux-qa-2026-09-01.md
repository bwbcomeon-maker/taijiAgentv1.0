# 前端 UX QA 报告：Provider 请求地址权威化（Task 9）

## 当前状态

Task9：**Approved（Sol 已终审）**。最终隔离浏览器 smoke 已取得三视口证据，并由原始执行回执确认 `exit_code=0`。Task10 正在执行 Phase B 门禁，尚未提交或推送。证据仅覆盖本地源码、隔离 WebUI 与 route mock，不代表真实 Provider、OAuth、Key、安装态或麒麟验收。

## 范围与身份

- 集成测试：`hermes-local-lab/sources/hermes-webui/tests/test_provider_endpoint_authority.py`
- 浏览器 smoke：`hermes-local-lab/sources/hermes-webui/tests/provider_endpoint_authority_browser_smoke.py`
- 本报告；Tasks 1–8 业务代码与已批准测试冻结。
- 浏览器脚本 SHA256：`616019fd499ebf4d2d9451d032825be163a12096fab3fd9d7765618772a1c531`
- HEAD：`1a8a24f3eaf3be95c8b32f101f2b55feb9b55016`
- Task8 批准指纹未变化：`panels.js 52d1d2aaef1a3772fb84407070eaae0a268d0cb8725d575337b301567e76bf20`；`test_model_config_frontend.py c926b4c6cbb4fb89c8ec9d1a566bd7109ad2d895ff9f5091f0a16afad7c66ea7`。

## 功能契约

| 能力 | 后端字段/API | 可见 UI 入口 | 状态/错误 | 键盘/ARIA | 验证证据 | 结果 |
|---|---|---|---|---|---|---|
| `zai-cn` 实际请求地址 | chat material、`/api/model-config.main.endpoint` | 主模型摘要 | BigModel 完整地址、旧候选不显示 | 摘要可读 | 集成 + 三视口截图 | 已验证 |
| Provider 地址投影 | `/api/providers[].endpoint` | Provider 卡展开 | `zai-cn`/DeepSeek/OAuth/runtime/missing 状态 | 原生按钮、`aria-expanded/controls` | 三视口 Provider 截图 | 已验证 |
| generic/named Custom 编辑 | `/api/model-config/main` | 主模型编辑器 | 成功、pending、uncertain、非法 URL 错误与草稿保留 | 字段 label、错误聚焦 | 三视口截图与脚本断言 | 已验证 |
| 键盘展开 | Provider 卡按钮 | 卡片标题按钮 | 展开状态可见 | click/Enter/Space | smoke 末尾断言 | 已验证 |
| 横向布局 | DOM viewport 尺寸 | 三视口页面 | 无明显横向溢出 | — | 1280×900、768×900、390×844 | 已验证 |

## 自动化命令与回执

1. 集成测试：`../hermes-agent/venv/bin/python -m pytest -p no:cacheprovider tests/test_provider_endpoint_authority.py -q`；结果 `1 passed, 1 warning in 1.52s`；raw：`/private/tmp/taiji-provider-endpoint-authority/task9-resume-integration-r2.log`。
2. 浏览器 smoke：`for key in ${(k)parameters[(I)*_API_KEY]}; do unset "$key"; done; for key in ${(k)parameters[(I)*OAUTH*]}; do unset "$key"; done; umask 022; exec /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 hermes-local-lab/sources/hermes-webui/tests/provider_endpoint_authority_browser_smoke.py > /private/tmp/taiji-provider-endpoint-authority/task9-unblocked-browser-final-r2.log 2>&1`；系统 Python 3.13、隔离 HOME/HERMES 配置、headless Chromium、本机 API 拦截；raw 内容 66 bytes，仅为 evidence 目录指针；实际退出回执来自原始执行记录 `/Users/bwb/.codex/sessions/2026/09/02/rollout-2026-09-02T17-50-44-01a06187-2d93-7723-9f4c-9baadf6b72ac.jsonl`：启动 `call_Cl9vhWsWGpMlAPJCpx0zMM8t`、session `71294`，收尾 `call_AtgPHXAuPaNAVpz0LoEL76Bx`、`chunk_id=96199b`、`exit_code=0`。

## 三视口证据

目录：`/private/tmp/task9-browser-evidence-w649jo7b`。每个 viewport 均有 `main-zai`、`providers-endpoints`、`invalid-custom`、`endpoint-authority` PNG、`network-events-*.json` 与 `server.log`。截图显示 BigModel/系统来源/已解析、DeepSeek 官方地址和 390px 非法 Custom 的 `not-a-url` 保留/聚焦/红色错误。

网络 JSON 均 `page_errors=[]`；同时记录了预期精确 `POST /api/model-config/main` 的 `net::ERR_FAILED` 与 400，因此不能表述为 console 全零。没有外部阻断 URL 证据异常。

## 历史失败摘要

| 记录 | 原因 | 当前结论 |
|---|---|---|
| task9-resume-browser-r3/r4 | 设置入口 locator/可见性问题 | 已由正式入口修正，raw 保留；区别于后续 `task9-unblocked-browser-r3/r4` |
| r5/r6 | fixture notice、Provider selector 严格匹配 | 已修正，raw 保留 |
| r7/r8 | 768/390 移动入口与屏幕外 locator | 已由移动侧栏路径修正，raw 保留 |
| final 首 run | 沙箱 socket；随后侧栏遮挡按钮 | C 类/脚本路径问题，raw 保留，未归因产品缺陷 |

## 问题与风险

- P0：无确定问题。
- P1：无确定产品问题；历史 C/D 类记录不作为当前产品 P1。
- P2 候选：Provider 来源/状态可考虑更显式标签；本轮不改冻结业务代码。
- 未验证：axe 自动化、独立视觉回归工具、长时工作体验、真实 Provider/OAuth/Key、默认浏览器、真实后端写盘、安装态、Kylin、部署及 Task10。

## 结论

Task9 浏览器 smoke 的三视口、主流程、Provider 展开、Custom 保存/错误状态、Enter/Space、ARIA 和横向布局已有本地隔离证据，且执行回执为 `exit_code=0`。本报告不将 route mock 或截图跨层推导为真实 Provider、安装态或发布结论；Task9 已由 Sol 批准；Task10 阶段门禁结果另见临时账本。
