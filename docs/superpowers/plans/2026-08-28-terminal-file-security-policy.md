# 方案 2 核心最小实施计划

## 边界

只实施已批准的六项核心合同。所有超范围探索保留在本地 WIP stash，不进入本轮工作区补丁。

## 步骤 1：状态错误分离

- 测试：损坏 `license-state.json` 返回 `license_state_invalid`，中文不含“系统时间异常”。
- 实现：真实回拨与内容损坏使用不同消息。

## 步骤 2：POSIX 目录合同

- 测试：自有 `0700/0755/0775` 可用。
- 测试：`0777`、错误 owner、symlink 返回独立原因。
- 实现：删除“自有父目录 group-writable 即拒绝”的单点规则，保留私有叶目录保护。

## 步骤 3：Windows 目录合同

- 测试：标准继承 DACL 可用。
- 测试：错误 owner、危险外部写 ACE、reparse point 独立识别。
- 实现：保持现有 Windows 安全句柄与 DACL 逻辑，不扩大到安装态验收。

## 步骤 4：安装态默认值

- 测试：空配置默认 local_controlled。
- 测试：strict 重启后保持。
- 测试：非法非空配置失败关闭。
- 实现：terminal/execute_code 默认开启；delegate/skill script 独立且默认关闭。

## 步骤 5：命令保护回归

- 测试：危险命令仍进入审批。
- 测试：灾难性命令仍硬阻断。
- 不修改既有命令风险规则，除非核心测试证明发生回退。

## 步骤 6：测试收集修正

- 将误嵌套测试移回 pytest 可收集层级。
- 用 `--collect-only` 和单测确认它被收集并执行。

## 最小验证

- Desktop launch/profile 与 POSIX state 聚焦测试；
- License 状态损坏和 0775 聚焦测试；
- POSIX/Windows 合同测试；
- approval 收集测试及危险/灾难性命令聚焦测试；
- `git diff --check`。

以上是设计/RED 阶段的最小验证边界。后续经用户另行批准进入核心最小实施与收尾，已追加 `scripts/verify.sh --full`；精确暂存、Sol cached-diff 审核、提交和推送按项目生命周期执行。
