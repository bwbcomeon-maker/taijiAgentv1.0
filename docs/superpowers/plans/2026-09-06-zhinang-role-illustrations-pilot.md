# Zhinang Role Illustrations Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate, validate, and preview twelve approved-style Image 2.0 role illustrations in real Zhinang cards and details without exposing them to normal runtime before user acceptance.

**Architecture:** A dedicated Python module loads a version-bound local manifest, validates every path and WebP file, and returns only trusted image paths. The existing catalog projects active images in normal runtime and review images only behind a loopback, isolated-state, server-side preview gate. Vanilla JavaScript progressively replaces the current single-character fallback after image decode, so image faults never block role actions.

**Tech Stack:** Python 3.11 standard library for runtime validation, system Python 3.13.6 with Pillow 12.3.0 as a verified build-time converter, vanilla JavaScript/CSS, pytest, Image 2.0 built-in generation, WebP.

---

## File map

- Create `hermes-local-lab/sources/hermes-webui/api/zhinang_images.py`: manifest and file validation with fail-soft lookup.
- Create `hermes-local-lab/sources/hermes-webui/tests/test_zhinang_images.py`: security, corruption, preview-gate, and catalog projection contracts.
- Create `hermes-local-lab/sources/hermes-webui/scripts/prepare_zhinang_role_images.py`: deterministic PNG-to-WebP conversion and manifest update.
- Create `hermes-local-lab/sources/hermes-webui/tests/test_prepare_zhinang_role_images.py`: build-time Pillow decode, conversion, exact-role and audit-record contracts.
- Create `hermes-local-lab/sources/hermes-webui/static/assets/zhinang/role-images.json`: pilot manifest.
- Create `hermes-local-lab/sources/hermes-webui/static/assets/zhinang/pilot-generation-record.json`: per-role input snapshot, final prompt, source digest, conversion and review evidence.
- Create twelve files under `hermes-local-lab/sources/hermes-webui/static/assets/zhinang/roles/`: product WebP assets.
- Modify `hermes-local-lab/sources/hermes-webui/api/zhinang.py`: attach trusted image paths to current, removed, and historical role projections.
- Modify `hermes-local-lab/sources/hermes-webui/api/routes.py`: derive request-scoped preview permission from `handler.client_address` and pass it to all four Zhinang projection paths.
- Modify `hermes-local-lab/sources/hermes-webui/static/zhinang.js`: progressive image rendering and fallback.
- Modify `hermes-local-lab/sources/hermes-webui/static/zhinang.css`: stable card and detail image containers.
- Modify `hermes-local-lab/sources/hermes-webui/tests/test_zhinang_ui.py`: rendering, fallback, and accessibility contracts.
- Modify `hermes-local-lab/sources/hermes-webui/tests/zhinang_browser_e2e.cjs`: add a reproducible `images` scope and enable the review environment only for its isolated server.
- Modify `docs/verification/2026-09-05-taiji-zhinang-implementation.md`: pilot evidence and acceptance boundary.

### Task 1: Trusted image manifest loader

**Files:**
- Create: `hermes-local-lab/sources/hermes-webui/api/zhinang_images.py`
- Create: `hermes-local-lab/sources/hermes-webui/tests/test_zhinang_images.py`

- [ ] **Step 1: Write failing loader tests**

Create fixtures for one valid 512×512 WebP and manifests covering malformed JSON, schema/catalog mismatch, duplicate role/path, protocol, absolute path, backslash, traversal, query/fragment, symlink, wrong format, dimensions, bytes, and SHA-256. Assert whole-manifest faults return `{}`, while individual file faults remove only that role.

```python
def test_manifest_faults_fail_soft_without_affecting_catalog(tmp_path):
    images = load_role_images(tmp_path / "role-images.json", "catalog-v1")
    assert images == {}

def test_bad_file_disables_only_its_role(tmp_path):
    images = load_role_images(write_two_item_manifest(tmp_path, corrupt_second=True), "catalog-v1")
    assert set(images) == {"agency:sales/sales-engineer"}
```

- [ ] **Step 2: Run RED**

Run: `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-agent/venv/bin/python -m pytest /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/tests/test_zhinang_images.py -q`

Expected: collection fails because `api.zhinang_images` does not exist.

- [ ] **Step 3: Implement the loader**

Define:

```python
IMAGE_SCHEMA = "taiji-zhinang-role-images/v1"
ALLOWED_STATES = {"draft", "review", "approved", "active"}
ASSET_PREFIX = "static/assets/zhinang/roles/"

def load_role_images(manifest_path: Path, catalog_version: str, *, include_review=False) -> dict[str, str]:
    """Return trusted site-relative paths; return an empty/partial map on image-only faults."""
```

Validate the runtime half of the two-layer contract: top-level structure, catalog binding, uniqueness, 64-character lowercase SHA-256, size limit, fixed path prefix and suffix, no URL/absolute/backslash/traversal/query/fragment, regular non-symlink file, WebP signature and dimensions parsed directly from RIFF `VP8X`, `VP8 `, or `VP8L` headers, 512×512 dimensions, byte count, and digest. Runtime code must not import Pillow. The conversion/build gate in Task 3 performs full Pillow decode before the digest is admitted to the manifest. Normal mode admits only `active`; review mode admits `review`, `approved`, and `active`.

- [ ] **Step 4: Run GREEN**

Run the test command from Step 2.

Expected: all tests pass.

### Task 2: Server-side review gate and catalog projection

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/api/zhinang.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/routes.py`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_zhinang_images.py`

- [ ] **Step 1: Write failing projection tests**

```python
def test_review_images_require_all_three_server_conditions(monkeypatch, tmp_path):
    assert review_images_enabled("127.0.0.1", tmp_path) is False
    monkeypatch.setenv("TAIJI_ZHINANG_IMAGE_REVIEW", "1")
    assert review_images_enabled("10.0.0.8", tmp_path) is False
    assert review_images_enabled("127.0.0.1", PRODUCTION_STATE_DIR) is False
    assert review_images_enabled("127.0.0.1", tmp_path) is True

def test_catalog_survives_invalid_image_manifest(monkeypatch):
    rows = load_current_catalog_rows()
    assert len(rows) == 274
    assert all("image_path" not in row for row in rows)
```

Also assert current, removed-favorite, and historical detail projections query images by `role_id`; no image field is persisted into favorites or v2 session snapshots. Route tests must cover `/api/zhinang/catalog`, `/api/zhinang/roles/{role_id}` for current and removed roles, and `/api/zhinang/session-role`, proving each passes a request-scoped `include_review` value derived from `handler.client_address[0]`.

- [ ] **Step 2: Run RED, implement, then run GREEN**

Run the Task 1 pytest command. Implement `review_images_enabled(remote_host, state_dir)` with exact environment value `1`, `ipaddress.ip_address(remote_host).is_loopback`, and a state directory proven different from the resolved production state root. `routes.py` computes this for each request and passes `include_review` explicitly through `query_catalog_roles()`, `current_role_detail()`, `removed_role_detail()`, and `public_session_role_detail_projection()`. Cache immutable validated maps under separate `(catalog_version, include_review)` keys; never cache or reuse a request decision itself. Add `image_path` only when a trusted mapping exists.

Expected: all image tests pass and existing `test_zhinang_library.py` remains green.

### Task 3: Generate and prepare the twelve pilot assets

**Files:**
- Create: `hermes-local-lab/sources/hermes-webui/scripts/prepare_zhinang_role_images.py`
- Create: `hermes-local-lab/sources/hermes-webui/tests/test_prepare_zhinang_role_images.py`
- Create: `hermes-local-lab/sources/hermes-webui/static/assets/zhinang/role-images.json`
- Create: `hermes-local-lab/sources/hermes-webui/static/assets/zhinang/pilot-generation-record.json`
- Create: twelve `.webp` files in `hermes-local-lab/sources/hermes-webui/static/assets/zhinang/roles/`

- [ ] **Step 1: Add conversion tests**

Using `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`, test that the script accepts only a decoded 1024×1024 PNG, converts it to 512×512 WebP, refuses non-image/symlink/wrong-size input, enforces 160 KiB by reducing WebP quality through the fixed sequence `82, 78, 74, 70, 66`, and writes canonical UTF-8 JSON with source/final SHA-256 and conversion metadata. Agent venv Python 3.11 runs runtime tests and must not import PIL; Pillow remains a build-time tool and is not added to product requirements or packaging.

Run from the repository root:

```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest \
/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/tests/test_prepare_zhinang_role_images.py -q
```

Expected: RED before the script exists, then PASS after implementation.

- [ ] **Step 2: Implement and verify the conversion script**

Expose:

```bash
(cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui && \
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 scripts/prepare_zhinang_role_images.py \
  --catalog data/zhinang/chinese-content-v1.json \
  --source-dir /tmp/zhinang-image2-pilot \
  --output-dir static/assets/zhinang/roles \
  --manifest static/assets/zhinang/role-images.json \
  --state review --model image-2.0)
```

The script derives the filename slug and role-ID hash, never overwrites a different digest, and emits reviewer fields as `null` until approval. It also writes `pilot-generation-record.json` containing the exact 12-role set, each role's name/category/summary/capabilities input snapshot, final prompt, model identifier, source PNG SHA-256 and dimensions, WebP conversion parameters, output digest, and review state.

- [ ] **Step 3: Generate one Image 2.0 image for each fixed pilot role**

Use one built-in Image 2.0 call per role. Every prompt uses this fixed style suffix:

```text
Premium lightweight 3D icon scene; friendly professional character or occupational subject; frosted-glass information board; soft matte polymer; Taiji blue, cyan, deep navy, white and cool gray; pale ice-blue background; centered square composition; large simple silhouette readable at 96px; no text, letters, numbers, logo, watermark or frame; avoid generic robots, childish toys, clutter and tiny details.
```

Use these distinguishing subjects:

- 售前方案顾问：consultant connecting customer dialogue, architecture nodes, and PoC checklist.
- 投标策略顾问：consultant arranging requirement document, evidence blocks, and evaluation target.
- 产品经理：product lead balancing user insight, prioritized roadmap cards, and acceptance check.
- 技术架构顾问：architect connecting modular system blocks with dependency and resilience symbols.
- 内容策划顾问：creator arranging audience, story structure, and multichannel content objects.
- AI 搜索基础设施顾问：specialist inspecting crawler path, structured content nodes, and citation beacon.
- 文档审阅助手：reviewer comparing two documents with consistency markers and issue lens.
- 资助申请写作顾问：writer connecting project goal, budget blocks, evidence, and evaluation plan.
- 应付账款运营顾问：operator matching invoice, approval route, duplicate check, and audit record.
- 自动化治理架构顾问：governance lead supervising workflow nodes, human checkpoint, shield, and audit trail.
- 跨文化包容性体验顾问：facilitator connecting diverse user silhouettes, dialogue, and inclusive interface panel.
- 品牌战略与一致性顾问：brand guardian aligning color/material samples, message system, and consistency shield.

Save generated PNGs outside the repository under `/tmp/zhinang-image2-pilot/<role-slug>.png`, verify every decoded source is exactly 1024×1024, visually reject any text artifact or role mismatch, then run the conversion command.

- [ ] **Step 4: Validate all twelve files**

Before generation and again after conversion, run `/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-agent/venv/bin/python /Users/bwb/Documents/工作/taiji-agentv1.0/scripts/check-local-change-safety.py` from the repository root. Run the Task 1 loader command and the exact `test_prepare_zhinang_role_images.py` command above. The latter fully decodes every output, calls `image.verify()`, reloads pixels, and asserts WebP format and 512×512 dimensions. It also asserts the manifest and generation record contain exactly the fixed 12 role IDs from the design, twelve distinct paths, every source PNG record is 1024×1024, every WebP is at most 160 KiB, and every prompt/input/output digest is present.

Expected: all checks pass. If the safety script fails, keep all fixed 12 roles and reduce WebP bytes or code/record overhead; if the full set still cannot pass, stop with evidence instead of reducing the role set.

### Task 4: Progressive card and detail rendering

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/static/zhinang.js`
- Modify: `hermes-local-lab/sources/hermes-webui/static/zhinang.css`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_zhinang_ui.py`

- [ ] **Step 1: Write failing static contracts**

Assert `cardHtml()` and `detailHtml()` render a fixed-size wrapper containing the existing character fallback plus an `<img alt="" loading="lazy" decoding="async">` only for trusted `image_path`. Assert `load` adds `is-loaded`, `error` removes the image without retry, and no image changes click/favorite/detail handlers.

- [ ] **Step 2: Run RED, implement minimal rendering, run GREEN**

Add one delegated `load/error` binding for `.zhinang-role-image`. Keep the fallback in the DOM. Add stable aspect ratios, `object-fit:cover`, and reduced-motion-safe opacity only. Do not add download, zoom, carousel, or image controls.

Run:

```bash
/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-agent/venv/bin/python -m pytest /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/tests/test_zhinang_ui.py -q
/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/hermes-home/node/bin/node --check /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui/static/zhinang.js
(cd /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-webui && PATH=/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/hermes-home/node/bin:/usr/bin:/bin /Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/hermes-home/node/bin/npm run lint:runtime)
```

Expected: all pass.

### Task 5: Isolated real-browser pilot acceptance

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/tests/zhinang_browser_e2e.cjs`
- Modify: `docs/verification/2026-09-05-taiji-zhinang-implementation.md`

- [ ] **Step 1: Start the isolated review server**

Add `images` to `allowedScopes`. In that scope, make the existing harness add `TAIJI_ZHINANG_IMAGE_REVIEW=1` to the spawned WebUI only; it already creates a fresh isolated state directory, binds loopback, strips provider credentials, blocks outbound network, and uses the repository Agent venv. Add negative HTTP probes that omit the environment gate, use a non-loopback request decision fixture, and point at the production state root; each must omit review image paths.

- [ ] **Step 2: Test the twelve-role path in Chromium**

For all twelve roles, verify card image decode, role-image match, detail image, empty alt semantics, fallback after a deliberately blocked image, no layout shift, card selection, favorite focus, “查看详情”, “使用此示例”, “使用此智囊”, Escape close, and focus return. Repeat representative checks at 1440×900, 1024×768, 390×844, and 200% zoom. Record image requests, decoded natural dimensions, container rectangles, layout-shift measurements and blocked-image fallback.

Run exactly:

```bash
PLAYWRIGHT_NODE_PATH=/Users/bwb/.codex/skills/huashu-design/node_modules/playwright-core \
ZHINANG_E2E_CHROMIUM='/Users/bwb/Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing' \
ZHINANG_E2E_PYTHON=/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/sources/hermes-agent/venv/bin/python \
ZHINANG_E2E_OUT=/private/tmp/taiji-zhinang-images-pilot \
ZHINANG_E2E_SCOPE=images \
/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/hermes-home/node/bin/node \
hermes-local-lab/sources/hermes-webui/tests/zhinang_browser_e2e.cjs
```

Expected: JSON status `PASS`, scope `images`, exactly twelve mapped roles, zero console/page/external errors, and evidence at `/private/tmp/taiji-zhinang-images-pilot/e2e-evidence-images.json`.

- [ ] **Step 3: Capture evidence and write the UX report**

Capture overview and detail screenshots for each category, record console/page/external-request errors, image request count, dimensions, and asset bytes. Update the verification ledger with exact commit/worktree, commands, results, evidence paths, and the explicit status: `代表集待用户验收，未授权生成其余 262 张`.

### Task 6: Verification, review, and user gate

**Files:** all files listed above.

- [ ] **Step 1: Run focused and default verification**

Run image loader/conversion tests, Zhinang library/UI tests, JS syntax, runtime lint, `git diff --check`, and local change safety with the exact interpreters already specified. Then run from the repository root:

```bash
PATH=/Users/bwb/Documents/工作/taiji-agentv1.0/hermes-local-lab/hermes-home/node/bin:/usr/bin:/bin:/usr/sbin:/sbin \
/Users/bwb/Documents/工作/taiji-agentv1.0/scripts/verify.sh
```

Expected: all applicable checks pass. Browser smoke missing Python Playwright is recorded as an environment limitation only if the separate Chromium acceptance in Task 5 passes.

- [ ] **Step 2: Stage exact pilot files and obtain Sol final review**

Review the five required Git views. Sol must inspect manifest safety, all twelve images, code, tests, browser evidence, package-size impact, and the user gate. Any staged byte change invalidates the review.

- [ ] **Step 3: Commit and push the pilot**

Use a Conventional Commit such as `feat: preview zhinang role illustrations`, refresh `origin/main`, prove remote is not ahead, push normally, and verify local/remote SHA equality and clean status.

- [ ] **Step 4: Stop for user acceptance**

Present the real card and detail screenshots plus the twelve-role review table. Do not change manifest entries to `active` and do not generate the remaining 262 images until the user explicitly approves the representative set.
