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

Validate a final delivery package:

```bash
node src/cli/validate-delivery.js --delivery-dir <delivery-dir> --json
node src/cli/validate-delivery.js --delivery-dir <delivery-dir> --write-report --json
```

The CLI validates final delivery packages and requires the package to include a
hash-bound `replay-report.json` plus recorded WPS/Word visual acceptance with
evidence. Internal pre-replay staging validation is kept inside the workflow API.

Replay a delivery package from its packaged original source:

```bash
node src/cli/replay-delivery.js --delivery-dir <delivery-dir> --json
node src/cli/replay-delivery.js --delivery-dir <delivery-dir> --write-report --json
```

Use `--write-report` after package-level figure rerendering or other accepted
asset updates. It writes `replay-report.json` back into the delivery package,
updates the delivery manifest hashes, and refreshes `quality-report.json`.
Run final `validate-delivery` after replay evidence and WPS/Word visual
acceptance have both been recorded.

Record human WPS/Word visual acceptance:

```bash
node src/cli/record-wps-visual.js --delivery-dir <delivery-dir> --status passed \
  --visual-check document_opened \
  --visual-check layout_reviewed \
  --visual-check content_order_reviewed \
  --visual-check figures_reviewed \
  --visual-check tables_reviewed \
  --evidence-file <wps-screenshot-or-export> \
  --note "checked in WPS" --json
```

For `passed` or `passed_with_warnings`, the CLI requires structured visual
checks. `document_opened`, `layout_reviewed`, and `content_order_reviewed` are
always required. `figures_reviewed` is required when the render plan contains
images, and `tables_reviewed` is required when it contains tables. At least one
`--evidence-file` is also required; evidence files are copied into the delivery
package and rebound by sha256. Evidence must be a PNG/JPEG screenshot or a PDF
export from the WPS/Word review.

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

### Packaged runtime layout

Source checkouts keep the existing single-root layout when no runtime variables
are set. A packaged installation must point the engine at its immutable builtin
seed and a writable runtime home:

```bash
export TAIJI_DOCX_BUILTIN_ROOT=/opt/taiji-agent/runtime/docx-engine-v2
export TAIJI_DOCX_RUNTIME_HOME="$HOME/.local/share/taiji-agent/docx-engine-v2"
```

`TAIJI_DOCX_RUNTIME_HOME` is the explicit writable location. If it is omitted,
the engine uses `$TAIJI_RUNTIME_HOME/docx-engine-v2`; when only the builtin root
is configured, it falls back to
`$XDG_DATA_HOME/taiji-agent/docx-engine-v2` (or the equivalent directory under
`~/.local/share`). The builtin registry and `templates/` tree are read-only.
The writable home contains `template-registry.json` and `installed/`.

On first use, the writable registry is initialized atomically from the builtin
registry. Later releases append newly seeded builtin ids without replacing
existing entries or installed template files. If an installed template id
collides with a builtin id added by an upgrade, the installed template wins.
Registry files and template packages containing path traversal, symbolic links,
or non-regular files are rejected. `list-templates`, `install-template`,
`run-job`, scaffolding, and replay all use the same runtime routing, so none of
those paths needs to write under `/opt`.

Installation validates and smoke-renders a managed snapshot rather than the
mutable incoming directory, then rescans both source and snapshot before the
atomic commit. Installed registry entries persist `contentDigest` and
`revisionDigest`; replacement uses the revision as a compare-and-swap token so
concurrent stale replacements fail instead of silently overwriting each other.
Explicit runtime roots reject symbolic links in any existing path ancestor;
the automatic XDG/home fallback remains supported.

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
