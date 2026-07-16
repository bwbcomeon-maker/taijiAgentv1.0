# 会话资源包与历史迁移功能契约（2026-07-17）

范围：会话安全导出/导入、旧 JSON 文本兼容、旧会话只读审计与用户确认迁移。本契约不扩大到会话主链、图片生成主链或发布总门禁的其他阶段。

## 用户目标

用户能够把一段会话连同已注册图片导出为可校验资源包，在新会话中安全恢复；系统发现旧会话问题时只提示、不静默修改，用户确认后先备份再修复，并能看见不包含内部路径或凭据的结果报告。

## 不变量

- 正式资源包固定包含 `session.json`、`manifest.json` 和 `artifacts/`；资源包内路径、文件类型、数量、大小、压缩比、MIME、魔数和哈希必须全部通过校验。
- 导入总是创建新的 `session_id` 和新的 `artifact_id`，不继承原会话所有权或内部路径。
- 消息中的公开 artifact 描述必须与 manifest 一一精确对应；未知字段、未知文件、未引用文件和不完整描述均拒绝导入。
- 旧 JSON 仅导入文本；其中的绝对路径、artifact 路径和 `MEDIA:` 文本不能建立媒体权限。
- 启动阶段只执行只读审计；任何历史数据修改都必须经过用户明确确认，并在备份成功后执行。
- 迁移只接受可精确证明的修复：turn id、顺序、内容或现存缓存文件任一证据不足时跳过，不猜测、不改写用户原文。
- 单会话迁移失败时恢复该会话 sidecar、数据库语义行和本批新增产物；不能删除其他会话或更早存在的产物。
- 本批新增产物删除失败时不得从 manifest 静默移除后遗留正式目录孤儿；残留必须移入不可授权的私有隔离区，旧产物文件、哈希和 manifest 记录保持不变，并将本批标记为 `rollback_incomplete`。
- 任一会话迁移失败后立即停止本批次；后续会话的 sidecar、数据库行和产物校验和必须保持不变，无论当前回滚是否完整。
- 迁移成功或回滚后，在独占屏障释放前撤销并失效受影响的内存 Session；后续 GET 必须从磁盘重载，被撤销的旧对象不得再次 `save()` 复活旧隐私状态或 `MEDIA:`。
- 明确带 profile 的 sidecar 只能访问该 profile 对应的 `state.db`；当前数据库不匹配或无法解析时 fail-closed，不写入其他 profile。
- 公共 API 和页面报告不包含 `backup_path`、`storage_path`、绝对路径、原始工具参数或凭据，只返回安全计数、状态、原因码和 `backup_created`。
- Apply 幂等：第一次成功修复后再次执行修改数必须为 0。
- 迁移期间 Session sidecar、`state.db` 和 gateway 快照必须共享读写屏障：有限读操作看到迁移前或提交/回滚后的完整状态，不能看到半迁移状态；长连接 SSE 不得长期占用读锁阻塞迁移。
- 已启动的普通/gateway worker 持有可跨线程释放的共享 lease 直到正常结束、取消或异常；Apply 必须等待，writer 排队后新 worker 不得启动，`Thread.start()` 失败也必须释放 lease。
- manual compression、首次/自适应 title、周期 checkpoint 和 session-index rebuild 等会写迁移真值层的后台线程必须走同一 worker lease；`Session.save`、Artifact Registry mutation 和 SessionDB `_execute_write` 作为实际写 sink 必须再次 fail-safe 加锁。
- manual cron 的路由分类和 worker lease 必须覆盖 Agent 子进程的完整生命周期；已有 cron 未结束时 Apply 必须等待，writer 已排队或持锁时新 cron 不能启动。
- SessionDB schema 初始化/提交与普通写事务使用同一可选 process-local guard；standalone Agent 未安装 hook 时仍为 `nullcontext`。
- 只追加 run/turn journal 的 metering emitter 不持 session-state lease；迁移恢复不能替换活动 `_turn_journal`/`_run_journal`，并发追加后 sequence 必须连续且事件零丢失。
- 资源包导入回滚逐项清理并验证 sidecar、index、manifest 和 artifact；任一清理异常时正式命名空间不得留有可加载数据，残留和安全回执进入不可由 Session、search 或 media 授权访问的私有隔离区，并返回 `rollback_incomplete`。

## 功能契约

| 能力 | 数据/API | UI 入口 | 用户反馈 | 错误/安全处理 | 自动化 | 真实 Electron | 状态 |
|---|---:|---:|---:|---:|---:|---:|---|
| ZIP 资源包导出 | 是 | 是 | 是 | 字段白名单、校验和 | 通过 | 通过 | 通过 |
| ZIP 资源包导入 | 是 | 是 | 是 | ZIP Slip、超限、哈希/MIME/魔数、未知字段 fail-closed | 通过 | 通过 | 通过 |
| 新会话/新产物 ID 重绑定 | 是 | 导入后可见 | 是 | 跨会话所有权隔离 | 通过 | 通过 | 通过 |
| 资源包导入失败回滚 | 是 | 错误反馈 | 是 | 逐项验证；异常隔离并返回 `rollback_incomplete` | 通过（四阶段故障注入） | 未做 Electron 导入故障注入 | 带限制通过 |
| 旧 JSON 文本兼容 | 是 | 独立入口 | 是 | 不授予旧路径媒体权限 | 通过 | 通过 | 通过 |
| 启动只读审计 | 是 | 问题存在时显示卡片 | 是 | 不创建 artifact 根、不改历史文件 | 通过 | 通过 | 通过 |
| Dry-run 历史迁移 | 是 | 审计结果承载 | 是 | 默认只读 | 通过 | 通过 | 通过 |
| 用户确认、备份后 Apply | 是 | 是 | 是 | 明确确认、逐会话回滚、读写屏障；业务失败不得显示成功反馈 | 通过 | 通过（含真实磁盘故障与成功/失败 toast 互斥） | 通过 |
| Apply 幂等 | 是 | 修复后显示“无需修复”并禁用按钮 | 是 | 第二次修改数 0 | 通过 | 通过 | 通过 |
| 迁移后缓存一致性 | 是 | 透明 | 正常 rename/save/GET | 撤销旧对象并从磁盘重载 | 通过 | 通过 | 通过 |
| worker 生命周期屏障 | 是 | 透明 | Apply 等待已有 worker/cron | start/异常/取消 exactly-once 释放；schema 与实际写 sink 二次保护 | 通过 | fresh10 Agent/cron 前后状态均为空闲 | 通过 |
| 隔离项报告 | 是 | 是 | 仅显示数量和待人工处理状态 | 不公开路径，不自动删除 | 通过 | 静态契约覆盖 | 通过 |
| 公共迁移报告 | 是 | 是 | 是 | 内部路径和凭据字段不投影 | 通过 | 通过 | 通过 |

## 状态矩阵

| 状态 | 页面表现 | 可执行操作 |
|---|---|---|
| 审计中 | 状态区显示检查中 | Apply 禁用 |
| 无问题 | 显示“无需修复” | Apply 禁用；导入导出可用 |
| 发现可修复问题 | 显示安全摘要和问题计数 | 可点击修复并进入明确确认 |
| 用户取消 | 保留原审计结果 | 不修改文件，可再次确认 |
| 修复中 | 显示处理中 | Apply 禁用，防止重复提交 |
| 修复成功 | 显示由本次 Apply 回执和修复后只读审计共同组成的安全结果；再次审计或重进设置页后收敛为“无需修复” | Apply 禁用 |
| 部分跳过 | 报告显示跳过数和稳定原因码 | 用户可按原因人工处理 |
| 批次失败 | 卡片、badge、aria-live 与 error toast 一致显示失败和回滚结果，不显示路径或任何“修复完成/无需修复”成功文案 | 可在排除原因后重试 |
| 存在隔离项 | 显示“隔离待人工处理 N 项” | 自动 Apply 禁用；不自动删除隔离数据 |

## 安全拒绝条件

- ZIP 中出现绝对路径、`..`、反斜杠路径、重复大小写文件名、符号链接、非普通文件、加密条目或预期之外的文件。
- 文件数、单文件、总解压大小或压缩比超限。
- manifest/session/messages/artifact 校验和不一致。
- artifact 的公开描述缺字段、与 manifest 不一致、MIME 与后缀不匹配，或文件魔数不匹配。
- API 请求不是 ZIP、超过请求上限，或迁移 Apply 缺少显式 `confirm: true`。
- 历史 user 回填缺少 journal、turn id、顺序或内容精确证据；历史缓存图片不存在或不在受信缓存范围。
- `privacy_context` 没有可定位的 user turn、不是紧邻来源轮次，或 profile 对应数据库不是当前数据库。

## 验收结论

阶段 5 的功能契约已由 193 项目标契约测试、本轮可复跑的 Agent SessionDB+WAL 聚焦回归 239 passed，以及 fresh10 真实 Electron 主路径覆盖。另有此前实时执行的 Agent 关联选择集 244 passed；该选择集包含 WebUI 幂等扩展，与 239 项聚焦选择集范围不同，两者不得相加或视为同一总计。fresh10 通过生产 GET 预加载旧 Session，迁移后再走生产 rename/save/GET，证明旧隐私状态和 `MEDIA:` 没有从缓存复活；同时通过只读产物目录触发迁移中途磁盘写入失败，并验证失败页只有 error toast、卡片和 badge，不出现“修复完成/无需修复”，批次回滚后 sidecar/缓存/`state.db` 校验和恢复、未遗留新增产物，且故障解除后重新审计仍可修复。正常 Apply 为 6341ms，第二次幂等 Apply 为 4ms 且修改数为 0，失败回滚 Apply 为 6332ms；迁移前后健康接口均为 HTTP 200、`status=ok`、`active_runs=0`，cron running 数均为 0。另有自动化故障注入覆盖“旧产物与本批新产物并存且新文件 unlink 持续失败”：新残留被移入不可授权隔离区，正式 manifest/目录集合只保留旧产物，旧文件哈希和授权不变，迁移明确返回 `rollback_incomplete`。资源包导入失败的 sidecar/index/manifest/artifact 四类清理故障和隔离由后端自动化覆盖，尚未在 Electron 中单独执行导入故障注入，因此该子项保留“带限制通过”。
