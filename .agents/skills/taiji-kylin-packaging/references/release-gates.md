# Release Gates

Use this reference to choose an evidence label; it does not execute or replace repository gates.

## State ladder

| State | Minimum current evidence | Does not prove |
| --- | --- | --- |
| 制包输入已准备 | Clean reviewed source and verified same-commit frozen input | Candidate DEB exists |
| 候选 DEB 已构建 | Strict build, formal tests, physical payload checks, and terminal preflight bind one DEB | Offline install or real desktop passed |
| 离线安装已演练 | Same DEB passed declared no-network lifecycle and controlled upgrade scope | Real Kylin desktop passed |
| 目标机已验证 | Same DEB passed the declared real-terminal scope | Untested systems passed |
| 发布前证据门禁已闭合 | Required lifecycle, target, CI, signatures, and release check bind one identity | Publisher ran |
| 客户单 DEB 已发布 | Publisher atomically created exactly-one-DEB output and internal receipt | Other channels/environments received it |

## Required separations

- Frozen input, build workspace, internal evidence archive, and customer directory are distinct.
- Certification and publication approvals/signatures are distinct.
- Container rehearsal, build-host lifecycle, and real graphical target evidence are distinct.
- Current evidence, implemented-but-unrun code, and historical clues are distinct.

## Identity checks

At every applicable gate bind source commit, policy identity, DEB basename, bytes, SHA256, tool identities, formal-test log, and evidence inventory. Any changed upstream identity invalidates all dependent stages. Never reuse old screenshots, logs, signatures, or JSON because their shape looks compatible.

## Candidate requirements

The actual candidate must contain the source-authorized Linux x86_64 Electron and Python/Web/CLI runtimes, close native dependencies according to policy, avoid runtime CDN dependencies, preserve product privacy, and agree on package/manifest/CLI version. A source-only test does not prove these physical properties.

## Publication boundary

The customer output has one regular non-symlink file named `taiji-agent_<version>_amd64.deb`, byte-identical to the signed candidate. No README, checksum, manifest, script, evidence, signature, source, or local package repository is shipped alongside it.

If a current gate is absent, label it `未实时验证`. If only code exists, label it `已实现，未实时验证`. If only an old artifact/log exists, label it `历史线索`.
