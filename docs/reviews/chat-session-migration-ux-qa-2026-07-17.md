# 前端 UX QA 报告：会话资源包与历史迁移（2026-07-17）

## 状态

**带限制完成。** fresh10 真实 Electron 已覆盖 ZIP 导出/导入、图片恢复、旧 JSON 文本降级、审计确认/取消、迁移成功、修复后重新审计、二次 Apply 幂等、迁移后生产 rename/save/GET 缓存一致性、真实磁盘故障回滚、成功/失败 toast 互斥、cron 运行态空闲、窄屏和键盘关闭路径。失败态由隔离 runtime 中的真实只读产物目录触发，未向 renderer 注入迁移报告或状态。

当前无未关闭 P0/P1；未配置 axe/Lighthouse 和像素级视觉回归，明确标记为未验证。

## 主要用户目标

用户能区分“含图片的资源包”和“仅文本兼容 JSON”，能够在不接触内部文件路径的前提下导入、恢复和查看图片；当旧会话需要修复时，先看安全审计摘要，再明确确认，修复后获得可理解且可再次验证的结果。

## 信息层级与入口

- 主操作：资源包 ZIP 导出、资源包 ZIP 导入。
- 兼容操作：兼容 JSON 导出、兼容 JSON 导入，并明确标注“仅文本”。
- 条件操作：只有只读审计发现问题时显示“旧会话需要修复”卡片。
- 反馈：审计中、需处理、修复中、无需修复、失败/回滚均有可读状态；状态不只依赖颜色。
- 安全边界：页面只显示统计、原因和是否已备份，不显示备份目录、产物存储路径或绝对路径。

## 已验证主路径

1. 设置页出现四个名称和用途清晰、可发现的会话入口。
2. 导出含图片 ZIP，再导入为新会话；新 `session_id`、新 `artifact_id` 与原值不同。
3. 导入后媒体接口返回 200，图片完成真实解码并在会话内联显示，不以加载占位判成功。
4. 导入旧 JSON 后只保留文本，旧 `MEDIA:` 路径不获得媒体权限。
5. 审计发现旧状态后点击修复；取消确认不修改审计结果。
6. 确认修复后显示“已修复”和“已创建备份”的安全回执及 success toast；下一次手动审计或重新进入设置页后收敛为“无需修复”，按钮禁用。
7. 第二次 Apply 修改数为 0。
8. 将产物目录改为只读后真实 Apply 失败；卡片、badge、aria-live 和 error toast 一致显示失败，不出现“修复完成/无需修复”，报告只显示安全计数和回滚结论，不显示内部路径，sidecar、缓存和 `state.db` 校验和恢复，且没有遗留新增产物。
9. 先通过生产 GET 将旧会话放入服务端缓存，迁移后通过生产 rename/save/GET 再次持久化；新 artifact 保留，旧 `MEDIA:`、`privacy_context` 和遗留 taint 均未复活。
10. 隔离报告只提供数量和 `manual_review_required` 状态，页面显示“隔离待人工处理 N 项”，不显示路径，也不自动删除。
11. 窄屏下入口、迁移卡、操作按钮和文本不横向溢出。
12. dialog 中按 Escape 只关闭当前 dialog，不连带关闭设置面板。

## 自动化与真实环境证据

| 检查 | 结果 |
|---|---|
| Phase 5 目标契约测试 | 193 passed，覆盖资源包安全校验、四阶段清理隔离、路由、迁移 dry-run/apply/幂等/回滚/stop-on-failure、产物 unlink 失败隔离、缓存撤销、profile fail-closed、cron/worker 生命周期、SessionDB schema guard、journal 并发恢复和 UI 结果分类契约 |
| Agent SessionDB+WAL 聚焦回归 | 239 passed（本轮可复跑：`test_hermes_state.py` 与 `test_hermes_state_wal_fallback.py`） |
| Agent 关联回归（含 WebUI 幂等扩展） | 244 passed（此前实时执行；与 239 项聚焦选择集范围不同，不相加、不作为同一总计） |
| WebUI cron/title/compression/index/Gateway 离线关联回归 | 292 passed、2 failed、2 skipped、8 subtests passed；两项失败均为既有静态断言漂移：英文 toast 精确文本与“整个 streaming.py 不得含中文”，本轮相关 diff 未改这些断言所针对的文案。明确排除依赖外部常驻 WebUI 的 `tests/test_sprint3.py`；误纳入时出现 11 个 HTTP 500/503 环境失败 |
| `npm run lint:runtime` | 通过；worktree 复用主工作区同项目已安装的 ESLint 二进制 |
| JavaScript 语法检查与 Python `py_compile` | 通过 |
| `git diff --check` | 通过 |
| 真实 Electron smoke | fresh10 通过；Apply 6341ms、第二次 Apply 4ms、真实失败回滚 6332ms；成功 toast 与失败 error toast 均通过互斥断言；迁移前后 `/health?deep=1` 均为 HTTP 200、`status=ok`、`active_runs=0`，`/api/crons/status` 的 running 数均为 0 |
| 媒体真实可用性 | 通过；后端响应 200，DOM 图片 `complete/naturalWidth` 通过 |
| DOM 内部路径检索 | 0 |
| 自动可访问性扫描 | 未验证；项目未配置 axe/Lighthouse |
| 像素级视觉回归 | 未验证；已执行真实截图人工目视检查 |

结果清单：`hermes-local-lab/sources/hermes-webui/output/phase5-session-bundle-migration-fresh10/electron-session-bundle-migration-result.json`，run id 为 `taiji-bundle-migration-electron-W05R1c`。Electron、Agent、WebUI 三个 smoke PID（96749、96790、96818）均已停止，隔离临时目录已删除；上一轮证据目录已删除。

截图证据：

- `01-settings-audit.png`：四个独立入口与审计卡。
- `02-bundle-roundtrip.png`：ZIP 往返后图片真实加载。
- `03-legacy-json-text-only.png`：旧 JSON 仅文本降级。
- `04-migration-applied.png`：真实 Apply 成功回执显示“已修复”、已创建备份及绿色 success toast；后续审计收敛为“无需修复”。
- `05-migration-failure-report.png`：真实磁盘故障触发的失败/回滚安全报告和红色 error toast；无成功文案。
- `06-narrow.png`：窄屏布局。

## 可访问性检查

- 文件输入由可见按钮触发，按钮具有明确 accessible name。
- 迁移状态使用 status/live region；确认 dialog 支持键盘和 Escape。
- 修复中和无需修复状态禁用 Apply，避免重复提交。
- 错误、成功和需处理均有文字，不只靠颜色。
- 窄屏主要操作仍可见、可点击，没有互相遮挡。
- Tab 全链路与自动 axe 扫描：未验证。

## 本轮发现并修复的问题

| 严重程度 | 问题 | 处理与复验 |
|---|---|---|
| P1 | dialog Escape 事件继续冒泡，导致 dialog 与设置面板同时关闭 | 在 dialog 捕获处理器停止同事件继续传播；真实 Electron 复验只关闭当前 dialog |
| P1 | smoke 在图片仍显示加载占位时误判导入成功 | 增加媒体接口 200、图片 `complete` 和 `naturalWidth` 门禁；真实 Electron 复验图片已解码 |
| P1 | Apply 后页面沿用旧审计徽标，可能仍显示“需处理” | 成功态改为以修复后审计为真值、叠加本次 Apply 的安全计数回执；真实 Electron 复验成功回执，下一次审计收敛为“无需修复” |
| P1 | 长连接 SSE 持有共享锁会让迁移写锁等待约 38 秒并可能饥饿 | SSE 仅在有限状态快照阶段持有共享锁，长连接本身不持锁；新增 writer-preference、并发快照和真实 Electron 复验 |
| P1 | 失败截图曾由 renderer 注入受控迁移状态，不能证明真实回滚链 | 删除 renderer/state 注入，改由只读产物目录触发真实后端失败；复验回滚校验和、无新增产物和故障后可再次审计 |
| P1 | Apply 改写磁盘后，SESSIONS 中旧对象仍可能被 GET/后续 save 使用并复活旧 taint/MEDIA | 在独占屏障内撤销并驱逐旧对象；fresh10 通过生产 GET 预加载、迁移、生产 rename/save/GET 验证未复活 |
| P1 | 资源包导入清理异常被吞，可能留下正式 sidecar/index/manifest/artifact | 改为逐项清理、验证、私有隔离并返回 `rollback_incomplete`；四阶段故障注入均通过 |
| P1 | Apply HTTP 200 时无条件显示“修复完成”，即使 `failed>0` 或复审仍需修复/隔离 | 以 apply 计数与 fresh audit 共同分类 error/warning/success；Node 行为测试和 fresh10 真实成功/失败 toast 互斥通过 |
| P1 | 新产物 unlink 持续失败后仍从 manifest 删除并返回成功，正式目录留下未登记孤儿 | 删除失败残留移入不可授权私有隔离区，验证 manifest/目录集合与旧文件哈希后仍显式抛错；迁移标 `rollback_incomplete`，两级故障注入通过 |
| P1 | 一个会话失败后仍继续迁移后续会话 | 首失败立即停止；完整和不完整回滚两种测试均证明后续 sidecar/DB/artifact 不变 |
| P1 | HTTP handler 释放共享锁后，已有普通/gateway worker 仍可与 Apply 并发写状态 | handler 启动前保留可转移 worker lease，worker finally 释放；真实 artifact commit→Session.save 暂停、start失败、异常和取消测试通过 |
| P1 | manual compression、title、checkpoint 和 session-index 后台线程可绕过 handler lease，并在 Apply 中写 Session/DB/artifact | 统一使用可转移 worker lease，并在 `Session.save`、Artifact Registry mutation、SessionDB `_execute_write` 三个实际 sink 再加迁移 guard；真实 writer 等待测试和此前包含 WebUI 幂等扩展的 244 项 Agent 关联选择集通过 |
| P1 | journal-only metering 若持迁移 lease 会造成 worker 自等待；恢复整个 session 目录又可能覆盖迁移期间追加的 journal | journal emitter 明确不持状态 lease；恢复逐项保留 `_turn_journal`/`_run_journal`，真实 metering 并发测试验证 sequence 连续且事件零丢失 |
| P1 | `/api/crons/run` 以裸线程启动 Agent 子进程，可在 Apply 中写所选 profile 的 `state.db` | canonical 路由及 singular alias 均纳入屏障，使用可转移 worker lease 覆盖子进程完整生命周期；并发测试证明 Apply 等待现有 cron、writer 排队后新 cron 不启动、取消和 start 失败释放 lease/运行标记 |
| P2 | `SessionDB.__init__` 的 schema commit 未经过进程内写 guard | `_init_schema` 统一进入可选 host guard；WebUI exclusive 阻塞和 schema 失败释放通过，standalone Agent 默认仍为 `nullcontext` |

## 当前问题分级

| 严重程度 | 问题 | 状态 |
|---|---|---|
| P2 | 未配置 axe/Lighthouse 自动可访问性扫描 | 未验证；本次已做键盘、语义和焦点路径人工/脚本检查 |
| P3 | 未配置像素级视觉基线 | 未验证；六张真实截图已人工目视检查 |

## 剩余风险与结论

当前 Phase 5 主流程没有已确认的 P0/P1 阻断。资源包图片恢复、历史审计、用户确认、备份提示、幂等修复、迁移后缓存一致性、公开报告、Agent/cron 运行态回到空闲、成功/失败反馈互斥和真实磁盘故障回滚均在 fresh10 真实 Electron 中验证；资源包导入清理隔离、新产物 unlink 失败隔离、stop-on-failure、多 profile 防误写、恢复中途失败、所有已识别状态写 worker、SessionDB 建库/实际写 sink 和 journal 并发恢复由自动化覆盖。关联回归仍有 2 项与本轮 diff 无关的既有静态断言失败，且自动 a11y、像素回归及 Electron 资源包导入故障注入尚未配置，因此本报告结论保持“带限制完成”，不能扩展解释为整套六阶段发布已完成。
