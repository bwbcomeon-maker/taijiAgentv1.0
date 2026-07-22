# 前端 UX QA 报告：专家团 V3

## 结论

专家团 V3 已形成“门户 → 团队详情 → 任务规格/资料 → 阶段执行 → 人工复核 → DOCX 校验 → Office 验收 → 最终交付”的单一工作台。当前未发现 P0/P1 级前端阻断，可进入 PR/CI 门禁；真实 OIDC、真实 Provider 全阶段生成、人工 WPS/Word 终验仍是发布前门禁，不得由 mock 证据替代。

## 范围与隔离

- V3 新增样式使用 `et3-*` / `expert-team-v3-*` 命名，对通用聊天布局的两处桥接只在 `body.expert-team-v3-active` 或 `body.expert-team-v3-collapsed` 期间生效。
- 离开聊天面板时销毁 V3 工作台和状态类。Electron 1024px 定时任务页验证为 `active=false`、`workbench=false`、`tasksVisible=true`。
- 未修改聊天、定时任务、设置等非专家团页面的信息架构。

## 可用性与交互

- 专家团门户只呈现两个试点团队，发起任务前明确告知“先确认规格，不直接生成”。
- 阶段复核只保留“提交修改意见”和“无修改，进入下一阶段”两个主路径。“加入修改意见”直接写入同一编辑框。
- 权威重绘、身份状态返回、收起/展开工作台时保留未提交草稿；草稿指纹绑定 stage review / Office review / document SHA，不跨权威对象恢复。
- 失败态提供刷新、重试或停止入口；刷新失败会显示可见错误，不再形成“点击无反应”。
- 1024px 下工作台占满聊天主区，避免抽屉压缩主内容；1440px 下使用右侧工作台。

## 可访问性

- 团队详情使用 `role="dialog"` + `aria-modal="true"`，具备初始焦点、Tab/Shift+Tab 焦点圈定、Escape 关闭和焦点归还。
- 资料和 Office 证据上传由真实可聚焦按钮触发，隐藏输入仍保留语义和描述关联。
- 阶段进度提供 `progressbar`、数值和 `aria-current="step"`；错误反馈使用 assertive live region，忙碌操作暴露 `aria-busy`。
- 辅助文字颜色已加深；支持 `prefers-reduced-motion`。
- 未验证：VoiceOver/NVDA 真实读屏、axe/Lighthouse 自动扫描、系统文件选择器的纯键盘完整路径。

## 视觉与响应式

- 颜色、圆角、边框、白色玻璃卡和青色主操作与现有太极界面一致。
- 已实看门户、团队详情、真实 Brief、1024px 复核、1440px 复核、Office 表单、最终交付和 1024px 非专家团页。
- Office 默认选择“通过”时隐藏“不通过”问题表单；底部操作栏不再使用负 bottom/margin 导致内容下穿。
- 剩余 P2：1280/1281px 临界会在全工作台和右侧工作台间跳变；建议后续升级为基于容器宽度的容器查询。

## 验证证据与边界

- 前端 V1/V2/V3 合同：126 项通过。
- 专家团全量 Python 回归：588 项通过，1 个既有 `audioop` 弃用警告。
- Electron smoke 使用真实 Electron 壳、真实 V3 DOM/CSS、隔离 runtime；真实 HTTP 覆盖 session、catalog、start、Brief 更新、文字资料绑定和 run 回读。
- Electron 中的阶段 revise/approve、身份、Office API 为明确记录的 mock，只证明前端交互和请求体合同，不证明真实后端业务闭环。
- 真实 DOCX 生成链由 Python 全量回归覆盖；真实桌面端打开新生成 DOCX 并在 WPS/Word 人工逐页验收：未验证。

## 缺陷分级

- P0：0。
- P1：0。
- P2：1（临界宽度布局跳变）。
- P3：0。
