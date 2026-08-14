# Taiji 国产 x86_64 黄金制包流程 DoD-A 最终验收报告

日期：2026-08-14（Asia/Shanghai）

## 1. 结论

本轮完成的是“黄金制包流程实现候选”的本地 DoD-A 收口：仓库中的正式自动化、direct `formal-build-tests/v2`、证据验证、静态发现接口、repo-owned Skill、doctor、确定性 Skill 打包器、测试和操作知识已形成一套单一权威链路。

本报告不宣称 DoD-B。真实 Linux/Kylin 制包、DEB 安装生命周期、真实 Python 3.8 和 Linux `/proc` 执行、CI、签名、发布及客户交付均未在本轮执行。

## 2. 本轮收口内容

1. `00_制包机_生成离线交付包.sh` 在候选制品和交付身份固定后、direct driver 前明确准备正式测试依赖；成功和异常出口统一关闭 held runtime FD。
2. `scripts/run-taiji-formal-build-tests.py` 保持单一 20 项注册表：20 项、1864 字节、SHA256 `5fdcd9335ac9c722b224c06b03d817bd505cff4abc514b09f8d9ba604c11953b`。
3. direct driver 使用受控 Python/Node/npm/ESLint 身份，恢复 Agent/WebUI suite 所需的最小环境；npm/ESLint 以 held FD 内容执行，逻辑路径从固定 source/work root 派生并与 held FD 身份核对。
4. v2 producer 按 suite 聚合 stdout/stderr，再输出连续 target result、suite counts/status 和唯一 overall pass；完整 20 项生产日志已由真实 validator 联合验收。
5. pytest 从私有 scratch 目录启动，使用绝对 selector、固定 rootdir/confcutdir/config，由 trylast hook 切回受控项目根；缺配置、零收集、skip/deselect/xfail/xpass 均失败关闭。
6. suite 共用 3600 秒 deadline，stdout/stderr/result 三通道有界；超时、溢出和正常 leader 留下后台进程时均执行进程组收尾。
7. Skill doctor 只静态检查操作员明确提供的 repo 或 frozen input，不执行仓库代码，禁用 Git fsmonitor，按 HEAD 而非 index 建立入口权威。
8. Skill 明确 99 准备输入、00 消费输入的阶段边界，以及外部/特权动作的逐阶段授权事实；跨 Agent 安装只对 Codex `.skill` 作本地验证，其他产品未实测即标记未验证。

## 3. 本地验证证据

| 门禁 | 实际结果 |
| --- | --- |
| driver + formal evidence + Skill 合同 | 72 tests OK，2 项因 macOS 缺 Linux `/proc`/`waitid` 显式 skip |
| schema-v3、release-check、source integrity、strict toolchain、builder input、trusted Git、target evidence | 117 tests OK |
| frozen input preparation/consumer | 10 tests OK |
| 固定隔离 release Python runner | 311 tests OK，386.431 秒，无 unexpected skip |
| Bash 语法 | `00` 的 `/bin/bash -n` exit 0 |
| Python 当前解释器编译 + Python 3.8 grammar | 7 个变更 Python 文件通过 |
| Skill 官方 quick validator | `Skill is valid!` |
| Skill 前向评测 | 最终 Skill SHA 绑定的 8 项、24 expectations：24/24 PASS；旧 SHA/旧轮结果作废 |
| Git whitespace | `git diff --check` exit 0 |

## 4. Skill 产物

- 源 Skill SHA256：`798b13b9db6ab47f9e518b7882b6af4a2016365d15a0dac1268d46a0593052fd`
- 安装包：`dist/skills/taiji-kylin-packaging.skill`
- 字节数：46,782
- 安装包 SHA256：`c9811d617f2516014cb621ea819e7680e396c38312007a6802d0edb6bd83724e`
- 成员：固定 9 个普通文件，ZIP_STORED，固定顺序/时间/权限。
- 双目录构建：两份 `.skill` 逐字节一致；两个 sidecar 校验通过；两个 ZIP 完整性检查通过。
- 解包验证：解包后的 `doctor.py --selftest` 返回 canonical pass JSON。
- 旧 `dist/skills` 已移动到系统临时目录保留，未不可恢复删除。

## 5. 独立审查

- 规格审查：PASS，无剩余规格 blocker。
- 代码质量审查：PASS，无 P0/P1。
- 低能力模型前向评测：第三轮 24/24 PASS；8 项均先报告并匹配最终 Skill SHA。

非阻断 P2：driver 未统一设置 `TZ=UTC`；极早期 `getpgid` 异常路径的资源回收依赖进程退出；部分独立 helper 的 symlink 防护依赖正式入口的外层 inventory/root 门禁。这些不改变本轮批准合同，后续可作为维护项处理。

## 6. 证据边界

### 已实时验证

- 当前 macOS worktree 上的本地单元、静态、协议、语法、确定性打包和前向评测。
- 20 项注册表与 v2 producer→真实 validator 的协议闭环。
- repo-owned Skill、doctor、自检、固定包成员和摘要。

### 未实时验证

- 真实 Python 3.8 进程运行最终 driver/runner。
- 真实 Linux `/proc/self/fd` 与 `waitid/WNOWAIT` 路径。
- 固定 Linux Node/npm/ESLint 工具链执行完整 20 项测试。
- frozen trio 生成、真实 x86_64 DEB 制包、断网安装/升级/卸载。
- Kylin/UOS/openKylin 目标机验收、GitHub CI、签名和发布。

因此，本轮完成口径只能是“黄金流程实现候选、本地门禁通过”；不能写成“真实黄金 DEB 已认证”。
