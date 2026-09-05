# 太极智囊实施与验收台账

> 状态更新：2026-09-05（阶段 0/1 首批候选）
> 功能契约：[太极智囊 PRD](../requirements/2026-09-05-taiji-zhinang-prd.md)

## 当前状态卡

| 项目 | 已验证状态 |
| --- | --- |
| 物理仓库 / Git common dir | `/Users/bwb/Documents/工作/taiji-agentv1.0` / `.git` |
| 开发线与基线 | `main@18a607bc96a5689b184e4631a9606ce1cbb24e1e`；刷新后相对 `origin/main` 为 `0 0` |
| 写入边界 | 当前实施者是共享工作树及 Git index 唯一写入者；其他协作者只读 |
| 当前证据层 | 本地源码和 `/private/tmp` 导入试验；尚未形成随产品分发的完整角色资源、运行接口或浏览器证据 |
| 已完成 | 工具链绑定；固定上游检出；273 个源角色递归清单试导入；只读加载、完整性/版本校验、详情读取和安全错误；双哨兵单测 |
| 未完成 | 完整源资源入库、中文展示/运行适配、F01–F12 产品闭环、A01–A17 完整验收、前端 UX QA |
| 当前出口 | 阶段 0 可进入后续实施；阶段 1 底座代码待暂存终审，完整来源仍须下一批入库后才能宣称完成 |

## 工具链与隔离环境

| 工具 / 边界 | 固定来源与结果 |
| --- | --- |
| Python | `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`，Python 3.13.6；`pytest 9.0.3` |
| Node | `/Users/bwb/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`，Node v24.19.0 |
| npm | 上述 Node 执行 `/Users/bwb/.hermes/node/lib/node_modules/npm/bin/npm-cli.js`，npm 11.19.0 |
| Playwright | `NODE_PATH=/Users/bwb/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules`；`require.resolve('playwright')` 指向该固定目录 |
| Chromium | 使用现有 `~/Library/Caches/ms-playwright/chromium-*`，不下载或启动默认浏览器 |
| 自动化状态根 | `/private/tmp/taiji-zhinang-qa/state`；同时固定 `HERMES_WEBUI_STATE_DIR`、`HERMES_HOME`、`HERMES_BASE_HOME` |
| 自动化配置 / 工作区 | `/private/tmp/taiji-zhinang-qa/state/config.yaml` / `/private/tmp/taiji-zhinang-qa/workspace`；固定 `HERMES_CONFIG_PATH` 与 `HERMES_WEBUI_DEFAULT_WORKSPACE` |
| 服务与 Provider | 运行时选择空闲 loopback 端口；mock Provider 只监听 loopback；同时设置 `TAIJI_WEBUI_TEST_NETWORK_BLOCK=1` 和现有测试使用的 `HERMES_WEBUI_TEST_NETWORK_BLOCK=1` |
| 凭据隔离 | 复用 `tests/conftest.py` 的完整 Provider、AWS、记忆、消息、浏览/搜索与 GitHub 凭据前缀剥离清单；真实 Provider/OAuth 不进入自动化 |

说明：当前纯目录单测用 `--noconftest`，避免启动无关 HTTP 服务；它只证明目录纯函数契约。后续接口和浏览器验收必须启用上述独立服务、状态、配置、工作区、端口及 mock Provider，不能沿用日常用户状态。

## 固定上游与批次门禁

| 项目 | 结果 |
| --- | --- |
| 上游 | `https://github.com/msitarzewski/agency-agents` |
| 固定提交 | `af128a92888fd7d7c389b6cb37f1820be1b3cd9d`，本地检出为 detached、clean |
| 许可 | `MIT License`；`Copyright (c) 2025 AgentLand Contributors` |
| 递归范围 | `divisions.json` 的 18 个分区；分区目录下 273 个 Git 跟踪 Markdown 文件 |
| 原文规模 | 3,870,844 bytes；单个源文件均小于 1 MiB |
| 试导入 | `/private/tmp/taiji-zhinang-source-smoke`，273 个角色加清单、分区和许可，共 276 个文件；`--check` 已通过 |
| 稳定身份 | 上游角色为 `agency:<source_path 去掉 .md>`；目录版本 `agency-agents-af128a92888f-source-v1`；原文字节数及 SHA-256 写入清单 |
| 存储分层 | `upstream/agency-agents` 只存原文；中文展示与本地运行适配使用独立版本化资源，后续批次实现 |

首批只提交 PRD、台账、导入器、只读加载器和双哨兵测试。完整上游原文、`divisions.json`、许可证和生成清单作为独立资源批进入工作树，按完整文件字节计量并保持每批小于 4 MiB；任一新增文件小于 1 MiB。中文展示和运行适配不与 3.87 MB 原文批次混装。

加载器只接受固定仓库、固定提交和固定目录版本；校验清单数量、角色 ID、分区、相对路径、字节数、SHA-256、UTF-8/LF front matter 及磁盘 Markdown 文件集合。缺失、改写、额外文件、路径穿越或版本冲突返回稳定错误码和不含物理路径的中文消息。它不会运行上游安装脚本，也不读取任意远程 URL。

## F01–F12 实施状态

状态只使用“未开始 / 实施中 / 待验收 / 通过 / 阻塞”。“通过”必须绑定对应 A 用例证据，底座单测不外推为产品功能通过。

| 功能 | 状态 | 当前证据 / 下一步 |
| --- | --- | --- |
| F01 智囊库入口与外壳 | 未开始 | 阶段 4 接入现有导航与品牌 |
| F02 内置角色目录 | 实施中 | 固定源试导入和加载器通过；完整资源、中文层、适配层及发布对账未入库 |
| F03 分类、搜索、精选与全部角色 | 未开始 | 阶段 3 接口契约和阶段 4 UI |
| F04 我的收藏 | 未开始 | 阶段 3，沿用统一状态目录与档案隔离 |
| F05 最近使用 | 未开始 | 阶段 2 记录受理事实，阶段 3 查询 |
| F06 角色卡片及详情 | 未开始 | 阶段 3 内容，阶段 4 UI |
| F07 完整角色说明与来源 | 实施中 | 固定原文、路径、提交和许可底座已验证；产品详情接口/UI 未开始 |
| F08 示例任务 | 未开始 | 阶段 2 草稿契约、阶段 4 UI |
| F09 使用此智囊 | 未开始 | 阶段 2 幂等创建 |
| F10 持续角色上下文 | 未开始 | 阶段 2 会话快照与每轮执行注入 |
| F11 既有聊天与成果链路 | 未开始 | 阶段 5 mock Provider、附件与真实文件产物验收 |
| F12 完整状态与可访问性 | 未开始 | 阶段 4/5 状态、键盘、响应式及错误注入 |

## A01–A17 验收台账

| 用例 | 状态 | 计划证据 / 当前边界 |
| --- | --- | --- |
| A01 来源和范围 | 未开始 | 真实 WebUI 导航、品牌与排除项浏览器证据 |
| A02 目录对账 | 实施中 | 273 个固定源在临时目录一一校验；随产品资源、中文详情、有效提示词和排除记录未完成 |
| A03 筛选分页 | 未开始 | 接口契约 + 500 条数据 + 浏览器竞态/分页 |
| A04 收藏持久化 | 未开始 | 写入故障、刷新/重启、多窗口和档案隔离 |
| A05 使用事实 | 未开始 | 以执行链受理事实验证去重、删除与回到任务 |
| A06 详情真实性 | 未开始 | 卡片/详情/原文/许可/改编/历史说明对账 |
| A07 示例与草稿 | 未开始 | 模型调用计数 0、草稿保存失败及普通聊天隔离 |
| A08 创建幂等 | 未开始 | 双击、并发、超时重试及参数冲突 |
| A09 环境不被替换 | 未开始 | 配置前后摘要、非法标识和工具权限 |
| A10 真正进入模型 | 实施中 | 已有两个不同哨兵的目录夹具；尚未捕获 mock Provider 每轮请求 |
| A11 生命周期一致 | 未开始 | 多轮、两类认证自愈、重试、压缩、缓存、重启和复制 |
| A12 更新与损坏 | 实施中 | 源目录缺失、改写、额外文件和版本冲突已覆盖；会话旧快照/移除收藏未开始 |
| A13 聊天与产物 | 未开始 | mock Provider 下附件、流式、取消、失败恢复和真实产物 |
| A14 状态与键盘 | 未开始 | 故障注入、Tab/Escape/焦点和各视口 |
| A15 安全边界 | 实施中 | 路径穿越 ID 和物理路径脱敏已覆盖；HTML/链接/权限/公开投影未开始 |
| A16 产品回归 | 未开始 | 普通聊天、personality、模型、草稿、同步、专家团和启动流程 |
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

后续每批在暂存前运行聚焦测试和 `scripts/check-local-change-safety.py`，记录完整文件字节；任何 staged bytes 变化都会使原终审失效。全量 `scripts/verify.sh --full`、浏览器流程、独立前端只读审查和 Sol 暂存终审按阶段执行，未执行时不标为通过。
