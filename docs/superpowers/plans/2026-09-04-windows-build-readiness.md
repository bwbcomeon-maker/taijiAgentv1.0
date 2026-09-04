# Windows 制包环境与完整负载修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 本任务按项目规则在正式 main 单写入执行，最终 Sol 只读审核；不创建分支，不自动进入 BUILD。

**Goal:** 让 Windows 制包预检反映真实运行能力，补齐当前产品所需依赖，并明确候选包和完整交付的验收边界。

**Architecture:** 保留 `taiji-package`、独立 Windows adapter、冻结输入三件套、只读共享缓存和 fetch-only 恢复流程。先执行轻量运行探针，再做全量缓存哈希；依赖准备与离线构建分离，旧缓存保留，新环境必须复验后切换。

**Tech Stack:** Python 3.11 x64、PowerShell 5.1、OpenSSH、Node 22/24、Electron 39.8.10、Inno Setup。

## 来源与授权

- 基线：正式仓库 clean `main@4fb50be325a4f961dd9ad1c728bdb616de33d29e`；本任务为唯一写入者。
- 当前授权：用户已明确允许 Windows 下载、安装必要环境/依赖及冲突替换，并允许 Mac 下载后传入；正式制包仍须绑定修复后 clean commit 并通过 BUILD 确认。
- 不做：系统 Node 替换、旧缓存删除、真实配置迁移、安装/启动产品、GUI/业务验收、签名、Tag、Release。

## 已实时验证的缺口

1. 现有 Windows 在线 doctor 返回 `BUILDER_READY`，但只检查工具路径，未执行工具。固定 Node 实测为 `v20.20.0`，不满足当前 DOCX 的 Node 22/24 合同。
2. `Stage-CandidatePayload.ps1` 未复制同级 `docx-engine-v2`，也未携带独立 Node；Windows 启动 PATH 仅含 Python 和 System32。单独升级制包机 Node 不能修复安装包。
3. SSH 的 Wi-Fi 连接已验证，仓库默认别名仍为直连入口；使用独立 `--ssh-config` 映射即可，无需提交个人 IP 或改写旧连接。
4. 手册仍写“真实 EXE 从未构建”，与历史成功记录不符；历史成功不证明本轮源码完整可用。

## Task 1：修复运行环境预检

**Files:** `packaging/pipeline/adapters/windows_runtime_probe.py`、`packaging/pipeline/adapters/windows_ssh.py`、`tests/test_windows_runtime_readiness.py`、`tests/test_taiji_package_windows_real_transport.py`。

- [x] 先增加失败用例：Node 20、错误架构、Python 非 3.11、必要模块不可导入、探针非零退出/无效 JSON 必须阻断；不允许进入耗时缓存扫描。
- [x] 新增轻量只读 PowerShell 探针，实际执行目标配置指定的 Node/Python/npm/Inno；Python 通过编码传入脚本避免 Windows 引号丢失，禁写 bytecode。
- [x] `online_doctor()` 先验证运行探针，再保留既有缓存观察/schema/哈希流程；不改变历史 run 的 fetch 恢复协议。
- [x] 执行 Windows 聚焦回归，记录 RED/GREEN；当前 Windows 探针准确报告 Node 20 阻断，详见验证台账。

## Task 2：准备独立、可回退的依赖环境（已取得主机准备授权）

- [x] 新建版本化 Node 22.23.1 制包缓存，Mac 下载官方 Windows x64 包并校验官方 SHA256后传入；不覆盖系统 Node 20。
- [x] 18 项 Python core 版本匹配、上海时区通过；实际授权模块导入发现缺少 pywin32，按 uv.lock 的 311 版本先在独立 runtime 离线安装并验证，再补入共享缓存。未安装 python-docx/pip 到产品。
- [x] Desktop 和 DOCX 两套锁分别完成 Windows 在线准备及离线 ci，Windows resvg 原生模块与实际图表生成通过。
- [x] 工具版本、lock 摘要和准备方法进入手册/台账，目标改为私有 Node；旧 Node 保留。缓存变化后的完整 observation 留到 clean commit 重新生成，不复用旧计划。

## Task 3：修复完整负载闭包

**Files:** `packaging/windows/cache-requirements.json`、`Stage-CandidatePayload.ps1`、`Build-CandidateReview.ps1`、`apps/taiji-desktop/src/windows-runtime.js` 及对应合同/运行测试。

- [x] 增加 DOCX、私有 node.exe、Windows 原生依赖和虚假成功的回归测试。
- [x] Stage 从冻结源码复制引擎/模板/registry，按缓存观察校验 Node 字节，run 私有 npm cache 离线装配依赖；真实 PowerShell 5.1执行新增装配区段通过。
- [x] 仅允许 DOCX 目录内 node_modules，保留其他禁令，装配后生成 manifest；新增区段实际卫生扫描通过。
- [x] Windows runtime 固定 Node/DOCX和用户模板库，消除继承旧模板目录；保留 Linux 版本约束。实际发现 UTF-8/GBK 通信缺陷并修复。
- [x] Inno 前原有七项检查内添加 Python→Node模板枚举和有效DOCX生成检查；隔离真机生成140842字节含图表DOCX通过。完整Stage/Inno执行仍属于后续 BUILD，不以区段测试冒充。

## Task 4：收口与独立交付门禁

- [x] 更新 Windows 手册：连接入口、预检含义、依赖准备/回退、错误分类、真实历史与当轮证据。
- [ ] 聚焦测试后运行一次 `scripts/verify.sh --full`，固定已准备的 Node 22/24；只读 Sol 审核完整暂存内容，按项目规则 commit/fetch/push。
- [ ] 用户授权修复后 clean commit 的 BUILD 才制作新 EXE，记录 source/payload/installer 摘要和 fetch 结果。
- [ ] 安装、升级、卸载、桌面业务验收、生产授权和签名各自独立核对授权；全部完成前不得标为正式交付成熟。

## 停止条件

源码修复和聚焦验证不代表环境准备完成。没有依赖准备授权时仅完成不依赖该授权的预检修复；在状态卡列出未执行项。无法获得必要验证或最终 Sol 审核时停在提交前。完整成熟必须由修复后的实际候选构建及独立安装/业务验收证明。
