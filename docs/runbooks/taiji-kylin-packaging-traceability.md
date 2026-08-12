# Taiji 国产 x86_64 黄金制包流程追踪矩阵

本矩阵把黄金制包流程的要求、实现入口、自动化测试和证据范围固定在一起。它是本地 DoD-A 的审计索引，不把本地门禁升级为真实 Linux/Kylin 制包或客户交付认证。

| 要求 | 实现 | 自动化验证 | 证据范围 |
| --- | --- | --- | --- |
| 固定源码、工具和交付输入 | `taijiagent 打包交付/00_制包机_生成离线交付包.sh`、`01_制包机_发布预检.sh`、`99_本机_准备制包输入包.sh`、`scripts/source-archive-integrity.py` | frozen-source、strict-toolchain、builder-input、source-integrity 合同测试 | 已实时验证：本地固定输入与摘要绑定；真实制包机未验证 |
| 正式消费者使用同一快照 | `00` 的 source/inventory/tool/Electron retained FD；`packaging/linux/deb/build-deb.sh`；`packaging/linux/stage-electron-runtime.py` | retained-snapshot、FD/路径互斥、完整性回读和污染环境测试 | 已实时验证：静态/单元闭环；Linux 真实 FD 执行未验证 |
| 正式测试完整执行闭包 | `scripts/run-taiji-formal-build-tests.py` | formal driver、formal evidence contract | 已实时验证：20 个固定目标、六 suite、零收集/跳过失败关闭 |
| 结果不可伪造、可重建 | direct driver v2 日志、`scripts/validate-taiji-release-evidence.py` | schema-v3、formal-log tamper matrix、release-check 合同测试 | 已实时验证：v2 header、target result、suite counts、overall pass 顺序 |
| 统一工具身份 | held Python/Node/npm/ESLint FD 与 `/usr/bin/python3 -I -B` | strict toolchain、execution environment、formal driver tests | 已实时验证：路径与摘要门禁；真实 Python 3.8 进程未验证 |
| Skill 能力可发现且不执行仓库代码 | `packaging/linux/taiji-packaging-interface.json`、`.agents/skills/taiji-kylin-packaging/scripts/doctor.py` | Skill/doctor contract tests、污染 Git 环境和 frozen trio 负向测试 | 已实时验证：repo/input-dir/selftest JSON/退出码合同 |
| Skill 包可重复生成且不泄漏 | `scripts/package-taiji-kylin-packaging-skill.py` | Skill packager tests、双目录字节比较、解包自检和泄漏扫描 | 已实时验证：ZIP_STORED、固定成员/权限/摘要；未安装到其他 Agent 产品 |
| 操作、授权和故障处理可复用 | `docs/runbooks/taiji-kylin-uos-offline-delivery.md`、`taijiagent 打包交付/操作说明.md`、Skill references | 文档静态检查与 Skill eval | 已实时验证：本地文档；目标机操作未执行 |
| 客户边界为单一 DEB | `packaging/linux/deb/publish-single-deb.sh` 与 release gates | single-DEB、publisher、release evidence tests | 已实时验证：合同和静态门禁；未生成或发布真实 DEB |
| 真实目标认证 | Kylin/UOS x86_64 目标机、CI、签名、发布流程 | 本轮不执行 | 未验证，属于 DoD-B |

## 证据标签

- **已实时验证**：本轮在当前 worktree 运行并得到成功结果。
- **已实现，未实时验证**：代码或文档已存在，但缺少当前环境的可执行证据。
- **未验证**：本轮明确没有执行，不能从其它测试推导。
- **历史线索**：仅供排障参考，不得绑定当前制品。

## 当前已知偏差

生产主流程已经调用 direct `formal-build-tests/v2`；`00` 中旧 root-supervisor 嵌入实现仍作为历史兼容测试代码保留，不能被 Skill 或正式文档当作当前入口。若后续删除，必须单独建立迁移提交并重跑本矩阵中所有 formal 合同测试。
