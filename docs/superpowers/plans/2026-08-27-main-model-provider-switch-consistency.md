# Main Model Provider Switch Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make main-model Provider changes atomically replace provider-owned model, endpoint, credential-draft, and transport state without blocking genuinely new model IDs.

**Architecture:** The browser owns immediate draft reset and default-model selection. The backend independently enforces persisted route coherence using the previous authoritative config and a static, confidence-only model ownership check; it preserves free-form unknown models and the existing verification layers.

**Tech Stack:** Vanilla JavaScript, Python, Flask API helpers, Pytest, Node-based frontend behavior tests.

---

### Task 1: Lock the browser switch contract

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_model_config_frontend.py`
- Modify: `hermes-local-lab/sources/hermes-webui/static/panels.js`

- [x] **Step 1: Extend the failing Node behavior test**

Create DOM fixtures for `modelConfigModel`, `modelConfigApiKey`, and `modelConfigProvider.dataset.lastProvider`. Assert that switching from `deepseek` to `zai` selects `glm-5` and clears the DeepSeek URL and unsaved key, while a second sync on the same Provider preserves explicit user input.

```python
assert output["switched"] == {
    "model": "glm-5",
    "baseUrl": "",
    "apiKey": "",
}
assert output["sameProvider"] == {
    "model": "glm-5-air",
    "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
    "apiKey": "new-zai-key",
}
```

- [x] **Step 2: Run the test and prove RED**

Run from `hermes-local-lab/sources/hermes-webui`: `../hermes-agent/venv/bin/python -m pytest tests/test_model_config_frontend.py::test_builtin_main_provider_switch_resets_provider_owned_draft -q`

Expected: FAIL because the current handler clears only the Base URL and does not select the new model or clear the API Key draft.

- [x] **Step 3: Implement the minimal browser state transition**

In `_syncMainModelConfigControls()`, compare `providerSel.dataset.lastProvider` with the selected Provider. On a real change, set `modelConfigModel` to `models[0] || ''`, clear `modelConfigBaseUrl` and `modelConfigApiKey`, then store the new last Provider. Initialize `dataset.lastProvider` during `_renderModelConfigPanel()` before the first sync so rendering saved state does not look like a user switch.

```javascript
const modelInput=$('modelConfigModel');
const baseInput=$('modelConfigBaseUrl');
const keyInput=$('modelConfigApiKey');
const models=(provider&&Array.isArray(provider.models))?provider.models:[];
const previousProvider=String(providerSel.dataset.lastProvider||'');
if(previousProvider&&previousProvider!==providerId){
 modelInput.value=models[0]||'';
 baseInput.value='';
 keyInput.value='';
}
providerSel.dataset.lastProvider=providerId;
```

```javascript
providerSel.value=main.provider||'custom';
providerSel.dataset.lastProvider=providerSel.value;
providerSel.onchange=_syncMainModelConfigControls;
```

- [x] **Step 4: Run the focused test and prove GREEN**

Run from `hermes-local-lab/sources/hermes-webui`: `../hermes-agent/venv/bin/python -m pytest tests/test_model_config_frontend.py::test_builtin_main_provider_switch_resets_provider_owned_draft -q`

Expected: `1 passed`.

### Task 2: Enforce backend route coherence

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_main_model_provider_switch.py`
- Modify: `hermes-local-lab/sources/hermes-webui/api/model_config.py`

- [x] **Step 1: Add failing backend tests**

Add coverage proving that a DeepSeek-to-Z.AI request with the unchanged old model is rejected, a free-form unknown Z.AI model is accepted, and stale `api_mode` is removed after a valid switch.

```python
with pytest.raises(ValueError, match="仍属于 DeepSeek"):
    model_config.set_main_model_config({
        "provider": "zai",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
    })

result = model_config.set_main_model_config({
    "provider": "zai",
    "model": "glm-next-preview",
    "base_url": "",
})
assert result["ok"] is True
assert "api_mode" not in yaml.safe_load(config_path.read_text())["model"]
```

- [x] **Step 2: Run the backend tests and prove RED**

Run from `hermes-local-lab/sources/hermes-webui`: `../hermes-agent/venv/bin/python -m pytest tests/test_main_model_provider_switch.py -q`

Expected: FAIL because stale model ownership and `api_mode` are not currently enforced.

- [x] **Step 3: Implement confidence-only stale-model detection**

When the Provider changes and the submitted model exactly equals the previous saved model, call `hermes_cli.models.detect_static_provider_for_model(model_id, provider_id)`. Reject only when it confidently resolves to the previous Provider. On every Provider change, remove `model_cfg["api_mode"]`; keep the existing stale Base URL comparison and allow unknown model IDs.

```python
previous_model = str(model_cfg.get("default") or "").strip()
if provider_changed and model_id == previous_model:
    from hermes_cli.models import detect_static_provider_for_model

    detected = detect_static_provider_for_model(model_id, provider_id)
    if detected and detected[0] == previous_provider:
        label = _PROVIDER_DISPLAY.get(previous_provider, previous_provider)
        raise ValueError(
            f"模型 {model_id} 仍属于 {label}，请重新选择 {provider_id} 的模型。"
        )
if provider_changed:
    model_cfg.pop("api_mode", None)
```

- [x] **Step 4: Run the backend tests and prove GREEN**

Run from `hermes-local-lab/sources/hermes-webui`: `../hermes-agent/venv/bin/python -m pytest tests/test_main_model_provider_switch.py -q`

Expected: all tests pass.

### Task 3: Regression, UX QA, and repository gate

**Files:**
- Verify: `hermes-local-lab/sources/hermes-webui/api/model_config.py`
- Verify: `hermes-local-lab/sources/hermes-webui/static/panels.js`
- Verify: `hermes-local-lab/sources/hermes-webui/tests/test_model_config_frontend.py`
- Verify: `hermes-local-lab/sources/hermes-webui/tests/test_main_model_provider_switch.py`

- [x] **Step 1: Run the model-config regression set**

Run from `hermes-local-lab/sources/hermes-webui`: `../hermes-agent/venv/bin/python -m pytest tests/test_model_config_api.py tests/test_model_config_frontend.py tests/test_main_model_provider_switch.py -q`

Expected: all selected tests pass.

- [x] **Step 2: Run static safety checks**

Run from the repository root: `hermes-local-lab/sources/hermes-agent/venv/bin/python scripts/check-local-change-safety.py`

Expected: PASS.

Run: `git diff --check`

Expected: no output and exit code 0.

- [x] **Step 3: Execute the applicable full gate**

Run: `scripts/verify.sh --full`

Expected: all applicable suites pass. If an untouched subsystem fails, rerun its focused test to establish whether it is an existing independent blocker and report it without expanding this change.

- [x] **Step 4: Produce the Chinese frontend UX QA report**

Report discoverability, Provider-switch behavior, secret handling, keyboard/accessibility impact, automated browser-equivalent coverage, and clearly mark real Electron/browser visual verification as either executed or unverified.

- [ ] **Step 5: Complete the standard repository closeout**

Precisely stage only the four implementation/test files, the approved design/plan, and `docs/reviews/main-model-provider-switch-ux-qa-2026-08-27.md`; obtain the required final cached-diff Sol review, commit with `fix: keep main model provider switch coherent`, fetch `origin/main`, prove no remote lead/divergence, and normally push `main`. Do not include the unrelated 2026-08-26 image-capability draft files.

## Execution note

- Focused RED: 4 expected failures.
- Focused GREEN: 5 passed.
- Initial model configuration regression: 488 passed; after the Sol compatibility fixes, the current WebUI gate including the new backend test file passed 697 tests and runtime JS lint passed.
- Full repository gate after isolating the installed-production license fixture from the developer machine's active license: `verification: PASS`; root 1296 passed with 2 skipped, Desktop 68 passed, DOCX 276 passed, Agent 205 passed, and WebUI 692 passed.
- Cached diff review and safety checks passed; commit/push is the only remaining closeout action.
