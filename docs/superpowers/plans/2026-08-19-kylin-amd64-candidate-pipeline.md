# x86 麒麟候选 DEB 高效制包流水线全量实施计划

> **计划文件目标路径：** `docs/superpowers/plans/2026-08-19-kylin-amd64-candidate-pipeline.md`
> 当前处于 Plan Mode，尚未实际写入文件；退出 Plan Mode 后，第一步将把本方案原样写入该路径。

**目标：** 将现有成熟的 `99 → 00 → 01` 黄金制包流程封装成统一、稳定、可恢复的 x86 麒麟候选 DEB 流水线。

**架构：** Mac 作为控制端，真实麒麟 x86_64 终端作为制包机。第一阶段在 12 小时内完成本地薄执行器和模拟全链；麒麟恢复连接后执行真实候选构建；只有真实日志证明缓存是主要瓶颈时，才进入性能优化。

**技术边界：** Python 3.8+、Bash、SSH/SCP、现有 builder-input verifier、现有 `99/00/01`。不复制打包逻辑，不在第一阶段改造缓存和依赖体系。

---

## 一、最终使用方式

日常入口统一为：

```bash
cd /Users/bwb/Documents/工作/taiji-agentv1.0
./taiji-package doctor
./taiji-package build
```

辅助命令：

```bash
./taiji-package plan
./taiji-package status --run <run-id>
./taiji-package fetch --run <run-id>
```

未来不同平台仍共用 `taiji-package`，但使用独立适配器：

```text
kylin-amd64    # 本轮实现
kylin-arm64    # 后续单独实现
windows-x64    # 后续单独实现
```

不把三个平台的构建逻辑混入同一个脚本分支树。

## 二、完整流水线

```text
Mac 明确指定的正式仓库
  ↓
本地 doctor：main、HEAD、clean、接口和 SSH 配置
  ↓
在线 doctor：麒麟架构、dpkg、sudo、磁盘和系统能力
  ↓
展示 commit、主机、网络、输出和影响并确认
  ↓
生成或复用当前 commit 输入三件套
  ↓
创建唯一远程 run 目录
  ↓
传输并在远端先验证三件套
  ↓
执行冻结输入中的 00
  ↓
00 构建 amd64 DEB 并调用冻结 01
  ↓
取回 review tree 和构建日志
  ↓
本地复核 commit、DEB、SHA、manifest、marker
  ↓
状态：候选 DEB 已构建
```

严格不进入：

- 安装候选；
- 离线生命周期；
- 图形验收；
- N-1；
- certification；
- 签名；
- 客户发布。

## 三、统一入口合同

### `doctor`

```bash
./taiji-package doctor
```

只检查本地：

- 只使用操作员明确提供的仓库路径，不扫描其他目录；
- branch=`main`；
- HEAD 为完整 commit；
- worktree clean；
- packaging interface、99、00、01 存在；
- SSH alias `kylin` 可被本机 SSH 配置解析；
- 状态和产物根可写。

当前麒麟无法连接时输出：

```text
CONTROLLER_READY
BUILDER_UNREACHABLE
```

在线检查：

```bash
./taiji-package doctor --online
```

验证：

- SSH 连通；
- Linux x86_64；
- dpkg architecture=amd64；
- apt/dpkg；
- glibc 基线；
- `sudo -n`；
- 磁盘不少于 12 GiB；
- inode 不少于 100000；
- `/proc`、memfd；
- 远程构建目录权限。

### `plan`

```bash
./taiji-package plan
```

输出但不执行：

- source commit；
- 三件套名称和复用状态；
- host=`kylin`；
- remote run 目录；
- 本地产物目录；
- SSH/SCP/00 命令；
- 网络和 sudo 边界；
- 停止和恢复位置。

### `build`

```bash
./taiji-package build
```

顺序：

1. 本地 doctor；
2. 在线 doctor；
3. 显示执行计划；
4. 一次确认输入准备、远程传输和候选构建三个明确阶段；
5. 生成或复用三件套；
6. 远端先验证输入；
7. 执行冻结 `00`；
8. 等待冻结 `01`；
9. 取回完整 review；
10. 本地复核；
11. 写入最终状态。

主机不可达时必须在调用 `99` 前停止。

### `status`

显示：

- run-id；
- commit；
- 当前阶段；
- 开始/结束时间；
- 主机和远程目录；
- 日志；
- 失败分类；
- 是否允许 fetch；
- DEB 路径与摘要。

### `fetch`

只用于：

```text
远端构建成功，但本地取回失败
```

不得重新执行 apt、00 或构建。

## 四、输入和输出合同

### 输入三件套

```text
taijiagent-制包机输入-<commit>.tar.gz
taijiagent-制包机输入-<commit>.manifest.json
taijiagent-制包机输入-<commit>.tar.gz.sha256
```

处理规则：

- 全不存在：确认后运行 `99`；
- 全存在且验证通过：复用；
- 部分存在：停止；
- commit 或摘要错误：停止；
- 不覆盖、不删除、不自动修复。

### 本地状态目录

```text
~/.local/state/taiji-package/runs/<run-id>/
├── run-state.json
├── controller.log
├── remote-build.log
└── review/
```

### 远程目录

```text
/home/kylin/taiji-builds/<commit>/<run-id>/
```

### 候选工作区

```text
taiji-agent_<version>_amd64.deb
taiji-agent_<version>_amd64.deb.sha256
taiji-package-manifest.json
formal-build-tests.log
构建报告.txt
.build-success
run-state.json
```

`run-state.json` 绑定：

- source commit；
- 输入三件套 basename/bytes/SHA256；
- canonical policy SHA256；
- lock 状态；
- host alias；
- 远程目录；
- DEB basename/bytes/SHA256；
- 阶段耗时和结果。

## 五、第一阶段：12 小时本地薄执行器

### Task 1：建立开发隔离和 RED

**时间：0–1 小时**

执行时创建短期 worktree：

```text
codex/kylin-amd64-candidate-pipeline
```

新增失败测试，证明当前缺少：

- 统一 CLI；
- 本地 doctor；
- candidate plan；
- 状态记录；
- transport adapter；
- fetch 恢复。

本阶段不得运行 SSH、99 或真实制包。

提交：

```text
test(packaging): define candidate pipeline contract
```

### Task 2：CLI、配置和状态模型

**时间：1–3 小时**

新增：

```text
taiji-package
scripts/taiji-package-candidate.py
packaging/pipeline/targets/kylin-amd64.json
tests/test_taiji_package_candidate.py
```

职责：

- `taiji-package`：固定 Python 启动入口；
- candidate 脚本：命令解析和阶段编排；
- target JSON：非敏感平台默认配置；
- 测试：CLI 和状态合同。

测试：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q \
  tests.test_taiji_package_candidate
```

预期：

```text
OK
```

提交：

```text
feat(packaging): add candidate pipeline interface
```

### Task 3：本地 Doctor 和 Plan

**时间：3–4 小时**

实现：

- 明确仓库路径；
- main/HEAD/clean；
- interface/99/00/01；
- SSH alias 静态解析；
- 输出路径检查；
- JSON doctor 结果；
- plan 只输出不执行。

状态：

```text
CONTROLLER_READY
BUILDER_UNREACHABLE
BLOCKED
```

测试：

- clean main；
- dirty main；
- 非 main；
- 接口缺失；
- SSH alias 缺失；
- builder unreachable。

提交：

```text
feat(packaging): add candidate readiness planning
```

### Task 4：三件套识别和复用

**时间：4–5 小时**

实现：

- 根据当前 commit 计算三件套名称；
- 调用现有 builder-input verifier；
- 合法时复用；
- 部分存在或错误时停止；
- 未确认时不得运行 99。

测试：

- 三件都不存在；
- 合法三件套；
- 缺 archive/manifest/sidecar；
- commit 错；
- checksum 错；
- 重复同 commit。

提交：

```text
feat(packaging): reuse verified frozen builder input
```

### Task 5：Transport 与远程构建框架

**时间：5–8 小时**

新增：

```text
tests/test_taiji_package_transport.py
```

实现：

```text
RealSshTransport
FakeSshTransport
```

本地命令固定使用参数数组：

```text
/usr/bin/ssh
/usr/bin/scp
/bin/bash -p
/usr/bin/python3 -I -B
```

远程步骤：

1. 创建 run 目录；
2. 传输三件套；
3. 远端验证三件套；
4. 解压冻结输入；
5. 执行冻结 `00`；
6. 准备 review；
7. 取回 review；
8. 取回日志。

Fake 测试覆盖：

- 全链成功；
- SSH 失败；
- verifier 失败；
- 00 失败；
- 01 失败；
- SCP 中断；
- SHA 不一致；
- 不得调用安装、签名或发布脚本。

提交：

```text
feat(packaging): automate remote candidate build transport
```

### Task 6：状态与 Fetch 恢复

**时间：8–9 小时**

实现：

- 每阶段写入状态；
- 构建成功但取回失败时标记 `FETCH_PENDING`；
- `fetch` 只重试取回和验证；
- 本地产物存在时不得覆盖；
- 非远程成功状态不得 fetch。

测试：

- fetch 成功；
- 远程未成功拒绝；
- SHA 不一致拒绝；
- 本地目录占用拒绝。

提交：

```text
feat(packaging): recover candidate artifact retrieval
```

### Task 7：文档与兼容门禁

**时间：9–11 小时**

更新：

```text
docs/runbooks/taiji-kylin-uos-offline-delivery.md
.agents/skills/taiji-kylin-packaging/SKILL.md
tests/python38_linux_packaging_gate.py
```

补充：

- 日常命令；
- 当前离线状态；
- 真机阶段授权；
- 候选状态标签；
- 明确不包含安装和发布。

验证：

```bash
bash -n taiji-package
python3 -m py_compile scripts/taiji-package-candidate.py
python3 -m unittest -q \
  tests.test_taiji_package_candidate \
  tests.test_taiji_package_transport \
  tests.test_linux_golden_orchestrator
git diff --check
```

Python 3.8：

- 本机有真实 Python 3.8 时运行正式 gate；
- 没有时运行 3.8 grammar gate并明确记录真实 runtime 未验证；
- 不用当前 Python 冒充真实 3.8。

提交：

```text
docs(packaging): document fast kylin candidate workflow
```

### Task 8：最终复核与交接

**时间：11–12 小时**

检查：

- 目标文件全部提交；
- 无产品业务代码变更；
- `99/00/01` 核心无修改；
- 无真实 SSH/制包；
- worktree clean；
- 输出验证命令和结果；
- 输出未验证项。

本地状态：

```text
已实现，本地模拟通过
真实麒麟连接未验证
候选 DEB 未构建
```

不 push、不创建 PR、不合并。

## 六、第二阶段：麒麟连接恢复后

### Step 1：只读 Doctor

```bash
./taiji-package doctor --online
```

不安装、不传输、不制包。

### Step 2：专项授权

必须分别列出：

#### SSH 与传输

- host=`kylin`；
- source commit；
- 三件套 basename/bytes/SHA；
- 传输方向；
- 远程目录；
- 失败保留方式。

#### 依赖与网络

- `00` 可能运行的 apt/sudo；
- 固定工具下载；
- 网络边界；
- 文件系统影响；
- 失败停止位置。

#### 候选构建

- commit；
- build host；
- output；
- 网络和依赖边界；
- 不继续到安装、验收、签名和发布。

### Step 3：真实候选

```bash
./taiji-package build
```

成功标签：

```text
候选 DEB 已构建
```

输出：

- commit；
- DEB basename；
- bytes；
- SHA256；
- host；
- 总耗时；
- 日志位置。

预计：

- 环境已就绪：30–90 分钟；
- 需 apt/下载：1–3 小时。

## 七、第三阶段：按真实瓶颈优化

真实候选成功后分析：

```text
apt 时间
工具下载时间
npm/uv 下载时间
编译时间
正式测试时间
传输时间
```

仅当下载或依赖安装为主要瓶颈时，才实施：

- build profile 单一配置；
- apt readiness；
- Python/Node/Electron 固定归档缓存；
- npm/uv 下载缓存；
- `00` prepared 模式。

禁止缓存：

- 解压源码；
- BUILD_ROOT；
- venv；
- node_modules；
- 候选 DEB；
- manifest 和 marker。

该阶段不属于 12 小时完成标准。

## 八、最终验收标准

### 12 小时本地阶段

- CLI 可执行；
- doctor 准确报告 builder unreachable；
- plan 完整；
- fake 成功链通过；
- 主要失败路径通过；
- fetch 模拟恢复通过；
- Python/shell/diff 门禁通过；
- 仓库无无关变更；
- 有聚焦本地提交；
- 不声称已生成候选。

### 真机阶段

- online doctor 通过；
- 当前 commit 三件套绑定；
- 真实 `00/01` 通过；
- review 取回成功；
- 本地/远程 DEB SHA 一致；
- 状态为“候选 DEB 已构建”；
- 未安装、未验收、未签名、未发布。

## 九、明确延期范围

不纳入本轮：

- 麒麟 ARM；
- Windows；
- GitHub Actions；
- 自动 setup；
- 持久缓存；
- 离线安装；
- 图形验收；
- N-1；
- certification；
- 私钥签名；
- 客户发布；
- 自动远程清理。

这些能力不得阻塞 x86 麒麟候选流水线交付。
