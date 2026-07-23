# 专家团 V3 Image2.0 视觉基线

## 生成方式

- 模型：内置 Image2.0。
- 模式：以用户提供的 5 张现有系统截图作为风格与非专家页面骨架参考，逐状态独立生成高保真桌面稿。
- 原则：只调整专家团门户、团队详情、发起弹窗和专家团工作台；全局导航、最近会话、聊天输入区及其他页面保持原产品风格。
- 代码实现以 `feature-contract.md` 和真实后端合同为准。生成图中的动态阶段数、示例标题或少量文字漂移不作为业务真相源。

## 统一提示词约束

> 高保真企业级 macOS 桌面端中文 UI，延续太极智能体现有浅蓝电力场景、白色半透明卡片、深海军蓝标题、青色主操作、细浅蓝描边和克制阴影。保留非专家团页面骨架，只重设计专家团相关区域。页面简洁、留白充分、每个状态只有一个主任务，禁止旧任务/成果/过程三页签、固定 0/5 进度、调试字段、深色赛博风与多主按钮竞争。

## 17 帧状态映射

| 文件 | 设计状态 | 实现状态/入口 |
|---|---|---|
| `01-portal.png` | 专家团门户 | `GET /api/expert-teams/catalog` |
| `02-team-detail.png` | 团队详情 | 门户内可访问对话框 |
| `03-launch-task.png` | 发起任务 | `POST /api/expert-teams/start` |
| `04-brief-sources.png` | Brief 与资料 | `collecting_required/optional` 与资料增删 |
| `05-ready-to-start.png` | 规格已确认 | `ready_to_generate` |
| `06-generating.png` | 生成中 | `starting/generating` |
| `07-stage-input.png` | 阶段补充 | `awaiting_stage_input` |
| `08-stage-review.png` | 阶段复核 | `awaiting_review` |
| `09-revision-editor.png` | 修改意见编辑 | `stage/revise` |
| `10-revising.png` | 按意见修订 | 生成态的修订语义 |
| `11-docx-auto-validation.png` | DOCX 自动检查 | `delivery_validation_required` |
| `12-office-entry.png` | Office 验收入口 | 文档门通过、Office 门待处理 |
| `13-office-drawer.png` | Office 可信复核 | 身份、证据、9 项检查与结论 |
| `14-final-delivery.png` | 最终交付 | `completed` 且三道门通过 |
| `15-legacy-readonly.png` | 历史任务 | 老 schema 只读 |
| `16-office-failed.png` | Office 不通过 | 结构化问题与返修 |
| `17-component-state-board.png` | 状态与组件板 | 设计 token 与状态参考 |

全部图片保存在 [`reference`](./reference/)；它们是实现参考，不替代真实 Electron 截图验收。
