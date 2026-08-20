# Kylin amd64 候选制包流水线暂停交接

> 更新日期：2026-08-20（Asia/Shanghai）
> 本文件是跨平台控制器接续 Linux 候选流水线的 tracked handoff；其中历史测试结果不替代当前命令核验。

## 目标

- 保留 Kylin amd64 候选流水线已经实现的本地控制器、状态模型、fake transport 和恢复合同。
- 在真实麒麟连接恢复并获得独立授权后，按固定顺序生成当前 source commit 绑定的候选 DEB。
- 本 handoff 的最高结论只到候选 DEB 证据，不延伸到安装、验收、签名或发布。

## 冻结实现身份

- Kylin 冻结实现：`codex/kylin-amd64-candidate-pipeline@a5a36849bca009d1cfb07ac2309532a502c6bd70`。
- 正式 main 基线：`main@5364233e1297e5f2837382823d4e35a0d114aba7`。
- 原 Linux worktree：`/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/kylin-amd64-candidate-pipeline`。
- 跨平台控制器 worktree：`/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller`。
- 跨平台控制器分支：`codex/cross-platform-package-controller`。
- 唯一正式仓库：`/Users/bwb/Documents/工作/taiji-agentv1.0`。
- 旧 Windows 仓只作为有来源约束的迁移材料，不是运行时依赖，也不是最终代码真相源。

## 已完成

- 已实现，本地模拟通过。
- 已有统一入口、local doctor/plan、输入三件套合同、fake transport、阶段状态、`FETCH_PENDING` 和本地 fetch 恢复合同。
- 已保持 `99 → 00 → 01` 为 Kylin 的既有构建权威；本阶段没有复制或改写 Linux 打包逻辑。
- 绑定 `a5a36849bca009d1cfb07ac2309532a502c6bd70` 的历史验证包括：71 个核心/传输/编排测试和 56 个 Skill/输入合同测试；这些是历史证据，不是本次 handoff 的实时回归结果。

## 未完成

- online doctor 未执行。
- 真实麒麟连接未验证，未对 `kylin` 发起 SSH 或 SCP。
- 99/00/01 未执行，当前 commit 的三件套未真实生成。
- 候选 DEB 未构建，未验证远程与本地制品 SHA 一致。
- 未执行安装、离线生命周期、图形验收、签名、生产授权或发布。

## 恢复前置条件

- 继续前重新核对正式 main、暂停 Linux worktree 和跨平台控制器的 branch、HEAD、clean 状态；任何身份漂移或归属不明修改都停止。
- 确认本轮准确 source commit，并在本地 doctor 通过前不连接远端、不准备输入、不调用 99。
- 真实麒麟恢复可达后，先完成只读 online doctor；架构、工具、磁盘、sudo、权限或网络不满足时停止。
- SSH/传输、依赖/网络和后续构建阶段必须分别明确当前对象、影响范围和回滚边界；任何一项授权都不扩大到安装、验收、签名或发布。

## 精确恢复顺序

在暂停的 Linux worktree 中按以下顺序恢复，命令只表示恢复合同，不表示本次 handoff 已经执行：

```bash
git status --short --branch
git rev-parse HEAD
./taiji-package doctor
./taiji-package plan
./taiji-package doctor --online
```

local doctor 和 plan 必须先通过；online doctor 未通过时停止。online doctor 通过后，按下面三个独立授权块逐项确认：

1. **SSH 与传输**：确认 host、source commit、三件套身份、远程 run 目录、传输方向和失败保留方式。
2. **依赖与网络**：确认 `00`/`01` 可能使用的 apt、sudo、工具下载、网络边界和失败停止位置。
3. **候选构建**：确认 commit、构建主机、输出目录和只到候选 DEB 的范围；不进入安装、验收、签名和发布。

主机不可达时必须在调用 99 前停止。三项专项授权全部闭合后，才允许执行：

```bash
./taiji-package build
```

构建成功后仍需分别核对输入、远端 review/log、DEB basename/bytes/SHA、manifest 和状态；不能把 build 命令启动或健康检查当作候选制品完成。

## 跨平台接力

- 统一控制器在 `/Users/bwb/Documents/工作/taiji-agentv1.0` 同一正式仓库内继续开发，最终入口为 `taiji-package`。
- Kylin 和 Windows 使用独立 adapter 与独立 transport；Linux 的 `99/00/01`、apt、DEB 和远程目录语义不迁移到 Windows。
- 旧 Windows 仓只按计划中固定的 Git object、路径、mode、blob 和 SHA 取用指定迁移资产；不得递归复制、合并历史或作为运行时依赖。
- 本 handoff 只负责 Linux 暂停现场，不授权 Windows 真实远程阶段、候选 EXE、安装、签名或发布。

## 证据边界

| 项目 | 当前口径 |
| --- | --- |
| 本地控制器和 fake 链 | 已实现，本地模拟通过 |
| 历史测试 | 绑定 `a5a36849` 的历史证据 |
| 真实麒麟连接 | 真实麒麟连接未验证 |
| online doctor | online doctor 未执行 |
| `99/00/01` | 99/00/01 未执行 |
| 候选 DEB | 候选 DEB 未构建 |
| 安装、验收、签名、发布 | 未执行，不得从本地模拟推导 |

## 状态卡

- 任务：Kylin amd64 候选制包流水线暂停 handoff。
- 分支：`codex/cross-platform-package-controller`。
- worktree：`/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller`。
- 阶段：等待恢复验收。
- 交付口径：分支已有 Kylin 本地实现；真实候选未生成。
- 已完成：已实现，本地模拟通过。
- 未完成：真实麒麟连接未验证；候选 DEB 未构建。
- 验证：历史测试绑定 `a5a36849`；本 handoff 执行前需重新完成身份检查和本地回归。
- 是否影响 main：否；正式 main 仍为 `5364233e1297e5f2837382823d4e35a0d114aba7`。
- 下一触发条件：真实麒麟恢复可达、只读 online doctor 通过，并分别获得 SSH/传输、依赖/网络、候选构建授权。
