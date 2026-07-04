# docx-template-skill

This package is the copyable skill wrapper for DOCX Engine V2. Host products
should call the scripts in `scripts/`. Those scripts delegate to `engine/` while
preserving the supported invocation contract.

## Runtime Requirement

The lite package requires a host runtime with Node.js 20 or newer and permission
to execute skill scripts. Run:

```bash
node scripts/self-test.js --out-dir <writable-output-dir>
```

A usable installation prints `self-test-ok` and writes smoke DOCX files for the
built-in templates.

## Host Invocation

When no template has been selected, list templates first:

```bash
node engine/src/cli/list-templates.js --json
```

Render a full delivery package:

```bash
node scripts/apply-template.js --template-id general-proposal --source <source.md|source.txt|source.docx> --asset-dir <asset-dir> --out-dir <delivery-dir> --json
```

The legacy single-DOCX path is still available, but the delivery directory beside
the output is the traceable source of truth:

```bash
node scripts/apply-template.js --template-id general-proposal --source <source.md> --asset-dir <asset-dir> --out <output.docx>
```

Validate, replay, and record WPS/Word visual acceptance:

```bash
node scripts/validate-delivery.js --delivery-dir <delivery-dir> --json
node scripts/replay-delivery.js --delivery-dir <delivery-dir> --json
node scripts/record-wps-visual.js --delivery-dir <delivery-dir> --status passed --note "checked in WPS" --json
```

## Template Maintenance

Do not edit engine/template-registry.json by hand. Do not copy only
`template.docx` into `engine/templates`.

Create, validate, smoke-render, then install a template package:

```bash
node scripts/scaffold-template.js --from general-proposal --template-id <new-template-id> --name "<template-name>" --out-dir <template-package-dir> --json
node scripts/validate-template.js --package <template-package-dir> --json
node scripts/render-template-sample.js --package <template-package-dir> --out-dir <empty-smoke-dir> --json
node scripts/install-template.js --package <template-package-dir> --json
```

Each installed package must contain `template-install-report.json`. Runtime
template listing rejects installed templates without that report.

## Visual Approval Boundary

`quality-report.json` keeps WPS/Word visual inspection as `not_verified` until a
human opens `document.docx` and records acceptance. Script success is not final
visual approval.

For the exact host contract, read `skill-invocation-contract.md`.
