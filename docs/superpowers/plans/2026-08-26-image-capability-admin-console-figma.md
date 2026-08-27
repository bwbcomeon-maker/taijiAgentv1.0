# 图片能力中心管理员控制台 Figma 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Figma 中交付完整“模型配置”桌面页面，并以管理员控制台重新设计其中的图片能力中心。

**Architecture:** 新建一个 Figma Design 文件，先建立整页画板和既有页面上下文，再在中部使用状态条、能力总览、凭据库和配置抽屉描述新的管理流程。主页面和抽屉态保持同一视觉令牌、排版及页面框架，图片能力中心作为唯一重新设计的区域。

**Tech Stack:** Figma Design、Figma Plugin API、现有 WebUI 的太极浅蓝玻璃主题和字体令牌。

---

### Task 1: 建立完整页面骨架与视觉基线

**Files:**
- Create: Figma Design 文件“太极智能体｜模型配置图片能力中心改版”
- Reference: `hermes-local-lab/sources/hermes-webui/static/style.css:151-154`
- Reference: `docs/superpowers/specs/2026-08-26-image-capability-admin-console-figma-design.md`

- [ ] **Step 1: 创建 Figma Design 文件并检查可用的页面、组件、变量和字体**

使用 Figma `whoami` 取得唯一 plan key 后创建设计文件；检查空文件中已有页面/组件，并确认可用中文字体。字体优先使用 `PingFang SC`，不可用时依次回退 `HarmonyOS Sans SC`、`Microsoft YaHei`。

- [ ] **Step 2: 建立 1440px 宽的完整桌面画板**

建立名为“模型配置｜图片能力中心改版”的顶层画板，包含 macOS 窗口层、左侧导航、浅蓝玻璃背景和内容容器。导航复原“聊天 / 定时任务 / 专家团 / 设置”及“设置”选中态。

- [ ] **Step 3: 复原非改版的页面上下文**

在内容容器中依序摆放“模型配置”标题、授权摘要、API Key 异常提示、当前生效主模型和辅助模型摘要。保留原页面的蓝色描边、白色半透明卡片、圆角和紧凑按钮语气，不给这些区域新增交互。

- [ ] **Step 4: 截图核验整体框架**

导出主画板截图，确认导航、内容容器、标题层次、背景和上下文区域均可见，且图片能力中心有充足垂直空间。

### Task 2: 创建图片能力管理员控制台主态

**Files:**
- Modify: 同一 Figma 主画板的“图片能力中心”区域
- Reference: `docs/superpowers/specs/2026-08-26-image-capability-admin-console-figma-design.md:20-79`

- [ ] **Step 1: 放置状态与动作条**

增加“图片能力中心”标题、说明、状态摘要和“全部验证 / 刷新状态”动作。默认样例为“2 项已配置，0 项已验证”；“全部验证”说明可能产生外部调用费用。

- [ ] **Step 2: 创建图片理解与图片生成两张能力卡**

两卡展示不同的样例状态：图片理解为“已配置，待验证”，图片生成为“未验证 / 无可用路由”。每张卡展示 Provider、模型、凭据引用、实际路由，以及“配置 / 立即验证”可发现入口。宽内容列时并列，窄内容列时可单列。

- [ ] **Step 3: 增加凭据库与共享影响信息**

在能力卡下放置简洁凭据库，展示“taiji-image”被哪些能力引用，并用“共享”标识。加入“新增独立凭据”动作，明确它不会覆盖已有配置。

- [ ] **Step 4: 截图核验能力主态**

核验信息层级：状态与下一步在最上方，能力卡用于扫读，凭据库作为次级信息；所有状态均同时包含文字、图标和颜色，长模型与路由文本不溢出。

### Task 3: 创建配置抽屉展开态与最终审阅

**Files:**
- Create: Figma 页面或画板“模型配置｜图片生成配置抽屉”
- Reference: `docs/superpowers/specs/2026-08-26-image-capability-admin-console-figma-design.md:32-68`

- [ ] **Step 1: 复制完整页面为图片生成配置状态**

保留整页框架和导航不变，在右侧显示 420px 左右的配置抽屉，使用户能同时看见被编辑的图片生成能力卡。

- [ ] **Step 2: 填充抽屉内容与关键状态**

抽屉依次呈现“接入配置”“模型与路由”“高级设置”。凭据字段只显示引用“taiji-image（共享）”与影响范围，不显示 API Key；保存按钮、取消按钮和“保存后待验证”的辅助说明均可见。

- [ ] **Step 3: 进行 Figma 视觉与可访问性审阅**

核对标题、正文、标签、按钮的对比度和层级；确认状态并非仅靠颜色传达，按钮有文字标签，抽屉关闭位置明显，焦点/键盘行为有设计标注；对主画板与抽屉态各截一次图，检查无重叠或裁切。

- [ ] **Step 4: 提供 Figma 链接与已验证边界**

交付可编辑 Figma 链接和两个画板的说明。报告 Figma 截图审阅结论；明确真实浏览器、代码实现、Provider 调用、自动化无障碍和视觉回归未验证。
