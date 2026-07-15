# 前端 UX QA 报告：专家团 Office 二级验收

## 状态

代码实现与确定性自动化已通过；真实企业 IdP 和目标环境 WPS/Word 尚未验收，因此不具备“企业发布已完成”的结论。自动可访问性和像素级视觉回归未配置。

## 变更范围

专家团“成果”中的 Office 摘要卡、全高验收抽屉、可信授权人交接、逐项 waiver、结构化返修、安全 view/presenter 投影和 Electron smoke。

## 主要用户目标

用户能在不暴露 token、完整路径或客户端身份字段的前提下，检查正式 DOCX，安全处理 condition 授权或退回结构化问题。

## 主内容 / 辅助内容 / 高级内容

- 主内容：验收结论、9 项 checklist、结构化 issues 和唯一下一步。
- 辅助内容：正式版本、短 hash、问题数、验收人。
- 高级内容：完整指纹、证据路径和 token 只由服务端验证，UI 仅显示折叠说明，不暴露值。

## 已测试的主要用户路径

- 摘要卡打开抽屉，焦点进入抽屉。
- 九项 checklist、condition/blocking/unknown severity 显示策略。
- 原 reviewer 持久 SSO 失败：零 waiver、理由和焦点保留。
- 不同 authorizer 成功：waiver 只发 target/reason/version/idempotency。
- 交接过期：可重试，理由不丢失。
- 退回修改：先确认服务端派生影响，再只提交 issue IDs。
- 轮询替换 DOM 后关闭抽屉：焦点返回当前 live trigger。
- 脏草稿 Escape 保护：取消关闭保持抽屉，确认后关闭。
- “提交验收”真实点击：passed、passed_with_conditions、failed 各恰好调用一次既有记录 endpoint，双击不重复提交。
- token 过期：显示可恢复错误，保留结论、9 项 checklist、备注和结构化问题草稿。
- 只填写 waiver 理由或勾选返修/checklist 后按 Escape 均触发脏数据保护。

## 功能契约摘要

Office summary/drawer、主提交、waiver 和 revision 均有可见入口、状态反馈、错误恢复、禁用态和确定性 Electron 证据。blocking、unknown severity 以及 stage/semantic/automatic 目标不显示授权入口。Electron 中的 API 和身份为受控 mock，不代表真实企业系统验收。

## 真实浏览器测试证据

`tests/expert_team_electron_artifact_smoke.js` 通过真实 Electron + Playwright 执行上述路径，输出 `EXPERT TEAM ELECTRON SMOKE OK`。

## 截图情况

已目视检查 `/tmp/expert-team-office-submit-qa/expert-team-office-review-drawer.png`。首轮发现抽屉透明/层级裁剪 P1，改为 body portal 和项目真实主题 token 后复验不透明、主次清楚。

## 可访问性检查

已验证 dialog/aria-modal、fieldset/legend、aria-live、Tab 焦点圈定、Escape 脏数据保护、背景 inert 和关闭后焦点归还。自动 axe/Lighthouse：未验证，项目未配置对应工具。

## 视觉层级与长时间工作

摘要卡保持低密度；长表单使用一个纵向滚动容器；底部主操作稳定；背景遮罩降低干扰。抽屉关闭按钮仍为原生外观，记为 P3。

## 自动化检查运行结果

| 检查项 | 结果 |
|---|---|
| Task 5 完整 pytest 回归（含 DOCX route、frontend 与 trusted identity） | 221 passed，1 个既有 `audioop` 弃用警告 |
| `npm run lint:runtime` | 通过 |
| `git diff --check` | 通过 |
| Electron smoke | 确定性 mock 环境通过，18 张截图；不等同于真实 IdP/WPS 终验 |

## 问题列表

| 严重程度 | 问题 | 状态 |
|---|---|---|
| P1 | 抽屉受工作台 stacking/overflow 裁剪 | 已修复并复验 |
| P1 | 使用不存在的主题变量导致透明 | 已修复并复验 |
| P1 | 轮询替换原 trigger 后焦点无法归还 | 已修复并复验 |
| P3 | 关闭按钮外观未完全与主题统一 | 未修复，不阻断 |

## 剩余风险与未验证项

- 自动可访问性扫描未验证。
- 像素级视觉回归未配置；已执行真实截图目视检查。
- 真实企业 IdP 账号切换与 WPS/Word 人工终验仍属 Task 7；本轮 Electron 使用确定性 identity/API mock 验证 UI 合同。

## 后续建议

Task 6 继续保持 rollout 默认关闭；Task 7 使用真实 IdP 和 WPS/Word 完成两条黄金路径终验。
