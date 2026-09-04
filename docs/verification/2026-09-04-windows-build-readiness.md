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

1. 独立 Node 22 和 npm 锁文件缓存准备尚待主机写入/下载授权；不替换全局 Node、不删除旧缓存。
2. DOCX 引擎、私有 Node、Windows 原生依赖及装包/启动/生成门禁尚未实现，详见修复计划；不是升级构建机即可解决。
3. 修复后 clean commit 的候选构建、真实安装/升级/卸载、桌面业务、生产授权、签名和发布均未执行。
4. 当前成果是预检修复，不是“Windows 完整交付环境已成熟”。

## 提交与推送边界

当前 staged 第一阶段修复接受 Sol 五视图最终审核后才允许本地提交。基线已有上个任务提交 `4fb50be3` 未推送，该任务的推送审批尚未取得用户回复；本轮不借新提交绕过此前的推送审批。不存在远端领先（本次 fetch 后本地领先 1）。后续推送需一并确认前置提交边界，不能把本地提交写成已推送。
