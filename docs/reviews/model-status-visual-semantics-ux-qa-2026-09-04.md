# 前端 UX QA 报告：模型配置状态视觉语义

状态：源码及隔离浏览器通过；麒麟新安装态未验证。

## 范围与来源

用户目标：消除“模型服务连接正常”仍配黄色大感叹号的误导，同时保留真实失败提示。

物理仓库 `/Users/bwb/Documents/工作/taiji-agentv1.0`，Git common dir 为本仓库 `.git`，分支 `main`，起点 `1e4f2894595102e7db9efb100b6b175bbbaff3a1`。主代理唯一写入，既有 worktree 未操作。浏览器读取本工作树静态 HTML/JS/CSS；系统 Python 3.13 Playwright、headless Chromium、临时回环 HTTP 服务及全模拟 API。不读取真实配置或凭据，不调用真实 Provider，不修改麒麟服务。

本轮变更记录：连接正常采用绿色小勾；真实对话验证保留独立文案；未检查/不支持检查采用中性色；加载/检查/运行时刷新采用蓝色；主配置缺失为黄色；真实失败为红色。可选图片状态从主模型摘要移除，由当前可见图片能力中心显示。旧隐藏兼容控件没有作为可见验收证据。状态枚举、持久化、模型选择及请求协议不变。

原 WebUI CHANGELOG 超过既有安全扫描 1 MiB 门限，本次不追加该历史文件、不放宽扫描；本节与行为 RFC 记录本轮变更。

## 功能契约与验收

角色：本机配置用户；入口：设置 → 模型配置。契约依据为用户确认的设计文档。

| 能力 | UI / 数据 / 反馈 | 状态与错误 | 键盘、可访问性 | 证据 | 结论 |
|---|---|---|---|---|---|
| 主模型状态 | 既有主摘要及状态徽标；model-config verification | 连接成功与对话成功分开；中性、加载、缺配置、失败独立 | 文字与图标同时表达；polite live region | Node 状态执行；三个视口真实刷新与截图 | 通过 |
| 检查连接 | 原检查按钮；mock main/check POST | 检查中蓝色、按钮禁用；失败提示；重试恢复 | Enter 重试；aria-busy；不只靠颜色 | 三视口实际点击、延迟响应、503、键盘重试 | 通过 |
| 可选图片能力 | 可见图片能力中心、刷新图片能力按钮；mock image-capabilities GET | 未启用/未验证中性，验证中蓝色，失败红色；不污染主摘要 | 保留标签、原生控件及状态文本 | Node 状态映射；三视口实际刷新并断言可见验证区域 | 通过 |
| 原刷新及草稿保护 | 刷新本机状态、取消/确认 | 图片忙碌不阻塞；HTTP 失败可重试；草稿取消保留 | Enter、取消初始焦点 | 原有浏览器流程继续执行 | 通过 |

## 验证台账

- RED：主摘要连接成功/刷新配色与检查中断言先失败，随后修正。补充可见区域检查时，旧高级控件点击超时；确认祖先 `legacyImageCapabilityControls` 为隐藏，改用当前图片能力中心验证，而非强行显示旧控件。
- 中间回归发现图片加载状态新增代码未容忍缺少状态节点的测试 DOM；补齐空值保护，未修改刷新协议。
- 聚焦最终：`test_model_config_frontend.py`、`test_model_config_refresh.py`、`test_image_capability_center_frontend.py`，`--noconftest`，141 passed。Node 24.19.0；Agent venv Python 3.11。日志 `/private/tmp/taiji-status-focused-final.log`。
- 浏览器：`tests/model_config_refresh_browser_smoke.py`，1280×900、768×900、390×844；所有 API mock，外部请求阻断。每视口三次模拟连接检查，不发生真实模型调用或图片生成。最终日志 `/private/tmp/taiji-status-browser-final-source.log`；日志首行给出截图目录。
- 人工查看窄屏连接成功主摘要、窄屏图片未启用及桌面图片失败截图；主成功区绿色小勾与文字一致，错误在能力自己的区域显示。主摘要元素截图可完整显示窄屏内容，不以页面截图内部滚动裁切误判内容缺失。
- 广泛门禁：`scripts/verify.sh`，Node 24.19.0 在 PATH 首位；日志 `/private/tmp/taiji-status-verify.log`。该计划包含 root、Desktop、DOCX、Agent、WebUI、branding、bootstrap、coexistence。最终退出码及提交/推送结果以任务交付回执为准，不以本报告替代暂存后的 Sol 审核。

## 边界及遗留

已使用前端 UX QA 的功能契约、真实浏览器和截图检查区分当前可见中心与隐藏旧控件。受影响路径未发现阻断项。保留既有高级布局、费用提示与恢复入口；不新增依赖或重构模型配置。

未验证：axe 自动化、像素级视觉回归、读屏软件、长时间舒适度、真实 Provider、图片保存并验证的真实远程调用、麒麟本次 UI 安装态。源码测试不能替代新包及目标机验收。本轮不制包、不安装、不发布。
