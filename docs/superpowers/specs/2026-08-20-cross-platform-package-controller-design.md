# Taiji 跨平台候选制包控制器设计

日期：2026-08-20
状态：已批准设计，待按分计划实施
唯一正式仓库：`/Users/bwb/Documents/工作/taiji-agentv1.0`

## 1. 目标与当前事实

本设计把 Linux 麒麟候选 DEB 和 Windows x64 候选 EXE 收敛到同一仓库、同一入口和同一状态模型，同时保留两个独立平台适配器。统一的是控制层，不是把 Bash、PowerShell、DEB 和 Inno Setup 混成一套平台分支树。

当前可确认的起点如下：

- 正式 `main` 为 `5364233e1297e5f2837382823d4e35a0d114aba7`，其中尚无 `taiji-package`。
- 麒麟本地实现位于 `codex/kylin-amd64-candidate-pipeline@a5a36849bca009d1cfb07ac2309532a502c6bd70`，相对 `main` 领先 9 个提交。
- 麒麟本地模拟和合同测试已经通过；真实在线 doctor、SSH、`99/00/01` 和候选 DEB 均未执行。
- Windows 历史快车道有可取用的 PowerShell、Inno Setup 和门禁经验，但历史 EXE 不是当前候选证明。
- Windows 第一阶段的唯一成功标签是 `候选 EXE 已构建`。该标签不代表安装、桌面验收、生产授权、签名或发布。

## 2. 第一性原理

1. **候选制包的输入必须可重建。** 每次运行绑定一个完整 source commit 和一组经摘要验证的冻结输入，不能从 dirty 工作树或远端残留目录直接制包。
2. **控制层与平台实现分离。** CLI、锁、状态、阶段、确认和恢复属于 core；系统探测、远程命令、输入包、制品验证属于 adapter。
3. **状态只陈述已经发生的事实。** `plan` 不创建运行，`status` 不联网，`FETCH_PENDING` 只表示远端构建已成功而本地取回尚未完成。
4. **失败必须停在证据仍可审计的位置。** 不覆盖、不自动修复部分输入、不自动清理远程 run、不在缓存缺失时联网降级。
5. **物理上只保留一个权威仓库。** 旧 Windows 仓只作为有来源约束的迁移材料；最终运行不得依赖 sibling checkout。
6. **候选、安装、验收、授权、签名、发布分层。** 本轮只实现候选构建链，不能用某一层的证据替代下一层。

## 3. 固定开发拓扑

```text
/Users/bwb/Documents/工作/taiji-agentv1.0                 # 唯一正式仓库，main 暂不修改
└── .worktrees/
    ├── kylin-amd64-candidate-pipeline                   # 原 Linux 实现，暂停并保留
    └── cross-platform-package-controller                # 本轮统一控制器开发
```

统一控制器的固定身份为：

```text
branch:   codex/cross-platform-package-controller
baseline: a5a36849bca009d1cfb07ac2309532a502c6bd70
worktree: /Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/cross-platform-package-controller
```

本设计不授权 push、PR、merge、Tag、Release、真实制包或删除旧仓。

## 4. 最终目录与职责

```text
taiji-package                              # 稳定的 Bash 启动入口
scripts/taiji-package-candidate.py         # 兼容 shim；旧调用仍可用
packaging/pipeline/
├── cli.py                                 # 参数解析和命令分派
├── core/
│   ├── errors.py                          # PipelineError 和稳定错误类别
│   ├── registry.py                        # 固定 target/adapter 注册表
│   ├── state.py                           # v1 读取兼容、v2 写入、锁和日志
│   ├── orchestration.py                   # build/fetch 阶段机
│   └── models.py                          # 平台中立的 plan/state 元数据辅助函数
├── adapters/
│   ├── base.py                            # CandidateAdapter 合同
│   ├── kylin_amd64.py                     # 现有 Linux 行为迁入，不改 99/00/01
│   ├── windows_x64.py                     # Windows doctor/input/plan/review
│   └── windows_ssh.py                     # 独立 Windows SSH/PowerShell transport
└── targets/
    ├── kylin-amd64.json
    └── windows-x64.json
packaging/windows/
├── asset-provenance.json                  # 被取用旧资产的来源 commit/blob/mode/hash
├── cache-requirements.json                # Windows 离线缓存类型/路径/成员合同
├── legacy-assets/                         # 三个来源 Git object 的只读精确快照
├── verify_legacy_assets.py                # selected Git object 与 snapshot verifier
├── Initialize-CandidateSession.ps1        # 参数化的 Windows run 会话
├── Stage-CandidatePayload.ps1             # 已验证 tar source 的离线 payload 组装；不调用 Git
├── Build-CandidateReview.ps1              # verifier、payload、Inno、review 编排
├── builder_input_package.py               # Windows 冻结输入三件套 create/verify
├── safe_tar.py                             # 解压前独立传输并核 SHA 的安全 tar helper
├── import_product_source.py               # 远端产品 bundle 审计和 archive ref 安装
└── TaijiAgent.iss                         # x64 单文件未签名候选安装器
```

不引入动态插件发现、第三方框架或新服务。Python 边界保持 3.8+。

## 5. 统一 CLI 合同

```bash
./taiji-package [全局参数] doctor [--online]
./taiji-package [全局参数] plan
./taiji-package [全局参数] build
./taiji-package [全局参数] status --run <run-id>
./taiji-package [全局参数] fetch --run <run-id>
```

全局参数固定为：

```text
--repo PATH
--target TARGET
--state-root PATH
--ssh-config PATH
--json
```

解析规则：

1. `doctor/plan/build` 未指定 `--target` 时，兼容地使用 `kylin-amd64`。
2. `TARGET` 精确等于 `kylin-amd64` 或 `windows-x64` 时，加载仓库内置 JSON。
3. 其他值只允许解析为明确存在的绝对 JSON 文件路径；相对路径一律拒绝，其中 `target_id` 仍必须属于固定注册表。
4. target JSON 只能提供数据，不得指定或加载任意 Python 类、脚本或命令。
5. `status` 只读取 run-state 中的平台，不加载默认 target，不连接远端。
6. argparse 中 `--target` 的原始默认值必须是 `None`，以区分“未提供”和“显式覆盖”。`fetch` 未显式提供时只用 run-state 中冻结的 target；显式提供时，ID 与配置摘要必须完全一致。
7. `plan` 不 SSH、不创建状态、不生成输入。
8. `build` 先通过本地和在线 doctor，再显示计划并要求操作员输入精确字符串 `BUILD`。取消时不创建 run-state，也不调用输入准备。

最终调用方式：

```bash
./taiji-package --target kylin-amd64 doctor
./taiji-package --target kylin-amd64 build

./taiji-package --target windows-x64 doctor
./taiji-package --target windows-x64 build
```

## 6. Adapter 与 Transport 合同

固定注册表：

```python
ADAPTER_FACTORIES = {
    "kylin-amd64": KylinAmd64Adapter,
    "windows-x64": WindowsX64Adapter,
}
```

最小 adapter 接口：

```python
class CandidateAdapter:
    target_id: str
    artifact_kind: str
    success_label: str
    pending_label: str
    not_built_label: str
    online_plan_keys: tuple

    def validate_target(self, payload):
        raise NotImplementedError

    def local_doctor(self, repo, target, state_root, *, ssh_config):
        raise NotImplementedError

    def inspect_input(self, repo, source_commit):
        raise NotImplementedError

    def build_plan(self, repo, target, state_root, *, run_id, ssh_config):
        raise NotImplementedError

    def bind_online_plan(self, plan, online):
        raise NotImplementedError

    def prepare_input(self, plan, command_runner):
        raise NotImplementedError

    def create_transport(self, repo, target, *, ssh_config, command_runner):
        raise NotImplementedError

    def validate_review(self, plan, review, remote_log):
        raise NotImplementedError

    def initial_state_patch(self, plan, online):
        raise NotImplementedError

    def success_state_patch(self, artifact):
        raise NotImplementedError

    def normalize_legacy_state(self, state):
        raise NotImplementedError
```

`build_plan()` 只生成本地计划，不 SSH。`online_doctor()` 返回 `BUILDER_READY` 后，core 必须且只调用一次 `bind_online_plan(plan, online)`。校验算法不是挑选若干字段比较：先要求所有 `online_plan_keys` 在原 plan 中不存在、返回对象新增键集合与该 tuple 精确相等，再从返回对象删除这些新增键并与原 plan 做完整深相等；因此 version、asset provenance、controller bootstrap、commands、boundaries、input 等任何既有字段都不能漂移。Kylin 的 tuple 为空并返回原 plan 的深拷贝；Windows 绑定 cache requirements/observation、host facts 和其 SHA。随后展示、确认并用这份 online-finalized plan 创建 state；state 创建后除 `plan.input` 的一次 MISSING→REUSABLE 原子绑定外均冻结。绑定后产生的 bound execution plan 才由 transport、review validator 和 v2 fetch 消费。非 ready doctor、单独 `plan`、`status` 和 `fetch` 不调用 online hook。

transport 保持现有六阶段外形：

```python
online_doctor()
create_remote_run(plan)
transfer_input(plan)
verify_remote_input(plan)
build_remote_candidate(plan)
fetch(plan, staging_dir)
```

`fetch()` 内部必须分别记录 `fetch-review` 与 `fetch-log`；两者都成功后才进入本地验证。Linux 与 Windows 分别实现 transport；不得在同一真实 transport 中按操作系统堆叠分支。CLI 的唯一签名固定为 `main(argv=None, *, adapter_factory, command_runner, input_reader, publisher)`，五个参数必须在设计、fake 测试和 facade 中保持一致。facade 用仓库内模块级 factory 适配现有 monkeypatch seam，任何测试 runner 发现 `/usr/bin/ssh` 或 `/usr/bin/scp` 都立即失败。

顶层启动器继续使用 `/usr/bin/python3 -I -B scripts/taiji-package-candidate.py`。兼容 shim 必须只把 `Path(__file__).resolve().parents[1]` 解析出的精确仓库根插入 `sys.path`，随后校验实际导入的 `packaging.pipeline` 位于该根；不得依赖当前工作目录、`PYTHONPATH` 或同名 site-packages。所有会以 `python3 -I -B <script>` 直接执行且需要仓库模块的 Python helper 采用同一精确 bootstrap；纯自包含 helper 则不得隐式导入仓库模块。入口验收必须从仓库外 cwd 分别运行 launcher、shim 和每个直接执行 helper 的 `--help`。

## 7. Run-state v2

新运行只写 `taiji-package-run-state/v2`：

```json
{
  "schema": "taiji-package-run-state/v2",
  "run_id": "20260820T120000Z-abcdef123456-deadbeef",
  "target_id": "windows-x64",
  "target_config": {},
  "target_config_sha256": "<64 lowercase hex>",
  "source": {
    "repo_root": "/Users/bwb/Documents/工作/taiji-agentv1.0",
    "branch": "main",
    "commit": "<40 lowercase hex>",
    "tree": "<40 lowercase hex>"
  },
  "identity": {
    "controller_commit": "<40 lowercase hex>",
    "asset_provenance_sha256": null,
    "input_manifest_sha256": null,
    "cache_requirements_sha256": null,
    "cache_observation_sha256": null,
    "host_facts_sha256": null
  },
  "stage": "PLANNED",
  "status_label": "候选 EXE 未构建",
  "created_at": "<UTC ISO-8601>",
  "updated_at": "<UTC ISO-8601>",
  "started_at": "<UTC ISO-8601>",
  "finished_at": null,
  "host": {
    "alias": "windows-direct",
    "architecture": "x64",
    "remote_run_dir": "D:\\tw\\taiji-builds\\<commit>\\<run-id>"
  },
  "paths": {"local_run_dir": "<absolute path>"},
  "input": {"status": "MISSING", "files": {}},
  "policy": null,
  "remote_build_succeeded": false,
  "fetch_allowed": false,
  "artifact": null,
  "failure": null,
  "stage_history": [],
  "lock": {"status": "released"},
  "logs": {
    "controller": "<absolute path>",
    "remote_build": "<absolute path>"
  },
  "plan": {}
}
```

v2 只能由 `new_run_state(plan, online, adapter)` 工厂创建；`RunStateStore.create()` 拒绝缺少上述顶层字段或错误类型的 payload，类别为 `PLAN_INVALID`。target 摘要算法固定为：对 adapter 校验后的完整 JSON object 使用 `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))` 的 UTF-8 bytes 计算 SHA256，不加换行。`target_id`、`target_config`、该 SHA、source 全字段、controller commit、host alias/remote run、local run 和 `plan` 中除 `plan.input` 外的全部字段在创建后不可变；identity 中初始为 null 的字段只允许原子写入一次，之后不可修改。`input` 可在 `PLANNED` 阶段由 `MISSING` 一次更新为实际 `REUSABLE` identity，但该更新必须由 state store 在同一次原子写中同步更新顶层 `input`、`plan.input` 和 `identity.input_manifest_sha256`，三者任一不一致都拒绝。进入 `INPUT_VERIFIED` 前必须写入三件套 basename/bytes/SHA 和 input manifest SHA，此后顶层与 plan 中的完整 input object 都冻结。所有远端 transport、review validator 和 v2 fetch 只消费 state 中这份已绑定的 execution plan，不能继续使用创建 state 前的 MISSING plan 局部变量。

成功的通用制品字段固定为：

```json
{
  "kind": "exe",
  "basename": "TaijiAgent-Setup-<version>-win-x64.exe",
  "bytes": 123,
  "sha256": "<64 lowercase hex>",
  "path": "<absolute local path>",
  "relative_path": "<path below review>"
}
```

v1 兼容规则：

- v1 只代表 `kylin-amd64`。
- `source_commit`、`canonical_policy_sha256` 和 `deb` 的只读映射只存在于 `KylinAmd64Adapter.normalize_legacy_state()`；通用 core 不解释这些字段。
- `status` 不改写 v1 文件。
- v1 `FETCH_PENDING` 沿用兼容取回路径，不在后台升级格式。
- Windows 不允许创建 v1 状态。

阶段固定为：

```text
PLANNED
INPUT_VERIFIED
REMOTE_RUN_CREATED
INPUT_TRANSFERRED
REMOTE_INPUT_VERIFIED
REMOTE_BUILD_SUCCEEDED
REVIEW_FETCHED
LOCAL_REVIEW_VERIFIED
CANDIDATE_BUILT
FETCH_PENDING
FAILED
```

远端构建成功前失败进入 `FAILED`；成功后取回、验证或本地无覆盖发布失败进入 `FETCH_PENDING`。只有 `FETCH_PENDING` 允许 `fetch`，且 `fetch` 只能重复 `fetch-review`、`fetch-log`、本地验证和无覆盖发布。

本地 publisher 必须可重入：review 和 remote log 都不存在时按固定顺序无覆盖发布；崩溃后只存在其中一个时，只有该已存在组件的完整安全 tree/bytes/SHA 与本次已验证 staging 完全相同才视为已发布并继续补齐另一个；两者都存在且完全相同则幂等返回；任一不一致才报 `LOCAL_OUTPUT_OCCUPIED`。success patch 计算失败或最终 state 原子写失败时保持 `FETCH_PENDING`，下一次 fetch 必须能通过同一 identity 收敛，不能因为自己上一次留下的精确输出而永久拒绝。不得覆盖、删除或自动修复不匹配的既有输出。

## 8. Windows x64 固定合同

内置 target：

```json
{
  "schema": "taiji-package-target/v2",
  "target_id": "windows-x64",
  "host_alias": "windows-direct",
  "architecture": "x64",
  "remote_root": "D:\\tw\\taiji-builds",
  "cache_root": "D:\\tw\\cache",
  "cache_requirements": "packaging/windows/cache-requirements.json",
  "minimum_free_gib": 20,
  "allowed_source_branches": ["main"],
  "powershell": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
  "git": "C:\\Program Files\\Git\\cmd\\git.exe",
  "tar": "C:\\Windows\\System32\\tar.exe",
  "node": "C:\\Program Files\\nodejs\\node.exe",
  "npm": "C:\\Program Files\\nodejs\\npm.cmd",
  "python": "D:\\tw\\cache\\python-runtime\\python.exe",
  "iscc": "C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe"
}
```

配置不得出现 IP、密码、私钥、公钥全文或个人凭据。

Windows 候选版本的唯一来源是 source commit 内根目录 `VERSION` 的单行 `X.Y.Z\n`；同一 commit 的 `apps/taiji-desktop/package.json.version` 必须逐字相等，只作一致性门禁，不是第二来源。CLI/target 不提供版本覆盖参数。adapter 从 exact source commit 读取并把 `version` 冻结进 finalized plan、输入 manifest、session、package manifest 和制品文件名；任一缺失、非三段数字或不一致以 `PLAN_INVALID`/`INPUT_VERIFICATION_FAILED` 停止。

冻结输入三件套：

```text
taijiagent-windows-builder-input-<commit>.tar.gz
taijiagent-windows-builder-input-<commit>.manifest.json
taijiagent-windows-builder-input-<commit>.tar.gz.sha256
```

sidecar 精确包含 archive 与 manifest 两行 `SHA256  basename`。archive 是确定性的 Git tree tar+gzip；manifest 的 `created_at` 是运行证据，不参与“相同 commit 重建必须同字节”的承诺。helper 在创建和远端解压前拒绝绝对路径、`..`、symlink、hardlink、gitlink 和非 regular/directory member；目标 source 目录必须全新且不存在。tar 解压后是无 `.git` 的冻结 source tree，Windows staging 禁止执行 Git。

首次 `plan` 若三件套全不存在，只展示 `MISSING`、三个预期 basename 和“确认后由 build 准备”；此时不得伪造 bytes/SHA。操作员输入 `BUILD` 一次性授权输入准备、传输和候选构建，prepare 完成后必须把实际 basename/bytes/SHA 追加到 controller log，并以 write-once 方式写入 run-state，再继续远端阶段。partial 或 identity 错误一律停止。

远程唯一 run：

```text
D:\tw\taiji-builds\<commit>\<run-id>\
```

review 根目录精确合同：

```text
TaijiAgent-Setup-<version>-win-x64.exe
TaijiAgent-Setup-<version>-win-x64.exe.sha256
taiji-package-manifest.json
formal-build-tests.log
构建报告.txt
.build-success
run-state.json
```

`remote-build.log` 与 review 目录并列，单独取回。三个 JSON/marker schema 固定如下：

- `taiji-package-manifest/v2`：run/target/source commit+tree、输入 archive+manifest SHA、target/config SHA、asset provenance SHA、cache requirements/observation SHA、绝对工具身份、payload entries+manifest SHA、固定七项 ordered formal build validation 的 id/result/exit code 与非空八行 log、EXE basename/bytes/SHA、PE machine=`0x8664`、PE optional magic=`0x20b`、FileVersion/ProductVersion、Authenticode status=`NotSigned`；空日志或手写 `status=PASS` 无效。
- `taiji-package-remote-run/v1`：run-id、target-id、source commit、host facts SHA、有序 stage history、terminal status=`REMOTE_BUILD_SUCCEEDED`。
- `.build-success` 为 canonical JSON `taiji-package-build-success/v1`，绑定 package manifest、artifact、tests log、report 和 remote state 的 SHA；它必须最后原子创建。

local validator 逐项验证这三者、EXE sidecar、payload manifest、PE x64、版本一致和 `Get-AuthenticodeSignature=NotSigned`，然后才可写 `候选 EXE 已构建`。

`packaging/windows/cache-requirements.json` 使用 `taiji-windows-cache-requirements/v1`，精确固定三项：`npm-cache`（`npm/` 目录）、`electron-39.8.10-win32-x64`（`electron/electron-v39.8.10-win32-x64.zip` regular file，解包后必须含 `electron.exe`）、`private-python-runtime`（`python-runtime/` 目录，必须含 `python.exe` 与 `python311._pth`）。每项只含 id、相对路径、type、架构/版本和 required members；实际逐文件 bytes/SHA 由只读 doctor 生成 `taiji-windows-cache-observation/v1` 并绑定 requirements SHA，不把机器特定观测提交进 Git。observation 仍记录 `observed_at`，但 `cache_observation_sha256` 只对删除 `observed_at` 后的其余 exact object 做 canonical JSON SHA256，因此同一缓存字节在不同时刻重核得到相同 identity。online doctor 只读验证 Windows/x64、target 中每个绝对工具路径、NTFS、20 GiB、缓存清单，并返回完整 observation、requirements SHA 和上述 observation SHA；只读 ACL 观察不得声称实际可写。真实可写性只在获授权创建唯一 run 时证明。缺失返回 `WINDOWS_CACHE_MISSING/BLOCKED`，不得下载或安装。

## 9. Windows 资产取用规则

旧仓 `/Users/bwb/Documents/工作/taiji-agentv1.0-win` 不参与最终运行，也不合并其 Git 历史。资产只从下列 Git object 读取：

```text
source commit: f33663f7e3ffee672d39af7b4ecbe9fd2869a00b
```

第一批候选资产：

| 来源路径 | mode | blob | SHA256 | 处理方式 |
| --- | --- | --- | --- | --- |
| `scripts/windows/Initialize-FastTrackSession.ps1` | `100644` | `f792452ab6b3d2b95a1d2fd9e9badc5c71923cf2` | `49b5081d36ece563db5ecaafc9696dde31e86a4f73f60a3fe5e6898b2cbd4ee0` | 参数化后迁入 |
| `scripts/windows/Stage-WindowsPayload.ps1` | `100644` | `17ba9b8fde890a112aa9882d17bf097247d4c910` | `fbe32f4494d97e00b37e67627b106b08b840e34f449b2b2ebffedfcddcc54198` | 保留成熟门禁，去掉硬编码/覆盖行为 |
| `installer/TaijiAgent.iss` | `100644` | `ce11f481b6399deec0b436e0e13326d6a692253d` | `f6e1934c4aa8cffd948896cd7c72524138aaf1fa7515193637d6af9863cb0505` | 参数化后迁入 |

迁入前写 `asset-provenance.json`，并用专用 `verify_legacy_assets.py` 核 source commit/path 的 Git tree mode/blob、`cat-file` bytes/SHA 以及 snapshot 的 owner/type/mode/hash。`scripts/check-imported-source-tree.py` 是整树导入 gate，不适用于本次 3/14 的选择性快照；旧仓退休改用全路径处置表和全 refs bundle。禁止从当前 dirty Windows worktree 递归复制；其中未跟踪的 `docs/.DS_Store` 没有迁移价值。

`codex/phase-a-foundation@e4102f82798cafca664f128d0cab88cf0ab8ff41` 只取错误码、规范 JSON、安全写入测试思想，不整体 merge/cherry-pick，也不引入 Python 3.11 要求和过重发布模型。

产品源码导入是一次性迁移门禁，不属于日常 builder doctor。`import_product_source.py` 固定提供 `probe`、`fetch`、`verify`、`install-ref`、`inventory` 五个子命令：`probe` 只在 Gate R1 的 `READ_ONLY` 授权后读取远端 Git 身份且不写入；`fetch` 在独立 Gate R2 的 `IMPORT` 授权后通过 PowerShell transport 生成并取回 bundle；`verify` 只写本地私有 import run；`install-ref` 仍属同一次 R2 授权，按无覆盖规则把已验证 tip 安装为主仓私有 `refs/archive/windows-product/<tip>`；`inventory` 只读取已验证 manifest 并输出 allowlist 和逐 commit 证据。正式应用前必须在临时 clone 试应用完整序列；每个来源 commit 记录旧 SHA、stable patch-id、新 SHA、结果 path/mode/blob/SHA。正式分支冲突时记录 pre-import HEAD、已应用映射和下一 commit，停止且不得 reset。

日常 Windows builder doctor 只检查系统、固定绝对工具、磁盘、文件系统和离线缓存，不再依赖远端旧产品 repo。产品 repo 的 branch/HEAD/clean 只由上述一次性 `probe` 检查。

## 10. 实施顺序与门禁

```text
1. Kylin 暂停 handoff
   ↓ 本地文档合同通过
2. 通用 core + Kylin adapter
   ↓ 原有 71+56 测试及新增兼容测试通过
3. Windows 资产 + fake adapter
   ↓ fake 成功/失败/fetch 全链通过
4. Windows 产品源码导入 + 正式 main 集成 + 真实单机候选
   ↓ R1只读、R2导入、R3集成、R4构建四个独立授权；候选 EXE 已构建
5. 旧 Windows 仓退休
   ↓ 需再次人工授权；先归档后可恢复移除
```

计划 1—3 只允许本地修改、测试和提交。计划 4 的 R3 由主 Agent按 development lifecycle 在用户另行授权后完成正式 main 集成；R4 必须从 clean、已复验的正式 main 重新生成输入并构建。计划 4 的动态证据写入 `~/.local/state/taiji-package/runs/<run-id>/`，不在已经 clean 的正式 main 上追加证据提交。计划 5 的退休工具先在独立分支完成并标准集成；该集成改变正式 main HEAD 后，必须再次执行计划 4 的 R4，只有新候选 `source.commit == 当前正式 main HEAD` 才能进入旧仓物理处理。

旧仓退休的静态 policy 只描述资产处置和门禁规则；当前 main、candidate state 和 archive inventory 必须作为审计命令的显式绝对路径/commit 参数。物理处理前必须用 `git clone --mirror` 实际恢复全 refs bundle，且 retirement 合同本身已经按 development lifecycle 进入并复验正式 main。目录移动只允许同一文件系统、目标不存在、父目录为当前用户所有且非链接的场景，并在报告中给出精确反向 `mv` 命令。

## 11. 完成标准与不在范围

本轮统一控制器实现完成至少要求：

- 单一 `taiji-package` 能按 ID 分派两个 adapter。
- Kylin 原有本地合同和 v1 状态/fetch 不回归。
- Windows fake 全链与主要失败路径通过。
- Windows 真机产生绑定 clean 正式 main HEAD 的未签名 x64 单机候选 EXE，并取回一致 SHA。
- 没有任何运行时依赖旧 Windows sibling 仓。

明确延期：Windows 安装、交互 UI、production license、签名、SmartScreen、版本矩阵、离线生命周期、发布；Kylin 真机、安装、认证、签名和发布；ARM、GitHub Actions、持久缓存和自动远程清理。读取 Authenticode 状态以证明 `NotSigned` 属于候选验证，不等于执行签名。

## 12. 稳定失败类别

```text
PIPELINE_BLOCKED
TARGET_INVALID
REPO_INVALID
REPO_IDENTITY_MISMATCH
BRANCH_NOT_MAIN
SOURCE_COMMIT_INVALID
WORKTREE_NOT_CLEAN
PACKAGING_INTERFACE_INVALID
PACKAGING_ENTRYPOINT_MISSING
SSH_ALIAS_MISSING
STATE_ROOT_UNWRITABLE
RUN_LOCKED
RUN_LOCK_FAILED
STATE_WRITE_FAILED
BUILDER_UNREACHABLE
ONLINE_DOCTOR_BLOCKED
CONFIRMATION_REQUIRED
PLAN_INVALID
INPUT_PREPARATION_REQUIRED
INPUT_VERIFICATION_FAILED
INPUT_PREPARATION_FAILED
INPUT_TRIPLET_PARTIAL
COMPATIBILITY_POLICY_INVALID
SSH_FAILED
SCP_INTERRUPTED
REMOTE_VERIFY_FAILED
BUILD_00_FAILED
BUILD_01_FAILED
REMOTE_BUILD_FAILED
LOCAL_PREFLIGHT_FAILED
SOURCE_DRIFT
LOCAL_REVIEW_INVALID
ARTIFACT_SHA_MISMATCH
LOCAL_OUTPUT_OCCUPIED
LOCAL_OUTPUT_UNWRITABLE
LOCAL_PUBLISH_FAILED
FETCH_NOT_ALLOWED
WINDOWS_CACHE_MISSING
WINDOWS_ARCHIVE_UNSAFE
WINDOWS_PAYLOAD_FAILED
WINDOWS_INNO_FAILED
SOURCE_IMPORT_BLOCKED
```

以上先冻结现有 Kylin 类别，再增加不冲突的 Windows 类别。`RETIRE_READY`/`RETIREMENT_BLOCKED` 是退休审计状态；`CURRENT_CANDIDATE_MISSING`、`ARCHIVE_NOT_VERIFIED`、`CANDIDATE_NOT_FORMAL_MAIN_HEAD`、`UNCLASSIFIED_TRACKED_PATH` 等只存在于退休审计结果的 `blockers`，均不是 `PipelineError.category`。执行者不得临时创造近义 PipelineError 类别；新增类别必须先修改本设计和合同测试。
