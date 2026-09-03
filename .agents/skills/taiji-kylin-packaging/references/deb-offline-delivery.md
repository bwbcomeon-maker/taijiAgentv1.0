# Current DEB Offline Delivery

Use this reference for the current single-DEB route on graphical Debian-like Kylin, UOS, and openKylin `x86_64`/`amd64` systems.

## Supported boundary

- Linux `x86_64`/`amd64` only.
- `dpkg/apt`, a usable graphical package installer, and administrator authorization are required.
- The target must satisfy the frozen repository's canonical compatibility policy.
- ARM/aarch64, RPM-only systems, systems without a usable package manager, and policies outside the certified matrix need another artifact or certification.

Support design is not verification. Name a distribution/version as verified only when the exact DEB has current evidence from that declared environment.

当前 Linux 输入链仅接受已复验的 clean `main`；Linux hotfix 分支制包尚未支持。通用 hotfix 发布检查不授予该输入链能力，不得临时修改来源门禁或在远程解包树修补。

## Source-checkout path

1. Run doctor in `--repo` mode on the operator-supplied checkout.
2. Require the repository identity, branch, worktree state, declarative interface, and referenced entrypoints to be accepted.
3. Verify effective approval for generating this frozen build input; retain prior approval for the same object, stage, host and impact. Recheck changed scope rather than asking again solely because a new turn began.
4. Use only the repository's formal input producer; do not manually assemble the trio.
5. Transfer the same-commit archive, manifest, and sidecar together, recording basename, bytes, and SHA256 before and after transfer.

## No-Git build-host path

1. Run doctor in `--input-dir` mode on a dedicated directory containing exactly the trio.
2. Run the reported read-only sidecar check.
3. Treat that as transport verification only; the frozen `00` entry and its helpers remain responsible for formal archive/member/source validation.
4. Verify effective approval covering dependency installation, network use, sudo, and actual DEB construction. A scoped combined build approval can cover these named stages; it cannot authorize installation, signing or publication. Preserve the controller's exact `BUILD` confirmation.
5. Do not edit extracted source on the build host. A source defect returns to local review, a new commit, and a newly frozen input.

## Formal build contract

- Run the frozen source-controlled build-host entry with strict locks.
- Read tool versions and hashes from the frozen source; never use ambient PATH tools as substitutes.
- Require the build script and its terminal artifact-required preflight to return zero for the same candidate.
- Write the success marker last, after candidate, sidecar, manifest, report, formal test log, and physical preflight are mutually bound.
- Build-host networking may obtain only source-authorized bytes; customer installation remains offline.

## Internal acceptance and customer boundary

Internal automation owns no-network lifecycle rehearsal, controlled upgrade evidence, desktop cycles, compatibility records, raw evidence, CI, signatures, and publication gates.

按项目授权范围进行的 `kylin` 只读 SSH 能力检查无需重复确认；传输和系统变更仍分别核对授权。首次安装必须同时证明系统安装基线和用户配置基线；仅新建用户不能证明干净首次安装。局部安装/桌面成功不替代真实模型对话、附件等完整目标机业务验收，状态按 `release-gates.md` 选择。

After installation, formal target acceptance starts from the root-owned `/usr/bin/taiji-agent-acceptance`. A copied internal delivery directory supplies data, never executable authority.

The publisher alone creates the customer directory. That directory contains exactly:

```text
taiji-agent_<version>_amd64.deb
```

The customer copies and double-clicks that DEB. Internal scripts, manifests, hashes, logs, source, evidence, signatures, and private keys remain internal.
