# 本机调试开始使用检查 Implementation Plan

**Goal:** 用户已确认：企业安全和本机调试均为安装版可选策略，不因本机调试阻塞开始使用。

**Architecture:** 仅修改 `api/onboarding.py` 的安装态安全检查；保持 restricted 运行模式要求、未知策略拒绝和待重启检查。实际能力授权、默认值、风险确认和启动链不变。

**Tech Stack:** Python / pytest / 现有隔离 Playwright smoke。

## 执行与验收

- [ ] 更新 `hermes-local-lab/sources/hermes-webui/tests/test_onboarding_mvp.py`：restricted + strict/local_controlled 均 ready；两种策略待重启均拒绝；full、custom_restricted、未知策略仍拒绝，读取异常维持 unavailable。
- [ ] 运行该测试确认旧代码对 local_controlled 失败。
- [ ] 修改 `hermes-local-lab/sources/hermes-webui/api/onboarding.py`：`mode == "restricted" and profile in {"strict", "local_controlled"} and not security.get("restart_required")`；按当前模式输出已生效提示，本机调试保留可信环境风险说明，异常不强制指定企业安全。
- [ ] 更新 `docs/runbooks/security-capability-settings.md` 的安装检查契约。
- [ ] 聚焦 pytest 与现有隔离浏览器 smoke 验证主流程、完成后继续配置入口消失；保存中文 UX QA 报告，明确安装态未验证。
- [ ] 运行一次 `scripts/verify.sh --full`；精确暂存，Sol 最终审核，通过后 commit、fetch 并正常 push main。

不修改工具权限，不重命名脚本任务，不制包、不安装、不发布。唯一源码写入者为本任务主 Agent，审核者只读。按用户已确认方案直接实施，不增加设计选择。
