# Windows 制包环境核查与预检修复台账

## 来源与范围

- 源码基线：正式 `main@4fb50be325a4f961dd9ad1c728bdb616de33d29e`，开始时 clean；本轮仅主任务写入。
- 目标：用户确认主机密钥的 Windows x64，使用 Mac 现有专用密钥经局域网 SSH 登录。个人地址、密钥和完整缓存清单不入库。
- 操作：只读探针、本地代码/文档与测试；未下载、安装依赖、生成候选、安装或启动产品。

## 已实时验证

| 检查 | 结果与边界 |
| --- | --- |
| SSH | 免密远程命令成功，主机密钥已由用户在 Windows 对照 |
| OS | Windows 10 家庭中文版，10.0.19044，x64；不外推系统支持/安全认证 |
| 制包盘 | D 盘 NTFS，空闲 341512609792 bytes，超过当前 20 GiB 门槛 |
| 原版在线 doctor | `CONTROLLER_READY` / `BUILDER_READY`；三个缓存完整，但仅检查工具存在而漏过不支持的 Node |
| 旧缓存身份 | requirements `48459a7a05210d03b2b7f0ed7f95c063dbcc9fa17b8415d2e7a302be972b6971`；observation `9cf2cf9b4ec64ddee17289763201e873b17e67e45281d91b580d1be12f4cca26`；仅此时点有效 |
| Node / npm | 目标固定路径实测 `v20.20.0,x64` / `10.8.2`；Node 不满足当前文档引擎的 22/24 约束 |
| Python | 固定私有 Python 3.11.9 x64；aiohttp、fastapi、uvicorn、yaml、cryptography、psutil、pypdf 实际导入均成功；未证明全依赖版本与当前 lock 一致 |
| Inno | 实际运行返回 Inno Setup 6 Command-Line Compiler 帮助；`/?` 返回 1 是本机帮助响应，不标为编译失败，也不标为真实编译通过 |
| 修复后的在线调用链 | `WINDOWS_RUNTIME_NOT_READY Node must be 22/24 x64; observed v20.20.0,x64`；在缓存扫描前停止，准确暴露原版遗漏 |

Python 探针用 Base64 编码脚本解决 PowerShell 5.1 原生参数引号丢失；原生退出码读取 global 作用域，避免局部初始化遮蔽真实退出码。上述行为有真机复验和回归用例。

## 历史制品复核

本机历史 run `20260821T162253Z-cf18cee091d7-d381678d` 绑定源码 `d381678d3402b7299d11d7f1205e3d16b2b0d7f8`，记录候选 `TaijiAgent-Setup-1.0.2-win-x64.exe`，233449378 bytes，SHA256 `f860c89993e02b35f568bff1e6843e1a907a7cc91a7c5c2c64ba7bd2dc71bcb4`。这属于旧提交的 Installer 证据，不代表本轮重建、安装态或业务验收。

## 本地验证

- 聚焦：`python3 -B -m unittest tests.test_windows_runtime_readiness tests.test_taiji_package_windows_real_transport tests.test_taiji_package_windows_adapter tests.test_taiji_package_windows_transport tests.test_windows_packaging_script_contract -q`：93 tests，PASS。
- 全部 Windows 合同：`python3 -B -m unittest discover -s tests -p '*windows*.py' -q`：157 tests，19.782s，PASS；均为本地隔离测试，不冒充真实 EXE/安装验收。
- RED/GREEN：新增运行探针缺失、Inno 帮助非零退出、PowerShell 退出码作用域均先保留失败，再最小修复并通过。
- 广泛验证：`scripts/verify.sh --full` 使用已准备 Node v24.19.0。根目录 1327 项（3 skipped）、Desktop 79 项、DOCX 278 项通过；Agent 阶段因沙箱禁止 socket bind 导致 23 项失败，原始命令退出 1，**不记录为一键全量 PASS**。全量开始后补充的 Inno/退出码小修由最终 Windows 157 项覆盖。
- 按相同注册范围补跑：仅解除本地测试 socket 限制后，Agent 6 文件 220 项通过；WebUI runtime lint 通过，WebUI 注册套件 952 项通过，启动/共存 75 项通过。
- CLI/Bootstrap 补充套件的旧 `run_tests.sh` 清除隔离变量，收集时触及日常凭据锁，被沙箱阻止。未放开该路径；改用其同一 `run_tests_parallel.py`，清空凭据环境并显式绑定全新临时 `HERMES_HOME`、`TAIJI_RUNTIME_HOME`、`TAIJI_ACCOUNT_HOME`，得到 36 passed、5 skipped。此为已有验证包装器隔离问题，不改动日常凭据，也不在本轮扩展修改该包装器。
- 上述续跑补齐本次 `--full` 注册门禁；没有重跑已通过的耗时根目录套件。原始失败与续跑结果分别保留，不能外推为 Windows 制包、安装或产品功能验收。
- 本轮原始日志保留在 Mac `/private/tmp/taiji-windows-readiness-*.log`；安全扫描和 `git diff --cached --check` 通过。

## 未完成与下一步

1. 第一阶段当时未取得依赖准备授权；后续已取得并执行，见下方第二阶段记录。
2. 第一阶段尚缺 DOCX 装配闭包；第二阶段已实现和隔离验证，完整 Stage/Inno 仍需 clean source 的 BUILD。
3. 修复后 clean commit 的候选构建、真实安装/升级/卸载、桌面业务、生产授权、签名和发布均未执行。
4. 当前成果是预检修复，不是“Windows 完整交付环境已成熟”。

## 第二阶段：已授权的依赖准备与负载闭包修复

来源：`main@beeda70e4c4df9000539cd52e93665815684e842` 加本轮明确工作树改动，主 agent 唯一写入，Sol 只读预审。用户明确允许 Windows 下载/安装所需依赖、冲突替换，并允许 Mac 下载后传入。没有执行产品安装、系统 Node 替换、签名、发布或真实 BUILD。

### 已实时验证的主机准备

- 官方 Node 22.23.1 x64（npm 10.9.8）在独立版本目录安装。Windows 直连迟迟未返回，Mac 官方下载/校验后 SSH 传入成功；原直连后来完成，最终执行文件摘要仍与官方一致。旧 Node20 保留。
- Desktop lock SHA256 `f7106ea15c112100ae305a3675400013486ea2c808868784d4a2bfbe59db2819`、DOCX lock SHA256 `66b966028f1d0522950dcbaf46bd092133d1b8c680522e0363e2ff3b08e4b28e`；Windows 分别在线准备和 `npm ci --offline --ignore-scripts --no-audit --no-fund` 成功，安装71/18包。
- Python 3.11.9 x64：18项核心依赖版本全部匹配，Asia/Shanghai可用。运行时不带pip，最初`pip check`无法运行；改用`importlib.metadata`逐个核对已安装发行包的生效依赖，结果无缺失/冲突，未为检查而把pip装进产品。
- 实际 Python→DOCX 调用发现授权模块依赖 `win32api` 缺失，导致诊断层 fail-closed 进入production分支。未绕过授权判断；按现有uv.lock固定的pywin32 311从官方PyPI在Mac下载，验证wheel SHA后先装隔离Python、功能通过后补入共享私有Python。未做系统postinstall、未改System32。
- 共享环境新探针实际通过 Node/npm/Python/Inno；11个Python imports含4个win32模块均为true。该轻量结果不代表全量缓存观察或CLI clean-source门禁通过。
- 最终独立主机全量观察返回`BUILDER_READY`，blockers为空，4类cache均存在。requirements SHA=`67fc1651c0f0c4f3c7543b35594df68f7e5df84887af5f04f42933b028055477`，observation SHA=`304e8ffc71deac2cd3d62526c1325866f1f1f73cfd7753f77401d0537390aeec`。迟到直连下载的重复Node已移出cache保留备份，最终Node目录99503183 bytes。该观察是诊断证据，不作为未提交源码的BUILD许可。

### 已实时验证的修复

- Windows runtime固定私有Node和DOCX模板目录；预审发现继承`TAIJI_DOCX_RUNTIME_HOME`可串入旧模板库，新增污染回归RED后显式覆盖大小写变体。
- Stage新增区段在真实PowerShell5.1的全新scratch中执行：离线装配18包、Node字节核对、Windows resvg文件检查、白名单卫生扫描，返回`REAL_STAGE_DOCX_AND_HYGIENE_OK`。只执行新增区段，不冒充完整Stage/manifest/Inno验证。
- 实际Node模板输出为UTF-8，Windows Python默认GBK造成`UnicodeDecodeError`；API进程通信改为显式UTF-8，版本/生产路径约束不变。回归3项RED后通过，受影响DOCX API完整60项PASS。
- 用隔离源码/私有Python/私有Node运行`docx_payload_smoke.py`，断言Windows candidate profile、实际模块和Node来源，经Python API枚举8模板并生成含图表的DOCX，140842 bytes，成功JSON、质量/重放状态及ZIP成员检查通过。未打开Word/WPS或浏览器，不是文档视觉验收。
- 新增正式门禁helper从冻结源码读取，保留原七项formal checks和历史fetch协议；在Inno前验证DOCX，临时生成目录自动清理。
- 实际PowerShell包装器同样通过；对9626个DOCX/runtime文件进行生成前后摘要比较完全一致。反向真机测试证明Node观察摘要篡改、白名单外node_modules均被阻断。

### 第二阶段本地验证与边界

- Windows聚焦162项PASS，Desktop runtime污染路径回归6项PASS；新增装配和虚假成功测试先RED再GREEN。
- Agent全量注册范围采用同一个`run_tests_parallel.py`，空环境+独立runtime/account目录，256项PASS、5 skipped；避免旧包装器丢弃隔离变量而触及日常凭据。
- WebUI全量注册范围含bootstrap/coexistence：1027项PASS（1项audioop弃用warning）。
- `scripts/verify.sh --full`：根目录1332项（3 skipped）、Desktop79项、DOCX278项通过；随后Agent阶段23项因沙箱禁止socket bind失败，原始退出1，不记为一键全量PASS。以上相同注册范围的隔离Agent/WebUI续跑已补齐；WebUI runtime lint通过。全量启动后补入的pywin32、UTF-8与模板目录修复另由最终162项Windows、60项DOCX API和6项runtime聚焦覆盖。临时日志位于Mac `/private/tmp/windows-phase2-*.log`。
- 标准CLI doctor在工作树编辑期间正确以`WORKTREE_NOT_CLEAN`停止。未放宽该门禁；环境探针属于独立只读诊断，clean commit后才能形成新的正式计划和BUILD绑定。
- 保留本轮Windows依赖准备/隔离复验目录，未清理旧制品或归属不明目录。缓存变化后不能使用历史observation。

## 提交与推送边界

当前 staged 第一阶段修复接受 Sol 五视图最终审核后才允许本地提交。基线已有上个任务提交 `4fb50be3` 未推送，该任务的推送审批尚未取得用户回复；本轮不借新提交绕过此前的推送审批。不存在远端领先（本次 fetch 后本地领先 1）。后续推送需一并确认前置提交边界，不能把本地提交写成已推送。

## 第三阶段：首轮真实 BUILD 与扩展路径修复

- 上述推送边界是历史时点：用户随后批准，三个提交已正常推送，`main`、`origin/main`、GitHub 均核对为 `a6eb85f87dfdbff6b8f0edcbf169a400f607a98d`，pull 显示 Already up to date。
- 用户确认 `BUILD` 后执行 run `20260904T145200Z-6d2eaaa52795-a6eb85f8`，绑定 clean `main@a6eb85f8`、版本1.0.2、Windows x64。输入校验、传输、远端输入复核和完整 Stage 成功；Desktop 离线71包、Electron win32 x64正式检查通过。
- run 在 `payload-import-menu-policy` 失败，controller 类别为 `WINDOWS_INNO_FAILED`，Inno 实际未开始。异常是 `Build-CandidateReview.ps1` 调用 helper 时 `Join-Path` 无法解析 `source_root` 的 `\\?\` 前缀。npm 的 boolean 弃用提示并非此次阻断根因，该项正式检查已PASS。
- 第一处改为 .NET 路径组合后，实际执行到 helper，暴露其 `$PSScriptRoot` 同样携带扩展前缀；第二处也改为 `[IO.Path]::Combine`。未删除长路径支持、未放宽检查、未改缓存或产品依赖。
- 本地新增路径合同先RED再GREEN；Windows从修复脚本AST提取实际调用，在独立 `\\?\` 源码路径执行本轮真实payload：8模板、140842 bytes DOCX，`WINDOWS_PAYLOAD_DOCX_OK` 和 `EXTENDED_SOURCE_PATH_DOCX_GATE_OK`。失败run的冻结源码、日志、payload原样保留，没有覆写或重跑该run。
- 修复后Windows聚焦163项PASS。`scripts/verify.sh --full` 根目录1334项（3 skipped）、Desktop79项、DOCX278项PASS；Agent有23项因本地socket权限失败，原始退出1，不记一键全量PASS。相同注册范围采用空环境、临时runtime和同一runner，Agent256项PASS；WebUI1027项PASS（1项audioop弃用warning），runtime lint通过。日志保留在Mac `/private/tmp/windows-path-*.log`。
- 本次局部通过不能升级为 Installer 成功；新提交需重新生成计划并取得 `BUILD`，不自动安装、签名或发布。
