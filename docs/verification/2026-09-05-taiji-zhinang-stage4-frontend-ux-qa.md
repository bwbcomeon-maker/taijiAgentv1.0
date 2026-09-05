# 太极智囊阶段 4 前端 UX QA 报告

> 日期：2026-09-05
> 证据层：`main@5f387e89bba3f14d303bdfbd0269f86889614389` 之上的 P2 收口工作树、真实源码 WebUI、真实 `AIAgent`、loopback mock Provider 与隔离持久状态。本文不代表安装包、客户终端、真实模型或发布态验收。

## 结论

智囊库在现有太极外壳内的入口、目录、筛选、收藏、最近任务、完整详情、示例创建、角色标签、聊天与成果链已经可操作。阶段 4 首轮复审记录的宽屏详情卡片选中态和收藏重绘焦点两个 P2，已在后续有界任务中以浏览器 RED 固定并修正：详情卡片使用适合 `article` 的 `aria-current`，收藏异步重绘按用户当前焦点意图恢复。当前受影响范围没有已知 P0/P1/P2。

面向使用者的操作与边界说明见[太极智囊使用说明](../taiji-zhinang-user-guide.md)。

自动化全程使用独立 `HERMES_HOME`、`HERMES_BASE_HOME`、`TAIJI_RUNTIME_HOME`、`HERMES_WEBUI_STATE_DIR`、配置与工作区；剥离真实 Provider/OAuth 凭据，并同时启用 `HERMES_WEBUI_TEST_NETWORK_BLOCK=1` 和 `TAIJI_WEBUI_TEST_NETWORK_BLOCK=1`。浏览器只允许 `127.0.0.1`/`localhost`，未打开默认浏览器或访问真实 Provider。

## 受影响界面与交互

- 三个既有导航面均提供“智囊库”入口；目录标题为“太极智囊”，二级范围使用“全部角色”。
- 目录支持搜索、六类领域、精选/全部/收藏/最近正交筛选和 24 条分页；卡片有独立收藏、查看详情和经验证的继续入口。
- 详情显示 AI 角色身份、完整说明、限制、改编说明、来源与 MIT 许可；非 HTTP(S) 来源降为不可点击文本；示例明确标注“示例，非已生成文件”。
- “使用此智囊”创建空白角色任务；“使用此示例”只预填所选文本且不自动调用模型。已有最近任务时，新建与继续同时存在。
- 智囊任务显示可点击角色标签，空态、草稿态和消息态均可打开历史角色详情；固定角色任务禁用 `/personality`，普通聊天保留原入口。
- 收藏写入、详情加载、空态、重试、失败恢复、焦点圈定、Escape 关闭和关闭后焦点恢复均有交互状态；宽屏详情同步突出当前卡片，收藏重绘保持键盘焦点且不会抢回用户已主动移走的焦点。
- 真实 `write_file` 成功后显示成果、预览、下载并在刷新后恢复；失败或取消不会把既有同名文件误报为新成果。

## F01–F12 功能契约表

| 功能 | 产品依据、目标用户与 UI 入口 | API 与状态 | 反馈、键盘与边界 | 浏览器证据 / 状态 |
| --- | --- | --- | --- | --- |
| F01 入口与外壳 | PRD 4.1；所有现有用户；三处“智囊库”导航 | 复用现有 panel 状态 | 当前项含文字/图标选中，窄屏先关闭菜单 | `viewports`、`regression` / 通过 |
| F02 角色目录 | PRD 4.2；寻找专业方法的用户；太极智囊主区 | `GET /api/zhinang/catalog` | skeleton、错误重试、空态恢复；卡片可聚焦 | `flow`、`performance` / 通过 |
| F03 搜索筛选分页 | PRD 4.2；需要缩小角色范围的用户；搜索/领域/视图/分页 | catalog query：scope/category/view/query/page | 200 ms 防抖、旧响应丢弃、筛选回第一页、24 条当前页 | `faults`、`viewports`、`performance` / 通过 |
| F04 收藏 | PRD 4.3；回访常用角色的用户；卡片/详情收藏按钮 | `PUT /api/zhinang/favorites/{role_id}` | `aria-pressed`、pending `aria-disabled`；保存/刷新失败可重试且不假成功；重绘只在用户仍停留于原控件时恢复焦点 | `faults`、`selection-focus`、`lifecycle` / 通过 |
| F05 最近使用 | PRD 4.4；继续真实任务的用户；最近使用与卡片继续按钮 | catalog `view=recent` | 只认已受理、可访问 tip；无最近显示专用空态 | `flow`、`removed` / 通过 |
| F06 卡片与详情 | PRD 5.1–5.2；评估角色的用户；查看详情/整卡安全区域 | `GET /api/zhinang/roles/{role_id}` | drawer/dialog/aside；加载、失败、retry；关闭恢复滚动与焦点；宽屏 aside 以可见“当前查看”和 `aria-current` 同步对应卡片 | `flow`、`faults`、`selection-focus`、独立 UX / 通过 |
| F07 来源说明 | PRD 5.2；核对角色来源与限制的用户；详情来源/许可区 | 当前详情和 `session-role` 安全投影 | 完整文本可展开；仅 HTTP(S) 可链接；不公开有效 prompt | `flow`、A15 注入、独立 UX / 通过 |
| F08 示例任务 | PRD 5.3；需要起步文本的用户；“使用此示例” | draft save + create session | 仅预填所选示例、0 模型调用；失败留在原任务 | `flow`、`draft-idempotency` / 通过 |
| F09 使用智囊 | PRD 6.1；开始专业任务的用户；“使用此智囊” | `POST /api/sessions` 携 role/request id | 普通创建空白；pending 禁用；超时以同 request id 重试 | `flow`、`draft-idempotency` / 通过 |
| F10 固定角色 | PRD 6.2；持续对话用户；聊天顶部角色标签 | `GET /api/zhinang/session-role`，Provider 快照注入 | 标签三态可点；历史详情；固定任务禁用 personality | `flow`、`lifecycle`、独立 UX / 通过 |
| F11 聊天与成果 | PRD 7；提交附件和取回文件的用户；原聊天与成果 Tab | chat stream、windowed session、workspace/list/download | busy/stop、失败恢复；只显示带成功状态、非空工具 ID 与名称匹配的公开成果路径 | `flow`、`recovery`、Anthropic HTTP/Node 聚焦 / 通过 |
| F12 状态与可访问性 | PRD 8–9；键盘/窄屏及异常场景用户；全路径 | 各接口 generation/abort 防竞态 | Tab/Shift+Tab/Escape/详情焦点恢复、收藏 pending/成功/失败/刷新失败焦点、空收藏可见回退、五视口/边界/200%、外网零请求 | `faults`、`selection-focus`、`viewports`、`performance` / 通过 |

## 浏览器证据

可复现入口为 `hermes-local-lab/sources/hermes-webui/tests/zhinang_browser_e2e.cjs`。它要求显式传入 `PLAYWRIGHT_NODE_PATH` 与 `ZHINANG_E2E_CHROMIUM`，可选传入仓库 Agent venv 的 `ZHINANG_E2E_PYTHON` 和输出目录；脚本不会安装依赖、选择默认浏览器或访问非 loopback HTTP 源。每次只运行一个 `ZHINANG_E2E_SCOPE`。

| Scope | 结果 | 主要证明 | 证据 SHA-256 |
| --- | ---: | --- | --- |
| `flow` | 45 checks / 9 Provider requests | 目录、示例 0 调用、新建/继续、附件、真实工具写盘、预览/下载/刷新、失败不出假成果、assistant-only diff 刷新前后不出卡、历史详情；绑定 runner SHA-256 `e8ec954c925d7443d8d77621a625c9cbdbfdd0e8a429cb0c9d0236faedb02e1a` | `aa53751272dfe63aa7a0de2ad8f7bb410623d33c54d673a2d5be2befb8437107` |
| `faults` | 16 checks | 目录/详情失败与重试、收藏成功后刷新失败、收藏 PUT 失败、modal 加载焦点与键盘圈定；响应完成后控件状态与详情焦点恢复 | `beb0a2fc8d7b4d4316a45f151399584ef6d20e16715fa184579b9cf6b700bc10` |
| `selection-focus` | 17 checks | 2000×1000 宽屏 aside 初始/开/切/关/过滤选中态；收藏 pending、PUT 失败、刷新失败、成功、移焦到搜索或另一卡详情、最后收藏移除焦点；最终 runner SHA-256 `0a91f42543f95fe64c2ea3a7f0822ffbedfa81f2d804118da636bd2bb8e222a0` | `0077719b6d466d1ba4e55b291da629197fc7542ca03664b0a0995b218855578e` |
| `draft-idempotency` | 11 checks | 原草稿保存失败、原生 File 保持、双击并发、受理后 504 与同 request_id 重试 | `c4c584f874ad652ff6580a26e7f2795d1a850a719f01977c3c1d2968375d5dc1` |
| `lifecycle` | 19 checks / 4 Provider requests | 双窗口/profile 收藏、真实 WebUI 重启、同 SID 角色任务重启后继续进入 Provider | `74efcf70492c66f7d004b69f7be226e5d1e17a25ff2bb2ac2fe002ba4355a78d` |
| `recovery` | 12 checks / 7 Provider requests | 首 token 后取消、journal/socket 终止、模型 500、两次恢复、无重复用户消息和假成果 | `9b74c37315e07b92d96b52af6f0de4f736057d2a1613b52331730c862fac5faa` |
| `removed` | 10 checks | 下架收藏保留安全摘要、卡片保留经验证的历史继续入口、详情禁止新建、可取消并消失 | `13f5fd7797b1311ea5eee172f015beb7c78b2c15ffc6b6d9d623d9e14bd759dc` |
| `regression` | 12 checks / 2 Provider requests | 普通聊天、模型配置、普通 personality、写作/专家团真实启动与重载恢复 | `f1ef79d9092784de56b2fb50bbd7976e9304b96b8b80d825a01e20819e472da6` |
| `viewports` | 87 checks | 五个规定视口、900/901/902、1023/1024/1025 和 200% 缩放 | `b92ec6f55c994ba4b7da4a10f361d1c5621a5682e8d9c80423567f72620cc849` |
| `performance` | 67 checks | 500 条真实 HTTP 目录、24 条当前页、30 次 API 与浏览器连续查询、长内容 | `42f96f097b14140f44118b8a51723fcf54ce658b948c0d76671e520789a54c4b` |

除修正后 `flow` 外，阶段 4 JSON 和截图位于 `/Users/bwb/.codex/visualizations/2026/09/05/01a06f06-91f4-7823-a594-68ae33903fd7/stage4-e2e/`；最终 `flow` 位于同一证据根的 `stage4-review-fix-final2-e2e/`。本次 P2 收口的 `selection-focus`、更新后 `faults` 和选中态截图位于 `stage4-p2-fix/`，截图 SHA-256 为 `21b8b934aa3215c0d571d0c3a89ecdb6af44ef5572140c1b2ffe2a92a2b6d4c1`。主流程连接生产 `server.py` 与真实 `AIAgent`，Provider 只由 loopback fixture 替代；故障 scope 在浏览器路由层注入指定失败，不能替代后端契约。阶段 3 已用真实 HTTP 和持久化测试覆盖目录、收藏、最近与下架行为。

修正轮另以隔离 pytest 服务实际请求 `GET /api/session?messages=1&resolve_model=0&msg_limit=30`，验证 Anthropic `content[].tool_use` 在 session-level summaries 被窗口策略清空后仍携带同 ID/名称的成功公开成果路径；固定 Node 24 直接运行生产 `collectSessionArtifacts()`，验证该形状能生成成果卡，而 failed/cancelled/running 私有 args/result 和 assistant-only diff 均不能生成成果卡。三份受影响测试文件最终为 `87 passed`。

后续 P2 收口使用同一固定 Node 24、Playwright、Chromium、仓库 Agent Python 和隔离服务边界。`test_zhinang_ui.py` 为 `9 passed`；最终默认 `scripts/verify.sh` 为 `verification: PASS`，日志 `/private/tmp/taiji-zhinang-p2-final3-default-verify.log` 的 SHA-256 为 `a08f16c226d3a0acee95668ca97a4593012d0ef8879bf1a856d1f0c008170de4`。

`lifecycle` 的多 profile 检查使用隔离测试 wrapper，仅在该 fixture 内关闭产品 single-runtime guard；生产 `TAIJI_RUNTIME_HOME` 模式仍固定为 default，不把测试能力外推为档案切换 UI。最终报告绑定 SID `52ab2f9d1305`：重启前后完整 schema 2 snapshot SHA-256 均为 `eed29a2f226c4dfcd7413581dc531556ad452b2c6af5e08b5d4723ef615ad566`，`effective_prompt_sha256` 均为 `3e28b941fc912df29f9041ddcb73ea8fff223dbed25c5a7fd48e93035e788686`，两次实际 Provider 角色系统上下文 SHA-256 均为 `1b4ac0b11051f6b9f50bf88de3235a26a2f66193e858dda64880d800466661a3`。

性能测试在 macOS 26.6.2 arm64、Node v24.19.0、Python 3.11.15、Chromium 147.0.7727.15 上执行。wrapper 只替换 `api.zhinang.load_current_catalog_rows`，向生产 `server.py` 注入 500 条有效行；HTTP handler、查询与分页保持生产路径。30 次原始 HTTP p95 为 `16.888917 ms`，浏览器输入防抖 200 ms 后 30 次结果可交互 p95 为 `44.883083 ms`。报告保存全部样本与算法 `sort ascending; sample[Math.ceil(sample.length * 0.95) - 1]`，可独立重算。

## 响应式、视觉与键盘

自动化覆盖 `1440×900`、`1280×800`、`1024×768`、`768×1024`、`390×844`，以及外壳与详情边界 `900/901/902`、`1023/1024/1025` 和 200% 缩放。1024 宽度按实际主内容宽度使用 drawer，宽屏详情才进入 aside；详情 footer、按钮和说明均在可见裁剪边界内。

独立 Sol UX 复核另在 1440、1024、768、390 和宽屏 aside 上实际操作，验证普通创建空白、选例预填 0 调用、创建与继续并存、键盘收藏/继续、空态重置、详情重试、收藏故障、modal 焦点、HTTP(S) 链接白名单，以及角色标签在空白/草稿/消息三态的原生点击。报告 `/private/tmp/taiji-zhinang-independent-ux-review.md` 的 SHA-256 为 `b27fc112e9c851ef05aa03e8688266878d9ecc3fd89477a6dcdadf7270ff92a0`；其有界范围内无遗留 P0/P1/P2。

## 发现与修正

本轮浏览器与独立审查发现并修正的阻断问题包括：错误的“全部”视图切换、示例无条件预填、详情新建/继续互斥、嵌套按钮键盘事件被卡片抢占、空态无恢复入口、详情无重试、modal 焦点逃逸、收藏失败后控件永久 disabled、非 HTTP(S) 来源可点击、角色标签被外壳层遮挡，以及工具成功元数据跨消息/刷新丢失和失败工具结果产生假成果。每项均先保留失败证据，再由聚焦测试或实际浏览器复验闭合。

首轮完整暂存 Sol 终审又以反例确认三项 P1：公开事件会暴露 running/failed/cancelled 或无工具 ID 的 `artifact_path`；Anthropic `tool_use` 没有收到成功摘要回写而在 `msg_limit` 刷新后丢成果；前端仍会从失败工具的私有 args/result 或纯 assistant diff 猜测成果。修正后仅成功、非错误、带非空工具 ID 且名称匹配的公开投影能进入成果列表；Provider 请求会移除这些 WebUI 专用字段，内部 diff 推断只保留给预览缓存失效。正式审查报告 `/private/tmp/taiji-zhinang-stage4-final-sol-review.md` 的 SHA-256 为 `6b0e805d0553d434573b73b62a2c5c9b130628762af9180fb45e5d60964e2ef5`；RED 日志分别为 `25f25242cd37807bff494ca181b906b7e828d9529e7c45c3d42d697c99428af5` 和 `3e09c70ca2d3e554cbee82ebf13f08866e1a22f29946c310ba758f23d0db8b18`。

修正候选复审记录的两个 P2 已在后续有界任务闭合。目录初始不强制选中；打开、切换、关闭详情以及过滤移除当前角色时，卡片的 `aria-current` 与可见“当前查看”状态保持一致。收藏 grid 重绘前捕获用户实际停留的稳定卡片操作及角色、操作类型和相邻索引；重绘后恢复同一收藏/查看详情/继续控件，若角色被移除则回退到相邻操作或“浏览全部角色”，用户主动把焦点移到搜索框后也不会被抢回。首轮 RED 报告 SHA-256 为 `428689ddad91f76350f96e7c06ddea5edcfe1a76b9c05d68593afd7b69a91b24`；fresh Sol 追加的 grid 内移焦 RED 为 `fd06d8a8c65ebc525f6acec71dde13aacd931cb36dcc34a7cc9d2b0c30fa46ec`，GREEN 由上述最终 `selection-focus` 与 `faults` 证明。

恢复专项的历史失败最终定位为测试 Provider 对合并用户消息按“是否出现旧 marker”分派，导致恢复请求进入旧取消/失败分支；修正为按各 marker 最后出现位置识别当前意图后通过。该项是 fixture 分类错误，不是产品修正。

## 仍未覆盖的证据层

- 未运行 axe 等专用可访问性扫描器；当前证据为原生键盘操作、焦点、ARIA/语义断言和独立人工交互。
- 未运行像素基线式自动视觉回归；当前证据为规定视口截图、几何/裁剪/hit-test 断言和独立视觉审查。
- 未使用真实外部模型、OAuth、联网工具或客户数据；模型与工具协议由 loopback Provider 驱动。
- 未执行安装包、Kylin/UOS/Windows、目标机、升级、签名、发布或正式交付验收。
