# Model Status Visual Semantics Implementation Plan

> **For agentic workers:** Use executing-plans to implement task-by-task. The primary agent owns all writes; Sol performs read-only final review under project rules.

**Goal:** Implement the user-approved model-status visual semantics without changing verification facts or API behavior.

**Architecture:** Reuse existing summary renderers. Explicit `ok`, `neutral`, `info`, `warn`, and `danger` styles align text and icons; optional capability states leave the hero and use the current visible image capability center, not the hidden legacy controls.

**Execution record:** Sections 1–3 implementation, focused tests and three-viewport browser checks are complete. The unchecked items below preserve the original plan; exact outcomes and remaining evidence limits are recorded in `docs/reviews/model-status-visual-semantics-ux-qa-2026-09-04.md`. Commit and push require the final staged Sol review. No packaging or installation is included.

**Tech Stack:** Vanilla JS/CSS/HTML, Python pytest with Node drivers, existing isolated Python Playwright smoke.

## 1. State regression before implementation

- [ ] Extend `hermes-local-lab/sources/hermes-webui/tests/test_model_config_frontend.py` focus driver to return the hero icon and badge tone. Assert `connection.state == 'ok'`, `connection.icon == '✓'`, pristine configured state `neutral`, refresh state `info`, incomplete `warn`, failed `danger`; retain distinction from `chat_verified`.
- [ ] Run `hermes-local-lab/sources/hermes-agent/venv/bin/python -m pytest hermes-local-lab/sources/hermes-webui/tests/test_model_config_frontend.py -k focus -q`; record expected visual-state assertion failures.

## 2. Minimal presentation changes

- [ ] In `static/panels.js`, map connection success to `tone:'ok'`; configured/unsupported to `neutral`; refresh/checking to `info`. Map icon explicitly: `ok:'✓', neutral:'i', info:'↻', warn:'!', danger:'!'`. Keep `chat_verified` and validation API semantics unchanged.
- [ ] Set optional capability unconfigured/unverified to `neutral`, verifying to `info`, failed to `danger`; use `card.dataset.state=meta.tone` rather than collapsing all non-success to warning.
- [ ] In `static/index.html`, move vision/image status badge IDs from the hero into their corresponding capability cards. Initial loading icon is `↻`, never `✓`; add polite main-summary announcement without duplicate live regions.
- [ ] In `static/style.css`, define explicit neutral/info/loading styles for hero, pills and cards; scope small hero icon styling to avoid altering license UI. Preserve visible errors and responsive wrapping.
- [ ] Run the full model frontend and refresh regression files and fix only related failures.

## 3. Browser evidence and delivery

- [ ] Extend `tests/model_config_refresh_browser_smoke.py` to render each mock verification state and assert text, data-state, icon, computed colors and badge placement. Exercise real check/refresh controls with mocked APIs, including held check and failure recovery. Keep external requests blocked.
- [ ] Run `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 hermes-local-lab/sources/hermes-webui/tests/model_config_refresh_browser_smoke.py`; inspect 1280/768/390 screenshots and preserve existing draft/cancel/keyboard regression.
- [ ] Run `scripts/verify.sh` with prepared Node 24 on PATH; scope escalation follows the actual gate plan.
- [ ] Update behavior contract and Chinese UX QA report with exact evidence and unverified installed-state/a11y/pixel-regression limits. Record release-note changes there because the existing oversized WebUI CHANGELOG is rejected by the unchanged safety gate.
- [ ] Perform one self-review, stage explicit paths, obtain Sol review of all five views and exact staged bytes, commit and normal push main. No packaging, installation or release.

## Plan self-review

All approved states and badge relocation are covered above. Existing model verification enums, credentials, APIs and refresh draft protection remain unchanged. Tests must distinguish source/browser evidence from Kylin installed acceptance.
