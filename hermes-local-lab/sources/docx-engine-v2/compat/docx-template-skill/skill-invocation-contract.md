# Skill Invocation Contract

This file defines the host-facing contract for `docx-template-skill`.

## Entry Points

Use wrapper scripts from the skill root. Host products should not call
`engine/src/rendering/*`, `engine/src/planning/*`, or other internal modules
directly.

Required runtime:

- Node.js 20 or newer
- permission to execute `.js` scripts
- write access to the requested output directory

Self-test:

```bash
node scripts/self-test.js --out-dir <writable-output-dir>
```

## Template Selection

If the host has not collected a template id from the user, call:

```bash
node engine/src/cli/list-templates.js --json
```

If rendering is attempted without `--template-id`, the engine returns a JSON
payload with:

```json
{"ok":false,"code":"template_selection_required"}
```

The host must show the returned templates and ask the user to choose one. The
host must not silently default to `general-proposal` after this response.

## Rendering

Canonical render command:

```bash
node scripts/apply-template.js --template-id <template-id> --source <source.md|source.txt|source.docx> --asset-dir <asset-dir> --out-dir <delivery-dir> --json
```

Success returns `ok: true` and writes a delivery package. The package directory,
not chat text, is the source of truth.

Failure returns `ok: false`. If a job was created, the delivery directory can
contain `job.manifest.json` and `failure-report.json` for traceability.

## Delivery Gate

Every delivery package must include:

- `document.docx`
- `delivery-package.json`
- `source.md`
- `source-package.json`
- `source/original/...`
- `assets/`
- `asset-package.json`
- `job.manifest.json`
- `template.manifest.json`
- `render-plan.json`
- `quality-report.json`
- `README-图片调整说明.md`

Validate and replay before claiming package integrity:

```bash
node scripts/validate-delivery.js --delivery-dir <delivery-dir> --json
node scripts/replay-delivery.js --delivery-dir <delivery-dir> --json
```

`quality-report.json` can be `passed_with_warnings` while `wps_visual` is
`not_verified`. That is acceptable for automated validation, but it is not final
WPS/Word visual approval.

Record human acceptance only after opening `document.docx` in WPS or Word:

```bash
node scripts/record-wps-visual.js --delivery-dir <delivery-dir> --status passed --note "checked in WPS" --json
```

## Template Installation Gate

Template packages must be installed through the installer:

```bash
node scripts/validate-template.js --package <template-package-dir> --json
node scripts/render-template-sample.js --package <template-package-dir> --out-dir <empty-smoke-dir> --json
node scripts/install-template.js --package <template-package-dir> --json
```

Updating an installed template requires:

```bash
node scripts/install-template.js --package <template-package-dir> --replace --json
```

Do not edit `engine/template-registry.json` by hand. Do not copy only
`template.docx` into an installed template directory.

Installed templates must contain `template-install-report.json`. The report must
show passed checks for `template_package`, `sample_render`, and `registry_entry`.
Runtime template listing rejects installed templates when the report is missing
or does not match the registry entry.

## Editable Asset Workflow

For editable figure assets before rendering:

```bash
node scripts/package-rich-draft.js --source <source.md> --asset-dir <asset-dir> --out-dir <package-dir>
```

For a stable DOCX image replacement:

```bash
node scripts/replace-docx-image.js --docx <input.docx> --figure-id fig-001 --image <replacement.png|svg> --out <updated.docx>
```

The host should use `figureId` from `render-plan.json` or the asset manifest.
Caption text alone is not a stable image identifier.
