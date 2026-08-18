# Current DEB Offline Delivery

Use this reference for the current single-DEB route on graphical Debian-like Kylin, UOS, and openKylin `x86_64`/`amd64` systems.

## Supported boundary

- Linux `x86_64`/`amd64` only.
- `dpkg/apt`, a usable graphical package installer, and administrator authorization are required.
- The target must satisfy the frozen repository's canonical compatibility policy.
- ARM/aarch64, RPM-only systems, systems without a usable package manager, and policies outside the certified matrix need another artifact or certification.

Support design is not verification. Name a distribution/version as verified only when the exact DEB has current evidence from that declared environment.

## Source-checkout path

1. Run doctor in `--repo` mode on the operator-supplied checkout.
2. Require the repository identity, branch, worktree state, declarative interface, and referenced entrypoints to be accepted.
3. Obtain specific approval before generating a frozen build input.
4. Use only the repository's formal input producer; do not manually assemble the trio.
5. Transfer the same-commit archive, manifest, and sidecar together, recording basename, bytes, and SHA256 before and after transfer.

## No-Git build-host path

1. Run doctor in `--input-dir` mode on a dedicated directory containing exactly the trio.
2. Run the reported read-only sidecar check.
3. Treat that as transport verification only; the frozen `00` entry and its helpers remain responsible for formal archive/member/source validation.
4. Obtain separate approval for dependency installation, network use, sudo, and actual DEB construction.
5. Do not edit extracted source on the build host. A source defect returns to local review, a new commit, and a newly frozen input.

## Formal build contract

- Run the frozen source-controlled build-host entry with strict locks.
- Read tool versions and hashes from the frozen source; never use ambient PATH tools as substitutes.
- Require the build script and its terminal artifact-required preflight to return zero for the same candidate.
- Write the success marker last, after candidate, sidecar, manifest, report, formal test log, and physical preflight are mutually bound.
- Build-host networking may obtain only source-authorized bytes; customer installation remains offline.

## Internal acceptance and customer boundary

Internal automation owns no-network lifecycle rehearsal, controlled upgrade evidence, desktop cycles, compatibility records, raw evidence, CI, signatures, and publication gates.

After installation, formal target acceptance starts from the root-owned `/usr/bin/taiji-agent-acceptance`. A copied internal delivery directory supplies data, never executable authority.

The publisher alone creates the customer directory. That directory contains exactly:

```text
taiji-agent_<version>_amd64.deb
```

The customer copies and double-clicks that DEB. Internal scripts, manifests, hashes, logs, source, evidence, signatures, and private keys remain internal.
