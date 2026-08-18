# Historical v2 Packaging Lessons

This is read-only history for artifacts positively identified as the old copied-directory/local-apt-repository route. It is not the current build, installation, publication, or customer contract.

## Identification

Historical v2 commonly includes a copied `taijiagent 打包交付/` tree, `02_目标终端_安装并验证.sh`, and `离线依赖/Packages` plus `Packages.gz`. Current v3 customer delivery contains exactly one DEB.

## Retained lessons

- Match the package manager to target facts; Debian-like Kylin uses DEB rather than RPM.
- Exclude Apple xattrs, PAX metadata, caches, sessions, logs, and build-host paths.
- Do not run nested `apt` while `dpkg` already owns its lock.
- Keep production dependencies narrow, locked, non-editable, and independent of a root cache.
- Separate ordinary-user state from system-service state.
- Treat stale package-owned processes, ports, ownership, and failed-install residue as lifecycle concerns.
- Write checksum sidecars with basenames, never build-host absolute paths.

Use current source-controlled tests to preserve these lessons. Do not tell a v3 customer to reconstruct the old delivery tree, copy `Packages*`, or run internal numbered scripts.
