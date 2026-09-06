# 智囊库紧凑布局实施计划

**Goal:** 落实用户确认的设计稿，压缩页头与卡片留白，展开分类栏时宽屏三列、收起时四列，搜索始终可见。

**Architecture:** 保留 vanilla JS、既有目录 API、筛选/收藏/详情/分页状态。只调整智囊库作用域内 shell 尺寸、卡片结构与工具栏；列数由结果区容器宽度决定，详情并排时也能自动减列。不修改角色图片、Provider、会话或交付链。

**Tech Stack:** HTML / CSS container queries / vanilla JavaScript / isolated Playwright + Python server.

**来源与所有权:** main 基线 bc293bd83a1e040143983cc0c5638dad2defcea9；本任务主 Agent 唯一写入，Sol 只读终审。用户已确认两状态设计稿。

## 设计规格

- 仅智囊库宽屏页面将品牌栏设为 176px、分类栏 192px；其他模块保持原布局。
- 主区内边距 24px、页头约 76px，弱化电力背景；卡片实色白底。
- 卡片最小宽 268px、间距 16px；结果区小于 552px 一列、552px 起两列、836px 起三列、1120px 起四列。超宽时卡片不超过 340px。
- 图标 36px 与标题 16px 并排；说明 13px 两行；标签、详情入口紧凑布局；类别保留为次级信息。长标题允许换行，不挤压收藏按钮。
- 搜索移入主区工具栏，label 保留；展开/收起沿用现有有名称的侧栏按钮。窄屏保留原导航入口；641–900px分类栏192px，64px导航栏独立滚动并常显标签。
- 卡片网格独立滚动，分页不覆盖卡片；空态/加载/失败/收藏 pending 与最近任务入口保持。

## 执行步骤

- [x] 在 tests/zhinang_browser_e2e.cjs 增加 compact-layout scope，独立 tests/zhinang_compact_layout.cjs 检查 1440px 展开三列、收起四列、搜索、筛选、分页、详情、焦点和窄屏溢出，记录修改前截图及 RED。
- [x] static/index.html 移动搜索与压缩页头；static/zhinang.js 将标题放入 card-top 并保留所有动作；static/zhinang.css 实施上述布局与响应式规则。
- [x] 运行 compact-layout scope，检查截图并验证 1440/1280/1024/768/390/200% reflow；执行相关现有 runtime 合同和 scripts/verify.sh。
- [x] 更新用户说明、独立变更说明和中文前端 UX QA 台账（原 CHANGELOG 超过非源码安全上限，保持不改），记录真实验证与未验证边界。
- [ ] 精确暂存，Sol 对完整 staged bytes 审核，通过后 commit、fetch、证明远端未领先并正常 push main。

## 验收边界

仅 development 源码与隔离配置。截图来自当前源码真实浏览器，不等于安装态或目标机验收。无发布、制包、持久服务操作。
