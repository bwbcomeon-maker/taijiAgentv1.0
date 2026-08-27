# 前端 UX QA 报告：主模型 Provider 切换一致性

## 结论

本次主模型 Provider 切换交互未发现 P0–P3 问题。自动化行为验证、WebUI 回归和开发模式 Electron 视觉核验均通过；安装态未验证，不能据此宣称安装版本已更新。

## 检查范围

- 设置 → 模型配置 → 当前生效主模型
- Provider 下拉切换
- 模型、Base URL、尚未保存的 API Key 草稿同步
- 保存后的后端一致性保护

## UX 核验

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 可发现性 | 通过 | 沿用现有 Provider 下拉与模型输入框，没有新增隐藏入口。 |
| 切换反馈 | 通过 | Provider 变化后模型立即选择新 Provider 列表首项，旧端点和密钥草稿立即清空。 |
| 防误操作 | 通过 | 后端拒绝明确沿用旧 Provider 模型的切换请求，并清除旧 `api_mode`。 |
| 新模型兼容 | 通过 | 目录未知的新模型 ID 仍允许手工填写和保存。 |
| 密钥安全 | 通过 | 页面只清空未保存草稿；响应和测试均不读取或回显真实密钥，仓库扫描未发现用户提供的密钥。 |
| 键盘与可访问性 | 无回归 | 沿用原生 `select` 的 `change` 行为，没有新增焦点陷阱、覆盖层或仅鼠标入口。 |
| 视觉布局 | 无变更 | 本次未改 DOM 结构、尺寸、颜色或排版。 |

## 当前验证

- Provider 切换 RED：4 项按预期失败。
- 初次修复后聚焦 GREEN：5 项通过。
- Sol 兼容补强 GREEN：4 项通过。
- 当前完整项目门禁：`verification: PASS`；其中 WebUI 692 项通过。
- `npm run lint:runtime`：通过。
- `scripts/check-local-change-safety.py`：通过。
- `git diff --check`：通过。

## 未验证与门禁边界

- 开发模式 Electron 已验证 Provider 切换到“智谱 GLM（国内）”、模型联动为 `glm-5`、旧 Base URL 不显示且未保存配置；截图为 `/private/tmp/taiji-zai-cn-form-proof.png`。
- 使用所选模型完成一次真实计费对话：未验证；连接验证不能替代真实对话。
- 安装态或发布态：未验证。
