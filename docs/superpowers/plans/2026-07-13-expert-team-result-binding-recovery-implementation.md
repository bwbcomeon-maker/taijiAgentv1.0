# 专家团结果绑定与恢复实施计划

## Phase 0：基线与契约

- 确认 Gateway、会话、Run Journal、Turn Journal 和专家团 run 的真实写入顺序。
- 冻结不变量：强身份绑定、绑定与内容验收分离、恢复不触发模型、失败保留证据。
- 记录现有用户未跟踪文件，实施全程不触碰。

## Phase 1：TDD 覆盖主链缺口

1. 在 Gateway 测试中先增加 RED：成功写回必须产生 `submitted -> assistant_started -> completed`，两个完成事件共享 turn/stream，索引指向持久化 assistant。
2. 增加 RED：`assistant_started` 携带 assistant/user 索引与 SHA-256 摘要；保存后、completed 前中断时可用 intent 与持久化会话精确对账。
3. 实现 Gateway 生命周期补写；每次 append 独立 fail-open，不能阻断会话保存。
4. 启动前生成确定 turn id，并把专家团 run/stage/attempt/start id 写入 submitted 元数据。

验证：Gateway 定向测试；Turn Journal 生命周期测试；会话持久化回归。

## Phase 2：状态语义与恢复

1. 增加 `result_unverified` 状态和保持执行身份的转换。
2. 将“流已结束但结果未绑定”从 `fail_expert_team_execution` 改为 `mark_expert_team_result_unverified`。
3. 提取统一结果解析器：先使用 completed；没有 completed 时，仅接受摘要完全一致的 assistant_started intent。
4. “重新核验结果”通过权威 GET 对账执行，不启动模型；完成提交在同一 run 锁内接受 `generating/result_unverified`，继续校验 stream/stage/attempt，禁止中间切回 generating。
5. 旧版 `start_failed` 不自动认领；保持已有聊天结果可见，并只允许用户显式确认重新生成。

验证：错 stream、错 turn、错索引、正文摘要漂移、多候选、重复核验、内容校验失败、append 失败和保存崩溃窗口。

## Phase 3：前端交互

1. 工作台新增“结果待核验”状态，主按钮“重新核验结果”。
2. 操作只刷新权威状态，按钮具备 loading/disabled、防双击和错误反馈。
3. 重新生成只作为次操作，二次确认“可能已有结果且会产生额外模型消耗”。
4. 保留聊天结果；恢复成功后工作台自动转入“阶段成果待复核”或“草稿未通过校验”。

验证：Presenter 契约、DOM 可发现性、键盘可达、重复点击、窄窗口和桌面真实页面。

## Phase 4：对抗性测试矩阵

- 正常 Gateway 完成；本地 Streaming 回归；空回复；HTTP/SSE 错误；用户取消。
- 会话保存成功但 Turn Journal completed 写失败；completed 写成功但 done 发布前进程退出。
- 并发普通聊天插入；同会话多 stream；重复 completed；错误消息索引；消息被篡改。
- 旧 `start_failed` 不自动恢复；新 `result_unverified` 重复核验幂等。
- 绑定成功但业务关键词校验失败时必须显示 `generated_invalid`，不能显示启动失败。
- 真实桌面端从异常状态点击“重新核验结果”，确认不产生新模型请求，并检查最终工作台与聊天一致。

## Phase 5：交付门禁

- 运行所有受影响后端、前端静态契约、专家团全量测试。
- 输出中文《前端 UX QA 报告》，未执行项明确标注“未验证”。
- 检查 diff 与用户未跟踪文件隔离；仅提交本轮文件；创建本地 commit；报告 hash 与最终工作区状态。
