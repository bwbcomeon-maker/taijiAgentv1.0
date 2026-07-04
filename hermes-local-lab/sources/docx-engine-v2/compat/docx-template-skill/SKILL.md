---
name: docx-template-skill
description: Use when the user invokes /docx-template-skill, /套用文档模板, or /套用模板, asks to apply a document template after content already exists, or needs a templated editable DOCX delivery package.
---

# Docx Template Skill

This compatibility package is a thin shell over DOCX Engine V2. The maintained business logic lives under `engine/`; scripts in this directory only adapt legacy command names and call the v2 CLIs.

## Workflow

1. List templates when the user has not selected one:
   ```bash
   node engine/src/cli/list-templates.js --json
   ```
2. Render a delivery package:
   ```bash
   node scripts/apply-template.js --template-id general-proposal --source <source.md|source.txt|source.docx> --asset-dir <asset-dir> --out-dir <delivery-dir> --json
   ```
3. Legacy single-file output is still accepted:
   ```bash
   node scripts/apply-template.js --template-id general-proposal --source <source.md> --asset-dir <asset-dir> --out <output.docx>
   ```
4. Package a rich draft before template rendering when the user needs editable picture assets:
   ```bash
   node scripts/package-rich-draft.js --source <source.md> --asset-dir <asset-dir> --out-dir <package-dir>
   ```
5. Rerender an editable Mermaid figure from either a rich draft package or a v2 delivery package:
   ```bash
   node scripts/render-figure-assets.js --manifest <package-dir>/draft.manifest.json --figure-id fig-001
   node scripts/render-figure-assets.js --manifest <delivery-dir>/render-plan.json --figure-id fig-001
   ```
6. Replace a DOCX image by stable figure id:
   ```bash
   node scripts/replace-docx-image.js --docx <input.docx> --figure-id fig-001 --image <replacement.png|svg> --out <updated.docx>
   ```
7. Re-run delivery package validation at any time:
   ```bash
   node scripts/validate-delivery.js --delivery-dir <delivery-dir> --json
   node scripts/validate-delivery.js --delivery-dir <delivery-dir> --write-report --json
   ```
8. Record WPS/Word visual acceptance after a human opens and checks `document.docx`:
   ```bash
   node scripts/record-wps-visual.js --delivery-dir <delivery-dir> --status passed --note "已检查目录、图表、图片和版式" --json
   ```
9. Install a validated template package into this skill's engine registry:
   ```bash
   node scripts/install-template.js --package <template-package-dir> --json
   ```
   To update a template that was previously installed, use an explicit replace flag:
   ```bash
   node scripts/install-template.js --package <template-package-dir> --replace --json
   ```

## Acceptance

A usable delivery package contains `document.docx`, `source.md`, `assets/`, `job.manifest.json`, `template.manifest.json`, `render-plan.json`, `quality-report.json`, and `README-图片调整说明.md`.

A usable rich draft package contains `draft.manifest.json`, `source.md`, `assets/`, `图片清单.md`, and editable Mermaid sources such as `source.mmd`. The package keeps `figureId` stable so a later DOCX replacement can target the exact picture.

`quality-report.json` keeps WPS/Word visual inspection as `not_verified` until a human opens and checks the document. Do not describe CLI success as final visual approval.

Run the installation self-test with:

```bash
node scripts/self-test.js --out-dir <writable-output-dir>
```

A passing self-test prints `self-test-ok`.
