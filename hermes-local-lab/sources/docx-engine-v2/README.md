# DOCX Engine V2

DOCX Engine V2 is the canonical document-template rendering engine behind the
copyable `docx-template-skill` package. It converts an existing source document
into a traceable editable DOCX delivery package.

## Contract

The engine is a post-processing renderer. The caller must provide an existing
source file and an explicit template id. Template selection happens before data
mapping and rendering.

The supported source types are Markdown, plain text, and DOCX. Rich proposal
templates can reject sources that do not contain enough tables or visual assets.

Do not call lower-level render modules directly from a host product. Use the
CLIs below so source normalization, asset packaging, render-plan generation,
delivery validation, replay metadata, and failure artifacts stay in one chain.

## Canonical CLI

List templates when the user has not selected one:

```bash
node src/cli/list-templates.js --json
```

Template selection responses include each template's document types,
capabilities, source requirements, required assets, quality gates, and engine
compatibility metadata. Hosts should use those fields to choose or reject a
template before preparing template data.

Render a delivery package:

```bash
node src/cli/run-job.js --template-id general-proposal --source <source.md|source.txt|source.docx> --asset-dir <asset-dir> --out-dir <delivery-dir> --json
```

If `--template-id` is missing, `run-job` returns `template_selection_required`
and does not create delivery output.

Validate a delivery package:

```bash
node src/cli/validate-delivery.js --delivery-dir <delivery-dir> --json
node src/cli/validate-delivery.js --delivery-dir <delivery-dir> --write-report --json
```

The CLI validates final delivery packages and requires the package to include a
hash-bound `replay-report.json`. Internal pre-replay staging validation is kept
inside the workflow API.

Replay a delivery package from its packaged original source:

```bash
node src/cli/replay-delivery.js --delivery-dir <delivery-dir> --json
```

Record human WPS/Word visual acceptance:

```bash
node src/cli/record-wps-visual.js --delivery-dir <delivery-dir> --status passed --note "checked in WPS" --json
```

## Template Packages

Templates are packages, not standalone DOCX files. A template package must keep
these files together:

- `manifest.json`
- `template.docx`
- `schema.json`
- `sample.json`
- `prompt.md`
- `data-adapter.js`
- `adapter-sample.render-plan.json`

Create a new package from an existing template:

```bash
node src/cli/scaffold-template.js --from general-proposal --template-id <new-template-id> --name "<template-name>" --out-dir <template-package-dir> --json
```

Validate a package before installing:

```bash
node src/cli/validate-template.js --package <template-package-dir> --json
```

Render its adapter sample before installing:

```bash
node src/cli/render-template-sample.js --package <template-package-dir> --out-dir <empty-smoke-dir> --json
```

Install only through the installer:

```bash
node src/cli/install-template.js --package <template-package-dir> --json
node src/cli/install-template.js --package <template-package-dir> --replace --json
```

Do not edit `template-registry.json` by hand. Installed templates must have
`template-install-report.json`, and runtime registry loading rejects installed
templates whose report is missing or disagrees with the registry entry.

## Delivery Package

A successful render writes a delivery directory containing:

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
- `replay-report.json`
- `README-图片调整说明.md`

CLI success and package validation are not final visual approval. The
`wps_visual` check stays `not_verified` until a human opens the DOCX in WPS or
Word and records the result.

## Copyable Skill Package

Build the copyable compatibility skill:

```bash
node scripts/build-copyable-skill.js --out-dir <docx-template-skill-dir>
```

Then run its self-test:

```bash
node <docx-template-skill-dir>/scripts/self-test.js --out-dir <writable-output-dir>
```
