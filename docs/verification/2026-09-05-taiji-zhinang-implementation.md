# 太极智囊实施与验收台账

> 状态更新：2026-09-05（阶段 2 已审核并提交；阶段 3 目录、详情、收藏与最近使用后端实施中）
> 功能契约：[太极智囊 PRD](../requirements/2026-09-05-taiji-zhinang-prd.md)

## 当前状态卡

| 项目 | 已验证状态 |
| --- | --- |
| 物理仓库 / Git common dir | `/Users/bwb/Documents/工作/taiji-agentv1.0` / `.git` |
| 开发线与基线 | `main@d3a108c6d373da767b55ecf82c1f7cd4b249bc99`；`origin/main@18a607bc96a5689b184e4631a9606ce1cbb24e1e`，本地领先 3、远端未领先；阶段 2 已在本地提交，阶段 3 结论绑定当前未提交工作树 |
| 写入边界 | 当前实施者是共享工作树及 Git index 唯一写入者；其他协作者只读 |
| 当前证据层 | 本地源码与资源；目录/详情/收藏/最近使用 HTTP 路由、真实 `AIAgent` 到 mock Provider、持久化/重启和静态前端契约证据；尚无可见智囊页面或浏览器证据 |
| 已完成 | 274 个中文角色资源对账；服务端完整不可变角色快照；幂等新建与历史详情；目录筛选及 24 条分页、固定 6 精选、完整安全当前详情；profile 权威原子收藏与下架取消；基于真实受理记录的最近任务回溯；每轮角色注入及压缩、重启、复制/分支/清空契约；按 SID 串行草稿与未上传 `File` 内存隔离 |
| 未完成 | 智囊库可见页面及相关浏览器验收；A07 保存失败浏览器交互；A13/A14/A17；F01–F12 和 A01–A17 整体验收 |
| 当前出口 | 阶段 2 已经 fresh Sol v4 审核通过并本地提交 `d3a108c6d373da767b55ecf82c1f7cd4b249bc99`；阶段 3 已按 RED→GREEN 完成实施，79 项聚焦/关联回归及统一 `scripts/verify.sh --full` 全部 PASS，待精确暂存、新哈希与 fresh Sol 终审；终审 PASS 前不提交，不重试未获具体远端授权的 push |

## 工具链与隔离环境

| 工具 / 边界 | 固定来源与结果 |
| --- | --- |
| Python | 阶段 0/1 纯目录使用 `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`；阶段 2 WebUI 使用仓库 Agent venv 的 Python 3.11，并同时固定 `HERMES_WEBUI_AGENT_DIR` 与 `HERMES_WEBUI_PYTHON` |
| Node | `/Users/bwb/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`，Node v24.19.0 |
| npm | 上述 Node 执行 `/Users/bwb/.hermes/node/lib/node_modules/npm/bin/npm-cli.js`，npm 11.19.0 |
| Playwright | `NODE_PATH=/Users/bwb/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules`；`require.resolve('playwright')` 指向该固定目录 |
| Chromium | 使用现有 `~/Library/Caches/ms-playwright/chromium-*`，不下载或启动默认浏览器 |
| 自动化状态根 | 每组使用独立 `/private/tmp/taiji-zhinang-*-state`；同时固定 `HERMES_WEBUI_STATE_DIR`、`HERMES_WEBUI_TEST_STATE_DIR`、`HERMES_HOME`、`HERMES_BASE_HOME` |
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
| 存储分层 | `upstream/agency-agents` 只存原文；中文展示与本地运行适配使用独立版本化资源，后续批次实现 |
| 中文展示 / 运行适配 | 中文资源 `584049` bytes，SHA-256 `b2122872c03981332854d1afc2c425ad5d63c59ce8a4ec4b9ae3d852d83c45c6`；运行适配 `taiji-zhinang-runtime-v3` 依次组合逐字节原文、角色级 limitations/adaptation_note 和最终通用规则，覆盖语言、资质、证据、权限及角色文本不得自行触发多 Agent 的边界 |

首批只提交 PRD、台账、导入器、只读加载器和双哨兵测试。完整上游原文、`divisions.json`、许可证和生成清单作为独立资源批进入工作树，按完整文件字节计量并保持每批小于 4 MiB；任一新增文件小于 1 MiB。中文展示和运行适配不与 3.87 MB 原文批次混装。

加载器只接受固定仓库、固定提交和固定目录版本；校验清单数量、角色 ID、分区、相对路径、字节数、SHA-256、UTF-8/LF front matter 及磁盘 Markdown 文件集合。缺失、改写、额外文件、路径穿越或版本冲突返回稳定错误码和不含物理路径的中文消息。它不会运行上游安装脚本，也不读取任意远程 URL。

为逐字节保存上游原文，`.gitattributes` 仅对固定语料目录关闭空白差异检查，文本 diff 仍保持可读。只有 `design/design-persona-walkthrough.md` 使用 `conflict-marker-size=64`：该文件 SHA-256 为 `8def8c73df7f79a61704e7353b85b915afa31c4a9fa1100fbb9220c50d6a1c55`，第 118 行的 `=======` 位于 fenced `VERDICT` 教学模板内；其余 272 个角色仍使用默认冲突标记检查。

## F01–F12 实施状态

状态只使用“未开始 / 实施中 / 待验收 / 通过 / 阻塞”。“通过”必须绑定对应 A 用例证据，底座单测不外推为产品功能通过。

| 功能 | 状态 | 当前证据 / 下一步 |
| --- | --- | --- |
| F01 智囊库入口与外壳 | 未开始 | 阶段 4 接入现有导航与品牌 |
| F02 内置角色目录 | 实施中 | 273 个固定上游角色加 1 个本地角色的中文展示层已入库并与来源逐项对账；完整目录接口/UI 留待后续阶段 |
| F03 分类、搜索、精选与全部角色 | 实施中 | 正交范围/领域/视图/搜索筛选、24 条分页及固定 6 精选接口契约已通过；阶段 4 实现 UI |
| F04 我的收藏 | 实施中 | profile 权威状态、原子写入、进程内并发保护、重启等价重读及下架取消接口已通过；阶段 4 实现 UI |
| F05 最近使用 | 实施中 | 从真实受理元数据选取最新可见执行 tip，压缩展示快照与可继续 tip 分离，删除后按同角色其他独立任务回溯；阶段 4 实现 UI |
| F06 角色卡片及详情 | 实施中 | 卡片安全列表投影与完整当前详情 API 已通过；阶段 4 实现卡片与详情 UI |
| F07 完整角色说明与来源 | 实施中 | 当前目录详情可查原文、版本、改编、固定 source URL 和完整 MIT 文本；历史详情沿用任务快照且不返回内部提示词；UI 留待阶段 4 |
| F08 示例任务 | 实施中 | 服务端原子保存新任务草稿；前端按 SID 串行 debounce/立即/切换前/clear 四种写入，切换前等待旧写入后提交最新文本；原生 `File` 仅按 SID 保存在内存，成功切换隔离、失败保持原任务状态；示例 UI 与保存失败浏览器交互未实现 |
| F09 使用此智囊 | 待验收 | `role_id/catalog_version/request_id` 幂等创建、参数冲突和非法环境拒绝已通过 HTTP 测试；可见入口待阶段 4 |
| F10 持续角色上下文 | 待验收 | sync、streaming、Gateway、缓存、自愈、压缩、重启、复制/分支/清空均保存或重放固定快照；浏览器端标签待阶段 4 |
| F11 既有聊天与成果链路 | 未开始 | 阶段 5 mock Provider、附件与真实文件产物验收 |
| F12 完整状态与可访问性 | 未开始 | 阶段 4/5 状态、键盘、响应式及错误注入 |

## A01–A17 验收台账

| 用例 | 状态 | 计划证据 / 当前边界 |
| --- | --- | --- |
| A01 来源和范围 | 未开始 | 真实 WebUI 导航、品牌与排除项浏览器证据 |
| A02 目录对账 | 通过 | 273 个固定源加 1 个本地角色与中文层 274 项逐项对账；必填字段、六分类、能力 3–5、交付示例 2–3、固定提交和有效提示词均由聚焦测试验证 |
| A03 筛选分页 | 实施中 | 中英文/标签搜索、领域、精选、收藏、最近正交与稳定 24 条分页接口契约通过；500 条 30 次性能实测及浏览器竞态留待后续 |
| A04 收藏持久化 | 实施中 | 两个独立 Store 实例并发写、新实例重读、profile 隔离、幂等取消和 `os.replace` 故障前后磁盘不变已通过；多窗口与真实服务重启留待 UI 阶段 |
| A05 使用事实 | 实施中 | sync、streaming 与 Gateway 受理时间/请求去重已通过；最近查询分离展示快照与执行 tip，并重验 profile、角色、snapshot 与可见性，tip→branch→duplicate→全删回退通过；产品 UI 待验收 |
| A06 详情真实性 | 实施中 | 当前详情从固定目录生成，包含原文、改编、版本、完整 MIT 和固定来源；历史详情从会话快照读取，二者都排除内部有效提示词；卡片/详情 UI 未实现 |
| A07 示例与草稿 | 实施中 | Node 24 实际函数测试以 deferred Promise 证明同 SID 的 debounce/立即/切换前/clear 串行，旧请求不能反写最新文本或 clear，失败链允许后续显式重试；原生 `File` 不进入 JSON，成功新建/切换按 SID 隔离并能切回同一对象，创建或切换前保存失败保持原 SID/文本/附件；真实浏览器提示和模型调用计数待后续 |
| A08 创建幂等 | 通过 | HTTP 顺序重放、两线程并发、同请求不同参数冲突均通过；持久请求在重启等价读取时先于当前目录解析，即使目录版本变化或角色移除仍返回原 SID；坏 sidecar 逐文件隔离，有目标时继续重放、无法证明 request_id 不存在时 409 fail-closed；带草稿关联一致，空角色任务沿用不落盘生命周期 |
| A09 环境不被替换 | 待验收 | 无模型创建、非法档案/项目/工作区拒绝、创建 body 不得覆盖请求活动 profile、合法命名 profile 可创建、配置文件字节不变及工具不增权规则通过聚焦测试；完整可见流程待后续 |
| A10 真正进入模型 | 通过 | 两个角色唯一哨兵经真实 `run_agent.AIAgent` 到 mock `client.chat.completions.create`，sync 与 streaming 各验证两轮：所选出现、另一角色与旧 personality 不出现；编排角色在 `delegate_task` schema 保留时，覆盖性单角色禁令位于上游委派指令之后并进入实际 SDK 请求；医疗编码和法律审阅两个资质敏感角色的 limitations/adaptation_note 位于原文声明之后、最终通用规则之前并进入实际 SDK 请求 |
| A11 生命周期一致 | 待验收 | 第二轮、缓存复用、两条 credential self-heal worker 的重建后真实 SDK 请求、压缩父快照/metadata-only tip、重启、复制/分支/清空通过；可见标签和重试 UI 待后续 |
| A12 更新与损坏 | 实施中 | 目录更新/角色移除不影响旧任务快照和幂等重放；快照 schema v2 canonical digest 篡改在 Provider 前 fail-closed，损坏任务列表隔离；下架收藏按保留名称/分类/标签/摘要筛选，详情不伪造原文，HTTP 取消后失效条目消失已通过；可见 UI 待验收 |
| A13 聊天与产物 | 未开始 | mock Provider 下附件、流式、取消、失败恢复和真实产物 |
| A14 状态与键盘 | 未开始 | 故障注入、Tab/Escape/焦点和各视口 |
| A15 安全边界 | 实施中 | 清单寻址、路径穿越、完整快照摘要损坏、角色不增权、角色文本不得自行触发 spawn/delegate/team、角色级资质边界实际注入、公开字段白名单、内部提示词不泄露和历史角色说明跨 profile 404 已覆盖；可见 HTML/链接行为待 UI 阶段 |
| A16 产品回归 | 实施中 | 阶段 3 智囊目录/会话/收藏/最近关联 79 项通过，统一 `scripts/verify.sh --full` 原入口再次通过；历史关联集的 1 项未修改 boot.js 词法断言和 13 项 Gateway 签名失配均为下方已隔离基线；完整可见 UI 回归待后续 |
| A17 本地可用与性能 | 未开始 | 外网阻断、长内容及 500 条连续 30 次 p95 实测 |

浏览器视口固定覆盖 PRD 的五种尺寸，另测现有外壳断点 `900/901/902` 与桌面断点 `1023/1024/1025`，并执行 200% 缩放。浏览器、截图、可访问性和视觉回归当前均为**未验证**。

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

### 既有 Gateway 基线问题

13 个失败 node ID 均位于 `tests/test_webui_gateway_chat_backend.py`：`test_gateway_runs_accumulates_raw_internal_and_filtered_public_final_text`、`test_gateway_runs_short_buffered_delta_is_not_duplicated_by_completed_output`、`test_gateway_runs_collects_private_image_candidate_without_public_path`、`test_gateway_runs_terminal_events_keep_cancel_and_error_semantics` 的两个参数项、`test_gateway_runs_stops_orphan_when_started_session_id_does_not_match`、`test_gateway_runs_same_name_tool_completion_matches_stable_id`、`test_gateway_run_incomplete_event_stream_stops_run_and_returns_error`、`test_gateway_runs_user_cancel_returns_cancelled_even_with_partial_text`、`test_gateway_run_reasoning_uses_stateful_cross_chunk_filter`、`test_gateway_runs_server_cancelled_preserves_cancelled_outcome`、`test_gateway_runs_partial_eof_is_not_completed`、`test_gateway_runs_failed_discards_buffered_reasoning`。源码有 12 个 `brand_token_tail=` 调用点，其中参数化终止事件产生两个 node ID，所以共 13 项；错误均为 `TypeError: _stream_gateway_run_events() got an unexpected keyword argument 'brand_token_tail'`。

本批 diff 未修改 `_stream_gateway_run_events` 的定义。为排除导入路径或猴子补丁影响，使用 `git archive HEAD` 解出独立 `/private/tmp/taiji-zhinang-head-baseline`，以相同隔离环境单独执行首个代表 node ID；纯 `HEAD@266dfd73` 同样得到上述 TypeError。`scripts/verify.sh` 的 WebUI 952 注册计划只列品牌、模型配置、审批、专家团前端等选择器，不包含 `test_webui_gateway_chat_backend.py`，因此历史 952 通过与该问题不矛盾。本阶段不扩大范围修复该既有签名问题，也不把它表述为智囊核心阻断。

后续每批在暂存前运行聚焦测试和 `scripts/check-local-change-safety.py`，记录完整文件字节；任何 staged bytes 变化都会使原终审失效。全量 `scripts/verify.sh --full`、浏览器流程、独立前端只读审查和 Sol 暂存终审按阶段执行，未执行时不标为通过。
