# 专家团单机版重构实施计划

## 目标与边界

- 仅重构专家团目录、启动、阶段复核、最终文档交付以及对应服务端契约。
- 保持聊天、定时任务、设置和桌面壳的既有信息架构不变。
- 单机版不出现企业审批身份、OIDC、审批角色、企业合同试点和 Office 证据上传。
- 保留 CAS、幂等、阶段/产物哈希、来源快照、交付绑定、DOCX 自动检查和完成事务。
- 历史企业任务不迁移，只读、可导出、可删除。

## 唯一产品契约

### 启动

前端只能提交：

- `launch_profile_id`
- `prompt`
- `session_id`
- `idempotency_key`

服务端 Launch Profile 唯一决定团队、文种、模板、阶段定义和确认策略。前端不得提交或推断 `contract_version`、模板和审批策略。

新任务使用 Run schema v3：

- `product_mode: standalone`
- 不可变 `launch_profile_snapshot`
- `review_policy.kind: local_confirmation`

启动必须原子化：失败时不得留下空会话、孤儿 Run 或半写入来源快照。

### 公共状态机

```text
任务准备（0/N）
→ 可以开始
→ 正在执行
→ 等待阶段确认
  ├─ 提交修改 → 正在修改 → 等待阶段确认
  └─ 确认通过 → 下一阶段
→ 正在生成文档
→ 最终检查
  ├─ 退回修改 → 正在修改
  └─ 本机确认交付 → 已完成
```

`需求确认` 是阶段前置态 `0/N`；内容创作团公开 5 个阶段，深度研究团公开 6 个阶段。内部审校与交付动作通过服务端投影映射到公开阶段，不由前端硬编码。

### API

- `POST /api/expert-teams/start`
- `GET /api/expert-teams/status/{run_id}`
- `POST /api/expert-teams/stage/revise`
- `POST /api/expert-teams/stage/confirm`
- `POST /api/expert-teams/delivery/open`
- `POST /api/expert-teams/delivery/revise`
- `POST /api/expert-teams/delivery/confirm`

阶段动作必须校验 Run、Session、Version、Stage、Attempt、Artifact Hash 和幂等键。文件动作必须拒绝路径穿越、符号链接逃逸、跨 Run/Session 和旧 Delivery Binding。

### View 投影

前端只消费服务端 View：

- `workflow.stages[]`
- `workflow.current_index`
- `workflow.total`
- `state`
- `next_stage`
- `allowed_actions`
- `artifact`
- `automatic_checks`
- `confirmation`

Catalog 中可见且可点击的任务必须与真实可启动 Profile 完全一致。目录加载失败时显示错误与重试，不使用可操作的本地假目录。

## 分批交付

### PR1：单机版主链

1. 固化 Launch Profile、Run v3、Standalone View 和本机确认策略。
2. 原子启动，移除客户端合同版本选择。
3. 实现阶段修改、阶段确认、文档打开、最终退回和本机确认交付。
4. 重构专家团前端：门户、详情、任务选择、可解释进度、阶段复核、最终交付。
5. 删除 V2/V3 双轨所有权和单机版企业身份入口；企业实现保留为隔离的历史读取能力。
6. 只开放 `work_report` 与 `research_report`；其余文种展示为不可启动且说明未就绪，或暂不展示，禁止 Legacy 回退。
7. 补旧数据清理器，默认 dry-run，并拒绝清理活动任务。

### PR2：逐文种放行

按以下顺序逐项补齐 Brief、Profile、Artifact、Template/Adapter、DOCX 门禁和 Electron 验收：

1. 会议纪要
2. 通知通报
3. 方案说明
4. 总结计划
5. 材料润色

每个文种未通过完整门禁前不得出现在可点击目录中。

## 前端设计约束

### 门户与启动

- 复用现有太极智能体视觉系统，仅改变专家团区域。
- 团队成员显示真实头像及替代文本。
- 任务卡只显示中文名称、用途和示例，不暴露原始枚举。
- 启动失败保留用户输入与原会话，并给出可重试原因。

### 阶段复核

- 标题固定显示“第 X/Y 步 · 阶段名称”和“等待你确认”。
- 完整步骤条显示编号/完成勾、阶段名、完成/当前/待办；窄屏显示当前和下一阶段摘要。
- `加入修改意见` 将建议追加到可编辑文本框并聚焦，不直接提交。
- 主动作仅为“提交修改并重做本阶段”和“确认当前成果，进入下一阶段”。
- 成功、失败、冲突和处理中状态必须可见，并通过 `aria-live` 宣告。

### 最终交付

- 显示“第 Y/Y 步 · 文档交付”；用户确认前最后一步保持当前态。
- 展示最终 DOCX、自动检查结果和简明检查要点。
- 提供“打开文档检查”“打开所在文件夹”“退回修改”“确认文档可交付”。
- 最终确认只记录本机用户确认；不要求身份、截图或企业审批。

## TDD 与验收门禁

### 合同与后端

- Catalog 可见集合等于真实可启动 Profile 集合。
- 启动幂等；任一写入/分发失败不留孤儿 Run/Session。
- 阶段确认覆盖 Version、Stage、Attempt、Hash、并发与重放。
- 最终交付覆盖旧绑定、文件被替换、自动 Gate、崩溃恢复和完成事务。
- 安全覆盖路径穿越、符号链接、跨 Run/Session。
- Standalone 全流程对 `/identity/*` 为零请求。

### 前端

- 5/6 阶段完整且语义正确，不硬编码数量。
- 无英文文种枚举、企业审批身份、OIDC 或审批角色。
- 目录失败不可启动；修改意见失败或重绘后不丢失。
- 成员头像、错误反馈、加载/禁用/空状态、键盘焦点和 `aria-current` 可验证。
- 最终确认前不显示完成；文档与文件夹动作可发现、可键盘操作。
- 专家团外聊天、定时任务、设置和外链默认拒绝策略无回归。

### 真实验收

- 确定性夹具跑内容创作团 5 阶段与研究团 6 阶段全链。
- 至少一次真实模型/真实桌面主路径，不把夹具绿灯等同真实交付。
- 1440×900、1120×720 和窄工作台截图回归。
- 最终 DOCX 自动检查通过，并用 WPS/Word 打开实看；若环境无法执行，明确标记未验证。
- 记录源码 commit、进程 cwd、Python 模块 `__file__`、Node/Electron 入口、端口、runtime/config 和静态资源哈希。

## Git 与发布流程

1. 正式根目录 `main` 与线上同步且干净后，在独立 `codex/*` worktree 开发。
2. 每个任务遵循 RED → GREEN → 重构 → 聚焦回归；完成审查与验证后及时本地提交。
3. 不覆盖无关改动，不复用历史 worktree、runtime 或旧构建产物。
4. 该改动同时涉及 UI、合同和身份语义，PR 必须使用 `full-ci` 门禁。
5. 默认只创建本地提交；获得明确授权后才 push 和创建 PR。
6. PR 合并后，正式根目录回到 `main`，执行 `git pull --ff-only`，证明主干包含成果，并从正式启动入口非破坏性复验。
7. 只有主干复验完成后，才审计并清理已合并的分支、worktree 和历史运行输出。

## 完成定义

- PR1 主链在独立 runtime 中完成自动化和真实 Electron 验收。
- 可见入口均可真实执行，不存在“画出来但后端不支持”的动作。
- 单机版网络日志中不存在身份/审批请求。
- 最终 DOCX 与其 Run、Artifact、Hash、Delivery Binding 可追溯。
- 专家团外页面和桌面安全策略无回归。
- 本地提交存在且工作树干净；未 push/未 PR/未合并的状态被如实说明。
