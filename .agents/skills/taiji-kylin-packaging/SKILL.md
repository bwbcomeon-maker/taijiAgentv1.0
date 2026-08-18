---
name: taiji-kylin-packaging
description: Use when Taiji Agent needs x86_64 Linux Kylin, UOS, or openKylin DEB packaging, frozen build-input diagnosis, offline-install planning, target acceptance, release-evidence review, or packaging failure recovery.
---

# Taiji Kylin Packaging

## Core principle

Treat packaging as an identity-bound release lifecycle. A source commit, frozen input, candidate DEB, test log, compatibility policy, acceptance evidence, signatures, and customer artifact are different objects; never infer a later state from an earlier one.

This Skill is a self-contained router and diagnostic guide. The Taiji repository and its frozen source-controlled scripts remain the executable authority. Do not recreate their build, validation, signing, or publication logic inside this Skill.

## Choose exactly one input mode

Do not scan the disk for a repository or artifact.

仓库场景的答复必须明确说明：**只对操作员明确提供的路径运行 `doctor.py --repo PATH`，不得扫描其它目录**。

1. For a source checkout, run `python3 -I -B scripts/doctor.py --repo <operator-supplied-path>` from this Skill root.
2. For a build host that only has the frozen input trio and no `.git`, run `python3 -I -B scripts/doctor.py --input-dir <operator-supplied-directory>` from this Skill root. The supplied directory must contain **恰好一个同一 commit 的 `tar.gz`、`manifest.json` 与 `tar.gz.sha256` 三件套**。
3. For the bundled diagnostic itself, run `python3 -I -B scripts/doctor.py --selftest` from this Skill root.

Invoke `scripts/doctor.py` through a known Python 3.8+ interpreter with `-I -B`. Read its one JSON result before proposing any next action. The doctor does not execute repository code, install tools, verify the full frozen archive, or authorize an action.

Interpret its fields literally:

- `compatibility_status=pass`: the supplied input is structurally recognizable at the stated scope.
- `blocked`: repair the listed blocker before continuing.
- `unsupported`: the input is not this workflow's repository or schema.
- `evidence_scope`: facts actually observed by the doctor.
- `unverified`: gates that still require their authoritative implementation.
- `approval_required`: approvals still required before the reported next action.

## Authority order

When a Taiji repository is present, follow this order:

1. The user's current explicit boundaries and approvals.
2. Project `AGENTS.md` and `docs/runbooks/development-lifecycle.md`.
3. `packaging/linux/taiji-packaging-interface.json`.
4. The interface-named orchestrator, builder-input entry, build-host entry, and canonical runbook.
5. This Skill's references for routing and explanation.

Read versions, hashes, stage definitions, compatibility policy, target matrix, and evidence schemas from the frozen repository. Never copy remembered values into a command or treat this Skill as a second authority.

The six-field `taiji-packaging-interface/v1` contract keeps preparation and construction separate:

- `builder_input_entry` (`taijiagent 打包交付/99_本机_准备制包输入包.sh`) prepares the frozen trio from an accepted source checkout. Doctor 的 repo 模式只能把 99 报告为下一步，且仍需要当轮专项授权。
- `build_host_entry` (`taijiagent 打包交付/00_制包机_生成离线交付包.sh`) consumes that frozen trio on the controlled Linux build host. Never skip directly from repo inspection to 00.

## Authorization boundary

兼容检查通过不等于已获执行授权。Orchestrator `READY`, an existing script, a historical approval, or the user's word “继续” also does not authorize an external or privileged action.

| Action | Default | Required approval scope |
| --- | --- | --- |
| Read-only inspection, doctor, selftest, local static/unit checks, plan or dry-run | Allowed within the user's local scope | None beyond the current task |
| Download or install a public tool | Stop | Tool, source, machine, filesystem impact, rollback |
| `sudo` or system dependency installation | Stop | Exact command class, machine, impact, rollback |
| SSH or file transfer | Stop | Host alias, objects, direction, hashes, destination |
| 实际 DEB 制包 | Stop | Source commit, build host, output, network/dependency boundary, rollback |
| Install, upgrade, remove, or target acceptance | Stop | DEB basename/bytes/SHA256, target, lifecycle actions, data impact, rollback |
| Certification or publication 签名 | Stop | Exact evidence identity, key role, signer, output |
| Customer-directory 发布 | Stop | Exact DEB hash, channel/directory, audience, rollback |
| Destructive cleanup | Stop | Exact owned targets and recovery proof |

Each approval covers only the named stage. A build approval does not authorize installation; acceptance does not authorize signing; signing does not authorize publication. An explicit current prohibition on network, SSH, packaging, installation, signing, or publication always wins.

当“继续”夹带一个或多个外部或特权阶段时，必须同时完成三件事：

1. 逐项拒绝执行所有未获当轮专项授权的阶段。
2. 只提供只读诊断或计划，不发起下载、SSH、sudo、制包、安装、签名或发布。
3. 为用户夹带的每个阶段分别输出一个待授权块，不得合并成一句“需要批准”：
   - `待授权阶段`：本次被拒绝的单一阶段。
   - `精确身份`：从上表逐项给出对象、commit/版本/制品或证据 SHA256、主机/签名者/渠道和输入输出。
   - `影响范围`：网络、文件系统、系统依赖、安装态、证据态或客户目录中会变更的精确范围。
   - `回滚与停止条件`：失败时如何恢复、哪个检查点必须停止，以及不会自动继续到的下游阶段。

## Four artifact roles

Keep these physically and semantically separate:

| Role | Consumer | Contract |
| --- | --- | --- |
| Frozen build input | Controlled Linux build host | Same-commit `tar.gz`, manifest, and sidecar; internal only |
| Build/acceptance workspace | Automation and reviewers | Toolchains, candidate, logs, validators, and working evidence |
| Internal evidence archive | Release reviewers | Frozen evidence, signatures, raw records, and publisher receipt |
| Customer artifact | Customer/operator | 客户目录恰好只有一个 DEB named `taiji-agent_<version>_amd64.deb` |

Do not ask a customer to copy source, hashes, manifests, scripts, logs, acceptance tools, local package repositories, or signing material.

## Evidence labels

Use only the narrowest label supported by current evidence:

- `制包输入已准备`
- `候选 DEB 已构建`
- `离线安装已演练`
- `目标机已验证`
- `发布前证据门禁已闭合`
- `客户单 DEB 已发布`

Code or tests present without a current successful run: `已实现，未实时验证`. Old artifacts or logs: `历史线索`. An unrun gate: `未实时验证`.

Local source tests cannot prove a Linux DEB exists. A build-host install cannot prove a clean first graphical installation. One real Kylin terminal cannot prove every Kylin/UOS/openKylin release. A signature cannot repair stale or incomplete evidence.

## Route the task

| Situation | Read |
| --- | --- |
| Current DEB/build-host/customer flow | `references/deb-offline-delivery.md` |
| Screenshot, log, or interrupted run | `references/failure-playbook.md` first |
| Readiness and evidence claims | `references/release-gates.md` |
| Hermes, secrets, logs, processes, diagnostics | `references/privacy-surface-gate.md` |
| Positively identified historical v2 package | `references/kylin-deb-version-history.md`, then `references/failure-playbook.md` for the current v3 diagnostic path |
| Installing this Skill in another Agent product | `references/agent-installation.md` |

跨产品安装场景的答复必须逐字明确两点：**`.skill` 是 Codex 的便利安装包**；**其它 Agent 产品只有经过实际测试后才能标记为已验证**。不得把源目录映射说明冒充已验证的原生安装兼容性。

## Failure response

For every failure, report:

```text
失败层：<frozen input/build workspace/internal evidence/customer DEB>
失败分类：<stable category>
已确认根因：<fact or 证据不足>
影响范围：<what evidence or stages are invalid>
可复用修复：<one source-controlled repair entry>
重试点：<earliest valid checkpoint>
成功证据：<current commands/artifacts required>
禁止操作：<unsafe shortcut>
状态：<已实时验证/未实时验证/历史线索>
```

Fix product or packaging defects in reviewed source, add a regression, freeze a new commit, and restart from the earliest invalidated stage. Never patch an unpacked build-host source tree or installed payload in place.

For a separately approved target acceptance, the executable trust root is the installed, root-owned `/usr/bin/taiji-agent-acceptance`; a copied `04` directory can carry data but is not an equivalent executable authority.

## Hard prohibitions

- Do not disable Kysec or weaken platform security to make a package pass.
- Do not use root supervisors, temporary UIDs, or privileged isolation for local formal tests unless the user separately approves that design.
- Do not download, SSH, package, install, sign, or publish merely because the capability exists.
- Do not execute an operator-supplied repository to discover its interface.
- Do not treat a generic `.skill` archive as a universal installer for every Agent product.
- Do not claim this Skill alone can build without a Taiji repository or its frozen input.
