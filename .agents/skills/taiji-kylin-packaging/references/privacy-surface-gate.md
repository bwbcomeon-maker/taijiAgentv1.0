# Privacy Surface Gate

Ordinary users must see Taiji product names and sanitized diagnostics, not internal source lineage, credentials, build material, or private release evidence.

## Review surfaces

- Final DEB payload and installed `/opt/taiji-agent` tree.
- CLI, desktop/AppStream metadata, window/taskbar/icon resources.
- Process command lines and environment.
- Runtime directories, logs, diagnostics, support exports, and visible errors.
- Web static files, local storage keys, headers, and offline assets.
- Customer-facing instructions and the final customer directory.

Compatibility identifiers may remain in internal source where required. Do not expose them in ordinary-user paths, log names, process arguments, environment names, desktop files, or documentation.

## Never package

- `.env`, API keys, tokens, passwords, private keys, customer endpoints, or credentials.
- User sessions, databases, attachments, command history, or local configuration.
- Build caches, test results, source archives, absolute build-host paths, old packages, or signing material.
- Internal acceptance tools, raw evidence, manifests, sidecars, or logs in the customer directory.

## Verification scope

Source grep alone is insufficient. A real privacy gate scans the staged payload, actual DEB, installed tree, processes/environment, logs, diagnostics, and customer inventory for the exact candidate. If those artifact/installed checks did not run, report `未实时验证`.

Diagnostics should collect only the minimum support facts, sanitize paths and secrets, record collection failures, and keep restrictive permissions. Never hide a real product error as a successful empty response.
