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
4. Rerender an editable Mermaid figure from a v2 delivery package:
   ```bash
   node scripts/render-figure-assets.js --manifest <delivery-dir>/render-plan.json --figure-id fig-001
   ```
5. Replace a DOCX image by stable figure id:
   ```bash
   node scripts/replace-docx-image.js --docx <input.docx> --figure-id fig-001 --image <replacement.png|svg> --out <updated.docx>
   ```

## Acceptance

A usable delivery package contains `document.docx`, `source.md`, `assets/`, `job.manifest.json`, `template.manifest.json`, `render-plan.json`, `quality-report.json`, and `README-图片调整说明.md`.

`quality-report.json` keeps WPS/Word visual inspection as `not_verified` until a human opens and checks the document. Do not describe CLI success as final visual approval.

Run the installation self-test with:

```bash
node scripts/self-test.js --out-dir <writable-output-dir>
```

A passing self-test prints `self-test-ok`.
