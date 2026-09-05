# 太极智囊实施与验收台账

> 状态更新：2026-09-05（阶段 2、3 已审核并本地提交；阶段 4 阻断项已闭合、全量门禁通过，当前完成但有两项已知 P2 限制）
> 功能契约：[太极智囊 PRD](../requirements/2026-09-05-taiji-zhinang-prd.md)

## 当前状态卡

| 项目 | 已验证状态 |
| --- | --- |
| 物理仓库 / Git common dir | `/Users/bwb/Documents/工作/taiji-agentv1.0` / `.git` |
| 开发线与基线 | `main@ce4aadd17b526f50c96d436860db117105b7db27`；与未刷新本地跟踪引用 `origin/main@18a607bc96a5689b184e4631a9606ce1cbb24e1e` 比较为 `4 0`，不能证明当前远端状态；阶段 3 已在本地提交，阶段 4 结论绑定当前未提交工作树 |
| 写入边界 | 当前实施者是共享工作树及 Git index 唯一写入者；其他协作者只读 |
| 当前证据层 | 本地源码与资源；生产 WebUI HTTP 路由、真实 `AIAgent` 到 loopback mock Provider、真实持久化/进程重启、真实 `write_file` 写盘/预览/下载，以及规定视口的 headless Chromium 证据；不外推为安装态、目标机、真实模型或发布态 |
| 已完成 | 274 个中文角色资源对账；完整不可变角色快照；目录/筛选/详情/收藏/最近；幂等新建和草稿/File 安全；现有外壳内完整智囊 UI；角色持续注入；附件与真实文件产物；取消/模型失败恢复；下架、键盘、焦点、响应式、性能及原产品回归 |
| 未完成 | 修正候选完整暂存内容的 fresh Sol 最终结论和通过后的本地提交。宽屏详情卡片选中态与收藏重渲染后的焦点恢复为已知 P2；axe 专用扫描、像素基线视觉回归、真实模型、安装态和目标机验收不在本轮已验证证据层 |
| 当前出口 | 阶段 2 本地提交 `d3a108c6d373da767b55ecf82c1f7cd4b249bc99`，阶段 3 fresh Sol 终审通过后本地提交 `ce4aadd17b526f50c96d436860db117105b7db27`；阶段 4 首轮暂存终审的三项 P1 已闭合，修正候选 full 和最终浏览器主链通过，复审阶段性结论无 P0/P1、保留两项 P2。等待文档更新后的完整暂存最终结论；未获具体远端授权，不 push |

## 工具链与隔离环境

| 工具 / 边界 | 固定来源与结果 |
| --- | --- |
| Python | 阶段 0/1 纯目录使用 `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`；阶段 2 WebUI 使用仓库 Agent venv 的 Python 3.11，并同时固定 `HERMES_WEBUI_AGENT_DIR` 与 `HERMES_WEBUI_PYTHON` |
| Node | `/Users/bwb/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`，Node v24.19.0 |
| npm | 上述 Node 执行 `/Users/bwb/.hermes/node/lib/node_modules/npm/bin/npm-cli.js`，npm 11.19.0 |
| Playwright | 显式 `PLAYWRIGHT_NODE_PATH=/Users/bwb/.codex/skills/huashu-design/node_modules/playwright-core`；正式 harness 拒绝缺省模块路径，不自动安装 |
| Chromium | 使用现有 `~/Library/Caches/ms-playwright/chromium-*`，不下载或启动默认浏览器 |
| 自动化状态根 | 每组使用独立 `/private/tmp/taiji-zhinang-*`；同时固定 `HERMES_WEBUI_STATE_DIR`、`HERMES_WEBUI_TEST_STATE_DIR`（pytest）、`HERMES_HOME`、`HERMES_BASE_HOME`、`TAIJI_RUNTIME_HOME` |
| 自动化配置 / 工作区 | 每组独立 `/private/tmp/taiji-zhinang-*-home/config.yaml` / `/private/tmp/taiji-zhinang-*-workspace`；固定 `HERMES_CONFIG_PATH` 与 `HERMES_WEBUI_DEFAULT_WORKSPACE` |
| 服务与 Provider | 运行时选择空闲 loopback 端口；mock Provider 只监听 loopback；同时设置 `TAIJI_WEBUI_TEST_NETWORK_BLOCK=1` 和现有测试使用的 `HERMES_WEBUI_TEST_NETWORK_BLOCK=1` |
| 凭据隔离 | 复用 `tests/conftest.py` 的完整 Provider、AWS、记忆、消息、浏览/搜索与 GitHub 凭据前缀剥离清单；真实 Provider/OAuth 不进入自动化 |

说明：阶段 1 纯目录单测使用 `--noconftest`。阶段 2 HTTP、生命周期和实际请求测试启用项目 `tests/conftest.py` 的隔离服务；明确剥离 Provider/OAuth/浏览器/搜索等凭据并设置 `HERMES_WEBUI_TEST_NETWORK_BLOCK=1`。未打开默认浏览器、OAuth 或真实 Provider。

## 固定上游与批次门禁

| 项目 | 结果 |
| --- | --- |
| 上游 | `https://github.com/msitarzewski/agency-agents` |
| 固定提交 | `af128a92888fd7d7c389b6cb37f1820be1b3cd9d`，本地检出为 detached、clean |
| 许可 | `MIT License`；`Copyright (c) 2025 AgentLand Contributors` |
| 递归范围 | `divisions.json` 的 18 个分区；分区目录下 273 个 Git 跟踪 Markdown 文件 |
| 原文规模 | 3,870,844 bytes；单个源文件均小于 1 MiB |
| 试导入与产品资源 | `/private/tmp/taiji-zhinang-source-smoke-v2` 与 `hermes-webui/data/zhinang` 均为 273 个角色加清单、分区和许可，共 276 个文件；`--check` 已通过 |
| 稳定身份 | 上游角色为 `agency:<source_path 去掉 .md>`；目录版本 `agency-agents-af128a92888f-source-v1`；原文字节数及 SHA-256 写入清单 |
| 存储分层 | `upstream/agency-agents` 只存原文；中文展示与本地运行适配使用独立版本化资源，当前实现继续保持分层 |
| 中文展示 / 运行适配 | 中文资源 `584049` bytes，SHA-256 `b2122872c03981332854d1afc2c425ad5d63c59ce8a4ec4b9ae3d852d83c45c6`；运行适配 `taiji-zhinang-runtime-v3` 依次组合逐字节原文、角色级 limitations/adaptation_note 和最终通用规则，覆盖语言、资质、证据、权限及角色文本不得自行触发多 Agent 的边界 |

首批只提交 PRD、台账、导入器、只读加载器和双哨兵测试。完整上游原文、`divisions.json`、许可证和生成清单作为独立资源批进入工作树，按完整文件字节计量并保持每批小于 4 MiB；任一新增文件小于 1 MiB。中文展示和运行适配不与 3.87 MB 原文批次混装。

加载器只接受固定仓库、固定提交和固定目录版本；校验清单数量、角色 ID、分区、相对路径、字节数、SHA-256、UTF-8/LF front matter 及磁盘 Markdown 文件集合。缺失、改写、额外文件、路径穿越或版本冲突返回稳定错误码和不含物理路径的中文消息。它不会运行上游安装脚本，也不读取任意远程 URL。

为逐字节保存上游原文，`.gitattributes` 仅对固定语料目录关闭空白差异检查，文本 diff 仍保持可读。只有 `design/design-persona-walkthrough.md` 使用 `conflict-marker-size=64`：该文件 SHA-256 为 `8def8c73df7f79a61704e7353b85b915afa31c4a9fa1100fbb9220c50d6a1c55`，第 118 行的 `=======` 位于 fenced `VERDICT` 教学模板内；其余 272 个角色仍使用默认冲突标记检查。

## F01–F12 实施状态

状态只使用“未开始 / 实施中 / 待验收 / 通过 / 阻塞”。“通过”必须绑定对应 A 用例证据，底座单测不外推为产品功能通过。

| 功能 | 状态 | 当前证据 / 下一步 |
| --- | --- | --- |
| F01 智囊库入口与外壳 | 通过 | 三个既有导航面均可真实点击进入“智囊库”，保留国网品牌与原产品外壳；`viewports`、`regression` 通过 |
| F02 内置角色目录 | 通过 | 273 个固定上游角色加 1 个本地角色的中文层、真实目录 API 和可见列表通过 A01/A02/A17 |
| F03 分类、搜索、精选与全部角色 | 通过 | 正交筛选、24 条分页、固定 6 精选、竞态与 500 条性能通过 A03/A17 |
| F04 我的收藏 | 通过 | profile 权威原子收藏、双 Store/双窗口、失败恢复和真实 WebUI 重启通过 A04/A12/A14 |
| F05 最近使用 | 通过 | 真实受理最近、展示快照与可继续 tip 分离、删除/分支/复制回退和 UI 继续入口通过 A05/A12 |
| F06 角色卡片及详情 | 通过 | 安全卡片、明确“查看详情”、完整详情、可重试与响应式 drawer/aside 通过 A06/A14/A15；宽屏 aside 当前卡片尚无选中高亮/`aria-selected`（P2） |
| F07 完整角色说明与来源 | 通过 | 当前/历史说明、改编、版本、MIT、固定来源及 HTTP(S) 白名单通过 A06/A12/A15 |
| F08 示例任务 | 通过 | 所选示例预填且 0 模型调用；普通创建空白；失败保留原 SID/草稿/File；切换隔离通过 A07 |
| F09 使用此智囊 | 通过 | 可见 CTA、无模型创建、请求幂等、受理后可新建与继续并存通过 A05/A08/A09 |
| F10 持续角色上下文 | 通过 | Provider 实际注入、可见角色标签、缓存/self-heal/压缩/重启/复制/分支/清空通过 A10/A11 |
| F11 既有聊天与成果链路 | 通过 | 附件进入真实 Agent、`write_file` 真写盘、预览/下载/刷新、取消与失败不出假成果通过 A13/A16 |
| F12 完整状态与可访问性 | 通过 | 加载/空态/错误/重试、原生键盘、焦点、五视口/边界/200% 和外网阻断通过 A01/A14/A17；收藏后的 grid DOM 重建尚未恢复对应按钮键盘焦点（P2） |

## A01–A17 验收台账

| 用例 | 状态 | 计划证据 / 当前边界 |
| --- | --- | --- |
| A01 来源和范围 | 通过 | 真实源码 WebUI 三个导航入口、国网品牌和排除项检查；`viewports` 87 checks 与独立 UX 复核通过 |
| A02 目录对账 | 通过 | 273 个固定源加 1 个本地角色与中文层 274 项逐项对账；必填字段、六分类、能力 3–5、交付示例 2–3、固定提交和有效提示词由聚焦测试验证 |
| A03 筛选分页 | 通过 | 中英文/标签搜索、领域、精选、收藏、最近正交与稳定 24 条分页通过；500 条 30 次真实 HTTP/浏览器性能见 `performance` 67 checks |
| A04 收藏持久化 | 通过 | 两独立 Store 并发、磁盘故障不变、双窗口、profile 隔离与真实 WebUI 重启见 `lifecycle` 19 checks |
| A05 使用事实 | 通过 | sync/streaming/Gateway 受理、最近 tip 安全解析及删除回退通过；浏览器验证示例 0 调用、受理后新建与继续并存 |
| A06 详情真实性 | 通过 | 当前详情来自固定目录、历史详情来自会话快照，含原文/改编/版本/MIT/来源且不泄露内部提示词；浏览器完整显示 |
| A07 示例与草稿 | 通过 | Node 竞态契约及 `draft-idempotency` 11 checks：选例 0 调用、失败保留原 SID/文本/原生 File、成功后隔离 |
| A08 创建幂等 | 通过 | HTTP 顺序/并发/冲突/重启 replay 与浏览器双击、受理后 504、同 request_id 重试均通过 |
| A09 环境不被替换 | 通过 | 无模型创建、非法上下文拒绝；浏览器断言 active profile/workspace/model/provider 与 config 文件 SHA 不变 |
| A10 真正进入模型 | 通过 | 多角色真实 `AIAgent` mock SDK 覆盖 sync/streaming/self-heal；浏览器主链再次观察所选角色与工具 schema 进入 Provider |
| A11 生命周期一致 | 通过 | 后端覆盖缓存、压缩、复制/分支/清空；`lifecycle` 以同 SID 真重启验证完整 snapshot/effective prompt/Provider system 哈希一致 |
| A12 更新与损坏 | 通过 | schema2 canonical digest、坏 sidecar 隔离、目录移除与 replay 通过；`removed` 验证下架保留安全摘要、历史入口与取消 |
| A13 聊天与产物 | 通过 | `flow`/`recovery`：真实附件、流式 Agent、`write_file` 磁盘/下载 SHA、追问/刷新、取消/模型失败恢复且无假成果 |
| A14 状态与键盘 | 通过 | `faults` 与 `viewports` 覆盖加载/目录/详情/收藏故障、retry、Tab/Shift+Tab/Escape/焦点恢复、裁剪及 hit-test |
| A15 安全边界 | 通过 | 后端路径/快照/profile/权限边界与前端 HTML 转义、HTTP(S) 来源白名单、外网请求零记录均通过 |
| A16 产品回归 | 通过 | `regression` 12 checks：普通聊天、personality、模型配置、草稿/会话同步、写作/专家团真实启动与重载；固定角色人格入口禁用 |
| A17 本地可用与性能 | 通过 | 双 network block、浏览器外部请求零；500 条真实 HTTP 目录 30 次 p95 `16.888917 ms`，防抖后交互 p95 `44.883083 ms`，长内容可达 |

浏览器已覆盖 PRD 的五种尺寸，另测现有外壳断点 `900/901/902`、详情边界 `1023/1024/1025` 和 200% 缩放。键盘、焦点、ARIA/语义、截图、几何裁剪与 hit-test 已验证；axe 专用扫描和像素基线自动视觉回归未验证，不能由人工/语义检查替代。

## 验证记录与故障边界

| 日期 | 命令 / 观察 | 结果 |
| --- | --- | --- |
| 2026-09-05 | `git fetch origin main` 后比较 `HEAD...origin/main` | `0 0`，基线未落后 |
| 2026-09-05 | 固定提交 checkout 首次在受限网络内补取 promisor blob | DNS 不可用；确认原因后仅一次允许联网重试，固定提交 clean checkout 成功 |
| 2026-09-05 | 未固定解释器直接调用 pytest | PATH 进入 Python 3.14 且无 pytest；改用计划指定 Python 3.13，不再重试错误解释器 |
| 2026-09-05 | Python 3.13 默认加载全局 `tests/conftest.py` | 纯目录用例被无关测试服务 socket bind 阻断；确认范围后用 `--noconftest` 执行纯单元契约 |
| 2026-09-05 | 加载器实现前执行双哨兵目录测试 | RED：`ModuleNotFoundError: No module named 'api.zhinang'` |
| 2026-09-05 | `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest --noconftest tests/test_zhinang_catalog.py -q` | GREEN：`7 passed` |
| 2026-09-05 | 导入固定源到 `/private/tmp/taiji-zhinang-source-smoke` | PASS：固定提交、273 roles、固定 catalog version |
| 2026-09-05 | 首次运行导入器 `--check` | FAIL：脚本入口只有 `scripts/` 模块路径，无法导入同仓库加载器；加入固定 WebUI 根后同一检查一次通过 |
| 2026-09-05 | 导入器 `--check` 修正后复验 | PASS：273 个角色清单和固定上游摘要一致 |
| 2026-09-05 | 固定 Node 24 受控执行 `./scripts/verify.sh` | PASS：根套件 1334、WebUI 952 及关联 CLI/bootstrap/runtime 套件通过；仅现有依赖弃用警告 |
| 2026-09-05 | 首批提交及推送 | 本地提交 `e609b6b0fffcacd00b0c5c7d77ecadabe341e118`；自动审批审查拒绝向具体 `origin/main` 外发，未推送，等待用户明确授权 |
| 2026-09-05 | 阶段 2 首次直接运行 WebUI 聚焦测试 | RED：实际加载 `~/.hermes/hermes-agent`，缺少本仓库 `agent.provider_credentials`；根因是未固定 Agent 模块来源，不是功能失败 |
| 2026-09-05 | 固定 `HERMES_WEBUI_AGENT_DIR=<repo>/hermes-local-lab/sources/hermes-agent`、同仓 venv Python、独立 home/state/config/workspace 和 network block 后运行新增契约 | RED 依次捕获缺少快照 API、路由幂等重复 SID/历史接口 404、中文 loader 缺失；实现后最终 `test_zhinang_catalog.py test_zhinang_session_contract.py` 为 `33 passed, 1 warning in 13.47s` |
| 2026-09-05 | 实际 mock Provider 哨兵 | streaming 和 sync 都构造真实 `run_agent.AIAgent`，捕获 `client.chat.completions.create` 的请求；两个角色各两轮互斥，缓存复用不串角色，已绑定角色时旧 personality 哨兵不出现，品牌隐私提示保留 |
| 2026-09-05 | 两条 credential self-heal worker | `result_error` 和 outer `exception` 各对 ALPHA/BETA 两角色执行；初始 401 是受控实例故障注入，每组均触发一次凭据刷新、重建不同的真实 `AIAgent`，并在重建后 `client.chat.completions.create` 的实际 SDK 请求中验证选中角色、互斥角色、旧 personality 和品牌隐私边界；4 参数项通过 |
| 2026-09-05 | `py_compile` 六个受影响 Python 模块、`node --check static/sessions.js`、`git diff --check` | PASS |
| 2026-09-05 | 受影响关联回归：`test_webui_gateway_chat_backend.py`、`test_expert_team_launch_atomicity.py`、空会话/metadata save/profile env/跨窗口/sprint28/压缩清理/static runtime lint/new-session inflight | `145 passed, 13 failed, 1 warning in 9.21s`；所有失败均为下述同一既有签名失配 |
| 2026-09-05 | 追加关联回归：品牌隐私、session ops、provider mismatch、Gateway context、capability generation、stale stream 与 new-session inflight | 从仓库根执行时 `202 passed`，stale-stream 的 3 个源码读取用例因测试自身要求 WebUI cwd 而失败；改在 `hermes-webui` cwd 运行该完整文件为 `8 passed`，无产品失败 |
| 2026-09-05 | `python3 scripts/check-local-change-safety.py` | 初次仅将测试合成 `api_key` 字面量报为 `credential-assignment`；改用门禁认可的 `TEST_ONLY_*` 占位符后 PASS，未放宽门禁 |
| 2026-09-05 | 固定 Node 24、仓库 Agent venv 与独立 home/base/state/config/workspace 执行 `scripts/verify.sh --full` | 修正 profile scope 前候选 PASS：local change safety PASS；root `1334`（skipped=2，603.976s）；Desktop `79/79`；DOCX `278/278`；Agent `220/220`；WebUI lint PASS；WebUI 注册计划 `952 passed, 1 warning in 27.39s`；branding Agent `24/24`；bootstrap Agent `12 passed, 5 skipped`；bootstrap WebUI `69 passed`；coexistence `6 passed`；最终 `verification: PASS`。随后发现并修正 profile scope，故本条不作为最终暂存字节门禁结论 |
| 2026-09-05 | 跨 profile HTTP RED：所属 default 会话由 `Cookie: hermes_profile=research` 请求 `/api/zhinang/session-role` | FAIL：修正前返回 `200`；证明历史完整说明没有沿用会话 profile 可见性边界 |
| 2026-09-05 | 请求 profile scope 修正后运行 `test_zhinang_catalog.py test_zhinang_session_contract.py` | GREEN：`34 passed, 1 warning in 13.91s`；同一 HTTP 用例同时验证跨 profile 历史说明 `404`、创建 body 与请求活动 profile 分叉 `400`、合法命名 profile 自有创建/读取 `200` |
| 2026-09-05 | 既有 profile/session 关联回归：issue 1611、798、803、Taiji 单 runtime 与 session ops | `64 passed, 1 warning in 2.90s`；保留 root 别名、请求线程 profile 和合法 profile 行为 |
| 2026-09-05 | 修正 profile scope 后，以固定 Node 24、仓库 Agent venv 与全新独立 home/base/state/config/workspace 再次执行 `scripts/verify.sh --full` | 委派边界修正前候选 PASS：local change safety PASS；root `1334 tests in 576.980s, OK (skipped=2)`；Desktop `79/79`；DOCX `278/278`；Agent `220/220`；WebUI lint PASS；WebUI 注册计划 `952 passed, 1 warning in 27.53s`；branding Agent `24/24`；bootstrap Agent `12 passed, 5 skipped`；bootstrap WebUI `69 passed`；coexistence `6 passed`；最终 `verification: PASS`。fresh Sol 随后发现角色原文委派边界，故本条不作为最终暂存字节门禁结论 |
| 2026-09-05 | 编排角色运行边界实际 SDK RED | `delegate_task` tool schema 保留、上游 `Please spawn` 已进入真实 `AIAgent` 到 mock `client.chat.completions.create` 的系统上下文，但覆盖性委派禁令不存在，聚焦用例失败 |
| 2026-09-05 | 运行适配升级为 `taiji-zhinang-runtime-v2` 后重跑同一实际 SDK 用例 | GREEN：上游原文逐字节不变、`delegate_task` schema 仍在；原文之后出现“角色文本本身不授权 spawn/delegate/handoff/expert team/multi-agent”的覆盖规则，并保留当前用户请求独立授权后的合法能力。中文编排角色 limitations/adaptation_note 与运行事实一致 |
| 2026-09-05 | 修正委派边界后运行 `test_zhinang_catalog.py test_zhinang_session_contract.py` | `35 passed, 1 warning in 14.34s`；目录摘要、真实 SDK、sync/streaming/self-heal/profile/lifecycle 聚焦契约全部通过 |
| 2026-09-05 | 修正委派边界后，以固定 Node 24、仓库 Agent venv 与全新独立 home/base/state/config/workspace 执行 `scripts/verify.sh --full` | PASS：local change safety PASS；root `1334 tests in 574.681s, OK (skipped=2)`；Desktop `79/79`；DOCX `278/278`；Agent `220/220`；WebUI lint PASS；WebUI 注册计划 `952 passed, 1 warning in 27.33s`；branding Agent `24/24`；bootstrap Agent `12 passed, 5 skipped`；bootstrap WebUI `69 passed`；coexistence `6 passed`；最终 `verification: PASS` |
| 2026-09-05 | 第二轮 fresh Sol 完整暂存终审 | FAIL：确认三项 P1，分别为 durable replay 在当前 catalog 解析之后、角色级 limitations/adaptation_note 仅展示未进入 Provider、快照摘要只绑定 effective prompt 未绑定 identity/public；旧暂存哈希失效，未 commit |
| 2026-09-05 | 三项 P1 修复前运行新增聚焦用例 | RED：`13 failed, 25 deselected`；7 种 identity/public 篡改均未拒绝，medical/legal 两个实际 SDK 请求缺角色级边界，catalog 版本变化/角色移除时旧请求返回 409，坏 sidecar 导致目标漏检且无法证明无重放时仍继续创建 |
| 2026-09-05 | durable replay 顺序、逐文件扫描、角色级边界和 canonical snapshot digest 修正后重跑同 13 项 | GREEN：`13 passed, 25 deselected, 1 warning in 4.61s`；另以 HTTP 坏 sidecar 注入验证新建返回 `zhinang_create_replay_unavailable` 且无 session，`1 passed` |
| 2026-09-05 | 修正后三项受影响完整聚焦：`test_zhinang_catalog.py test_zhinang_session_contract.py` | GREEN：`48 passed, 1 warning in 16.91s`；真实 `AIAgent` mock SDK、Provider 零调用、外部持久 sidecar 重放与 metadata-only 全量 hydrate 均在同一文件覆盖 |
| 2026-09-05 | 修正后 `py_compile`、`git diff --check`、`python3 scripts/check-local-change-safety.py` | PASS |
| 2026-09-05 | 修正后首次启动 `scripts/verify.sh --full` | 测试未开始，预检以 `<change-set>: total-size-limit` fail-closed；根因是 index 仍为旧候选而 4 个修后文件同时存在 staged 基线与 unstaged 新版，安全脚本按两层完整文件重复计量。精确暂存这 4 个文件后，同一检查恢复 PASS；未放宽门禁 |
| 2026-09-05 | 精确暂存修后文件后，以固定 Node 24、仓库 Agent venv 与全新独立 home/base/state/config/workspace 重跑 `scripts/verify.sh --full` | PASS：local change safety PASS；root `1334 tests in 600.394s, OK (skipped=2)`；Desktop `79/79`；DOCX `278/278`；Agent `220/220`；WebUI lint PASS；WebUI 注册计划 `952 passed, 1 warning in 27.25s`；branding Agent `24/24`；bootstrap Agent `12 passed, 5 skipped`；bootstrap WebUI `69 passed`；coexistence `6 passed`；最终 `verification: PASS`。完整日志 `/private/tmp/taiji-zhinang-p1-full2.log` |
| 2026-09-05 | 第三轮 fresh Sol 完整暂存终审 | FAIL：确认三项新 P1，分别为单个损坏快照使 `/api/sessions` 整体失败、未上传原生 `File` 跨任务泄漏、同 SID 旧 debounce 请求可晚到覆盖切换前最新草稿或 clear；无其他阻断，旧暂存哈希失效，未 commit |
| 2026-09-05 | 第三轮修正前运行损坏快照聚焦与固定 Node 24 VM 探针 | RED：pytest 9 项失败；Node 的 10 个实际函数场景为 `5 failed, 5 passed`，失败精确覆盖 A/C 反写、A/clear 竞态、新角色继承旧附件、clear 后附件复活和目标无草稿时旧文本/附件污染 |
| 2026-09-05 | 损坏快照安全投影、按 SID 附件缓存和草稿写入链修正后 | GREEN：新增后端/前端聚焦 `12 passed`；Node 24 同一探针 `10 passed, 0 failed`；另补 `/api/chat/start` 在 worker 创建前校验，损坏或缺失的持久绑定快照返回 `zhinang_snapshot_invalid` 409，聚焦 `3 passed`，mock worker/Provider 均为零调用 |
| 2026-09-05 | 修正后 `test_zhinang_catalog.py test_zhinang_session_contract.py` | GREEN：`61 passed, 1 warning in 16.63s`；首次运行的 12 个实际 `AIAgent` 用例因全新隔离 HOME 误触宿主授权校验而失败，确认后用测试 fixture 固定 `require_valid_license=None`，不读取/消费宿主许可证，同一关联集一次通过 |
| 2026-09-05 | session index、metadata fast path、草稿、并发切换、空草稿恢复、附件、force refresh、新建和模型恢复关联集 | `109 passed, 1 failed, 1 warning in 1.97s`；唯一失败 node ID `tests/test_session_metadata_fast_path.py::test_boot_renders_session_list_before_workspace_and_onboarding_settle` 要求 boot.js 含精确字符串 `const _onboardingReady=_bootSettings.onboarding_completed?Promise.resolve(false):loadOnboardingWizard();`，当前与 `HEAD@266dfd73` 的 boot.js 字节 SHA-256 均为 `c35ff4e5a8dbf7d5dd79d5cfeb739e0be8e7f66ceba271f972679559217138d4`，测试文件当前与 HEAD 字节 SHA-256 均为 `7013f44899d116584500b2371b329e18f9215fc174a079214cfa49f4619ac9c0`；boot.js 在两个版本均不含该字符串，本批未改 boot.js 或该测试，不作无关修复 |
| 2026-09-05 | 第三轮修正后首次全量环境尝试 | root `1334` 中 `2 failures, 12 errors` 后停止；失败均来自额外 `env -i` 并改写 POSIX `HOME/TMPDIR`，分别触发 Linux upgrade owner、release trusted-directory 和 unified license profile 契约。恢复正常账户 `HOME/TMPDIR`、保留独立 Hermes 状态/配置/工作区、显式清除凭据并继续双 network block 后，三类各一代表节点 `Ran 3 tests ... OK`；未改产品代码或门禁 |
| 2026-09-05 | 已修正账户环境但仍把同一 `HERMES_WEBUI_TEST_PORT=18849` 固定给完整脚本的第二次尝试 | root `1334`、Desktop `79`、DOCX `278`、Agent `220`、WebUI lint、WebUI `952`、branding `24`、bootstrap Agent `12` 已通过；随后第二个 WebUI pytest 的 bootstrap 69 项均在 fixture setup 以 `port 18849 already occupied` 退出，未运行产品断言，整条 full 不记 PASS。`tests/conftest.py` 明确要求跨 pytest invocation 使用 process-scoped auto port |
| 2026-09-05 | 顺序验证 process-scoped auto port | bootstrap WebUI 在 PID `68139` / port `28441` 为 `69 passed`；紧接 coexistence 在 PID `68174` / port `28476` 为 `6 passed`，证明两个 pytest invocation 使用独立端口；仍为独立 Hermes 根、凭据清除与双 network block |
| 2026-09-05 | 同一暂存源码，以正常账户 `HOME/TMPDIR`、独立 Hermes home/base/state/config/workspace、凭据清除、双 network block、process-scoped auto port 执行原入口 `scripts/verify.sh --full` | PASS：local change safety PASS；root `1334 tests in 549.759s, OK (skipped=2)`；Desktop `79/79`；DOCX `278/278`；Agent `220/220`；WebUI lint PASS；WebUI 注册计划 `952 passed, 1 warning in 27.19s`；branding Agent `24/24`；bootstrap Agent `12 passed, 5 skipped`；bootstrap WebUI `69 passed`；coexistence `6 passed`；最终 `verification: PASS`。完整日志 `/private/tmp/taiji-zhinang-v3-full3.log` |

| 2026-09-05 | 阶段 3 目录/收藏/最近契约先于实现运行 | RED：聚焦测试在 collection 阶段缺少 `CATALOG_CATEGORIES` 等新后端符号；确认契约尚未实现后进入最小修正 |
| 2026-09-05 | 阶段 3 `test_zhinang_library.py` 聚焦契约 | GREEN：`17 passed, 1 warning in 2.19s`；覆盖 274 项安全目录、固定 6 精选、24 条分页、正交筛选、完整详情、profile 收藏、两独立 Store 并发、写失败磁盘不变、下架取消和最近 tip 回溯；唯一警告为现有 `discord/player.py` 的 Python `audioop` 弃用提示 |
| 2026-09-05 | `test_zhinang_catalog.py test_zhinang_session_contract.py test_zhinang_library.py` 关联回归 | GREEN：`79 passed, 1 warning in 17.80s`；目录/收藏/最近接口与阶段 2 快照、幂等创建、执行注入和生命周期契约兼容 |
| 2026-09-05 | 阶段 3 `py_compile`、`git diff --check`、`python3 scripts/check-local-change-safety.py` | PASS；4 个预计交付文件完整体积 `1,296,135` bytes，低于单批 4 MiB 门禁 |

| 2026-09-05 | 阶段 3 当前未暂存候选，保留正常账户 `HOME/TMPDIR`、独立 Hermes home/base/state/config/workspace、显式清除凭据、双 network block 且由 conftest 自动选择跨进程端口/测试状态，执行原入口 `scripts/verify.sh --full` | PASS：local change safety PASS；root `1334 tests in 552.556s, OK (skipped=2)`；Desktop `79/79`；DOCX `278/278`；Agent `220/220`；WebUI lint PASS；WebUI 注册集 `952 passed, 1 warning in 27.26s`；branding Agent `24/24`；bootstrap Agent `12 passed, 5 skipped`；bootstrap WebUI `69 passed`；coexistence `6 passed`；最终 `verification: PASS`；日志 `/private/tmp/taiji-zhinang-stage3-full.log` |
| 2026-09-05 | 阶段 3 完整暂存 fresh Sol 终审及本地提交 | PASS：plain diff `72159` bytes / SHA-256 `1a6c978b8832910c23d6bd7e497807ab05ed95a4e7f1d7064101d4ee45a3b4fd`；full-index `72415` bytes / SHA-256 `5d4819fe7bf14165449005769da655e2a9f19d5dc14ea5c530edec3f4cfe8d24`；提交 `ce4aadd17b526f50c96d436860db117105b7db27`，未 push |
| 2026-09-05 | 阶段 4 UI 与产物桥接聚焦 RED→GREEN | 初始 RED 捕获缺少可见智囊页面、收藏/焦点失败状态、非 HTTP(S) 来源、角色标签 hit-test、工具成果元数据丢失及失败假成果；修正后 Python 聚焦 `110 passed`、Node 草稿运行契约 `10 passed`，后续最终聚焦以本批收尾记录为准 |
| 2026-09-05 | 详情“继续最近任务”后端字段假设核查 | 撤销：曾推测当前详情 API 缺少 continue 会使 UI 丢入口；实际最近卡片已携带通过安全 resolver 的 `continue_session_id`，浏览器中卡片继续与详情新建并存，后端 `zhinang.py`/相关测试未因此改动 |
| 2026-09-05 | 隔离浏览器 `flow` / `faults` / `draft-idempotency` / `recovery` / `removed` / `regression` | PASS：修正前完整主链为 40 checks；最终同一正式 runner 为 45 checks / 9 Provider requests，新增 assistant-only diff 在刷新前后均无假成果卡；其余范围分别为 14/11/12/10/12 checks。主链使用真实 WebUI、真实 `AIAgent`、loopback Provider 与真实 `write_file`，route mock 仅用于指定故障和下架投影；完整 JSON 与截图见阶段 4 UX 报告 |
| 2026-09-05 | 隔离浏览器 `viewports` | PASS：87 checks；覆盖 1440×900、1280×800、1024×768、768×1024、390×844、900/901/902、1023/1024/1025 与 200% 缩放 |
| 2026-09-05 | 隔离浏览器 `performance` | PASS：500 条数据经生产 HTTP handler/query/paging，30 次 HTTP p95 `16.888917 ms`，30 次浏览器防抖后可交互 p95 `44.883083 ms`，报告保留原始样本、算法与运行版本 |
| 2026-09-05 | 隔离浏览器 `lifecycle` 最终专项 | PASS：19 checks / 4 Provider requests；同 SID 经真实 WebUI SIGTERM/新 PID 后继续发送，完整 schema2 snapshot、effective prompt digest 与 Provider 角色 system hash 前后一致；报告 SHA-256 `74efcf70492c66f7d004b69f7be226e5d1e17a25ff2bb2ac2fe002ba4355a78d` |
| 2026-09-05 | 独立 Sol UX 复核 | PASS：1440/1024/768/390、宽屏 aside 与角色标签空白/草稿/消息三态原生点击均通过；有界范围无遗留 P0/P1/P2。报告 `/private/tmp/taiji-zhinang-independent-ux-review.md`，SHA-256 `b27fc112e9c851ef05aa03e8688266878d9ecc3fd89477a6dcdadf7270ff92a0` |
| 2026-09-05 | 阶段 4 最终聚焦与关联契约 | PASS：受影响 Python `93 passed, 1 warning in 2.29s`；阶段 2/3 关联 `79 passed, 1 warning in 18.39s`；Node 24 草稿/File 运行契约 `10 passed`；Python/JS 语法和 `git diff --check` 通过 |
| 2026-09-05 | 关联契约首次环境偏差 | 首次为 `74 passed, 5 failed`；额外设置 `TAIJI_RUNTIME_HOME` 触发产品 single-runtime，导致 profile 创建被拒及 default/research 合并。按既有多 profile pytest 环境 unset 该变量、保持独立 HERMES 根和双 network block 后同 79 项通过，未放宽 profile 断言；真实浏览器继续固定独立 `TAIJI_RUNTIME_HOME` |
| 2026-09-05 | 本地安全门禁首次检查阶段 4隐私测试 | 唯一 finding 是已存在的合成 `sk-...` canary 在完整变更文件中触发 `high-confidence-token`；测试改为运行时拼接同一 canary，隐私断言和实际字符串不变，单测通过且未改安全脚本/白名单；复验 `local change safety: PASS` |
| 2026-09-05 | 阶段 4 当前工作树，以固定 Node 24、正常账户 `HOME/TMPDIR`、独立 Hermes home/base/state/config/workspace、凭据清除、双 network block 和 pytest 自动端口执行原入口 `scripts/verify.sh --full` | PASS：local change safety；root `1334 tests in 552.907s, OK (skipped=2)`；Desktop `79/79`；DOCX `278/278`；Agent `220/220`；WebUI lint；WebUI `953 passed, 1 warning in 27.10s`；branding Agent `24/24`；bootstrap Agent `12 passed, 5 skipped`；bootstrap WebUI `69 passed`；coexistence `6 passed`；最终 `verification: PASS`。日志 `/private/tmp/taiji-zhinang-stage4-full.log`，SHA-256 `02fdd8a433e448a49cdc8c8eb34e656b5dcd8eeea0f28292b1b2657ec5c78ab5` |
| 2026-09-05 | 阶段 4 首轮完整暂存 Sol 终审 | FAIL：确认三项 P1——running/failed/cancelled/无工具 ID 的公开事件仍可携 `artifact_path`；Anthropic `content[].tool_use` 未回写成功成果，在 `msg_limit` 清空 session summaries 后刷新丢失；前端从失败工具私有 args/result 或 assistant-only diff 推测假成果。报告 `/private/tmp/taiji-zhinang-stage4-final-sol-review.md`，SHA-256 `6b0e805d0553d434573b73b62a2c5c9b130628762af9180fb45e5d60964e2ef5`；首轮暂存哈希失效，未 commit |
| 2026-09-05 | 首轮终审修正 RED→GREEN | RED 正式反例见 `/private/tmp/taiji-stage4-review-backend-red.log` 与 `/private/tmp/taiji-stage4-review-workspace-red.log`；修正后只允许明确成功、非错误、非空工具 ID 且名称匹配的公开成果投影，Anthropic/OpenAI 均回写并在 Provider 请求前移除 WebUI 专用字段；可见 collector 不再挖私有 args/result 或 assistant-only diff，内部 diff 仅供预览缓存失效 |
| 2026-09-05 | 修正轮聚焦与真实公共入口 | PASS：新增/受影响 5 项为 `5 passed`，其中真实隔离 HTTP `GET /api/session?messages=1&resolve_model=0&msg_limit=30` 验证 Anthropic 窗口刷新，固定 Node 24 验证实际 collector；三份受影响测试文件为 `87 passed, 1 warning`。最终真实浏览器 `flow` 为 `45 checks / 9 Provider requests`，0 console/page/external，JSON SHA-256 `aa53751272dfe63aa7a0de2ad8f7bb410623d33c54d673a2d5be2befb8437107`，runner SHA-256 `e8ec954c925d7443d8d77621a625c9cbdbfdd0e8a429cb0c9d0236faedb02e1a` |
| 2026-09-05 | 修正候选原入口 `scripts/verify.sh --full` | PASS：full 启动后产品三文件保持冻结；local change safety；root `1334 tests in 544.330s, OK (skipped=2)`；Desktop `79/79`；DOCX `278/278`；Agent `220/220`；WebUI lint；WebUI `953 passed, 1 warning in 27.11s`；branding Agent `24/24`；bootstrap Agent `12 passed, 5 skipped`；bootstrap WebUI `69 passed`；coexistence `6 passed`；最终 `verification: PASS`。日志 `/private/tmp/taiji-zhinang-stage4-review-fix-full.log`，SHA-256 `9135086839b36345c8572054976618af52b0b9038b2f263828d5d861c0a33245`。full 运行中仅为补实际浏览器证据修改正式 runner，full 不执行该 runner；最终 runner 字节随后由上述 `flow` 单独验证 |
| 2026-09-05 | 修正候选 Sol 复审阶段性结论 | 旧三项 P1 均闭合，无新 P0/P1；记录两项非阻断 P2：宽屏详情 aside 缺少当前卡片选中样式/`aria-selected`，收藏触发目录重渲染后未恢复到对应按钮的键盘焦点。按本轮修正上限不再改产品，文档准确标注限制后重新绑定完整暂存内容 |
| 2026-09-05 | 阶段 4 最终完整文件预算 | 修正候选为 `3,822,172` bytes / 21 files，低于单批 4 MiB 门禁；每个新增文件小于 1 MiB |

### 既有 Gateway 基线问题

13 个失败 node ID 均位于 `tests/test_webui_gateway_chat_backend.py`：`test_gateway_runs_accumulates_raw_internal_and_filtered_public_final_text`、`test_gateway_runs_short_buffered_delta_is_not_duplicated_by_completed_output`、`test_gateway_runs_collects_private_image_candidate_without_public_path`、`test_gateway_runs_terminal_events_keep_cancel_and_error_semantics` 的两个参数项、`test_gateway_runs_stops_orphan_when_started_session_id_does_not_match`、`test_gateway_runs_same_name_tool_completion_matches_stable_id`、`test_gateway_run_incomplete_event_stream_stops_run_and_returns_error`、`test_gateway_runs_user_cancel_returns_cancelled_even_with_partial_text`、`test_gateway_run_reasoning_uses_stateful_cross_chunk_filter`、`test_gateway_runs_server_cancelled_preserves_cancelled_outcome`、`test_gateway_runs_partial_eof_is_not_completed`、`test_gateway_runs_failed_discards_buffered_reasoning`。源码有 12 个 `brand_token_tail=` 调用点，其中参数化终止事件产生两个 node ID，所以共 13 项；错误均为 `TypeError: _stream_gateway_run_events() got an unexpected keyword argument 'brand_token_tail'`。

本批 diff 未修改 `_stream_gateway_run_events` 的定义。为排除导入路径或猴子补丁影响，使用 `git archive HEAD` 解出独立 `/private/tmp/taiji-zhinang-head-baseline`，以相同隔离环境单独执行首个代表 node ID；纯 `HEAD@266dfd73` 同样得到上述 TypeError。`scripts/verify.sh` 的 WebUI 952 注册计划只列品牌、模型配置、审批、专家团前端等选择器，不包含 `test_webui_gateway_chat_backend.py`，因此历史 952 通过与该问题不矛盾。本阶段不扩大范围修复该既有签名问题，也不把它表述为智囊核心阻断。

阶段 4 修正候选在精确暂存后运行 `scripts/check-local-change-safety.py` 和一次必要的 `scripts/verify.sh --full`，并记录完整文件字节；任何 staged bytes 变化都会使终审失效。完整暂存内容须经新的 Sol 终审 PASS 后才能本地提交。
