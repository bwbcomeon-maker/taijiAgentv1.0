# Expert Team Workbench Outside Click Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow the expanded expert-team workbench to collapse when the user clicks a non-interactive blank chat background outside it, while preserving all existing inside interactions, drafts, restore behavior, overlays, and responsive layout.

**Architecture:** Reuse one `collapseWorkbench()` function for both the existing close button and the new outside-blank click path. Bind the listener only to the legacy chat surface and the current `.taiji-home-shell`, not `document` and not a backdrop; accept only known layout-background nodes so message content, controls, notifications, and dialogs do not trigger collapse.

**Tech Stack:** Vanilla JavaScript, existing Python/Node VM frontend contract tests, Playwright Electron smoke test.

---

## File map

- Modify `hermes-local-lab/sources/hermes-webui/static/expert-team-v3.js`: add the narrow blank-target predicate, shared collapse function, and scoped `#mainChat` click binding.
- Modify `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v3.py`: add deterministic unit coverage for accepted and rejected click targets and event binding cleanup.
- Modify `hermes-local-lab/sources/hermes-webui/tests/expert_team_v3_electron_smoke.js`: exercise the user-visible blank-area collapse, internal click protection, restore, and draft preservation in the existing Electron fixture.

### Task 1: Add failing interaction contract tests

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v3.py`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v3.py`

- [ ] **Step 1: Export the future helpers through the existing VM test hooks**

Add `isWorkbenchOutsideBlankTarget`, `handleWorkbenchOutsideClick`, and `collapseWorkbench` beside `handleWorkbenchClick` in `window.__expertTeamV3TestHooks`.

- [ ] **Step 2: Write a failing test for the exact click boundary**

Create a VM fixture that asserts:

```javascript
const blankTargets = ['mainChat', 'messages-shell', 'messages', 'msgInner'];
const protectedTargets = ['message-content', 'BUTTON', 'TEXTAREA', 'toast', 'dialog'];
```

For each blank target, `handleWorkbenchOutsideClick(event)` must return `true`, add `is-collapsed`, preserve the draft, and focus the restore button. For each protected target, it must return `false` and leave the workbench expanded.

- [ ] **Step 3: Run the focused test and confirm RED**

Run:

```bash
python3 -m pytest hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v3.py -k outside_blank -q
```

Expected: FAIL because the new helper functions do not exist yet.

### Task 2: Implement the minimal scoped interaction

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/static/expert-team-v3.js`
- Test: `hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v3.py`

- [ ] **Step 1: Add the narrow blank-background predicate**

Implement:

```javascript
function isWorkbenchOutsideBlankTarget(target) {
  if (!target?.matches) return false;
  return target.matches([
    '#mainChat', '#mainChat > .messages-shell', '#messages', '#msgInner',
    '.taiji-home-shell', '.taiji-main-workspace', '.taiji-hero', '.taiji-session-groups',
  ].join(', '));
}
```

This intentionally excludes message cards, text, links, controls, composer content, toast, dialogs, and other overlays.

- [ ] **Step 2: Extract the current close behavior**

Implement:

```javascript
function collapseWorkbench() {
  const root = workbenchRoot();
  if (!root || state.collapsed) return false;
  state.draft = captureWorkbenchDraft(root, state.card);
  state.collapsed = true;
  root.classList.add('is-collapsed');
  document.body.classList.add('expert-team-v3-collapsed');
  root.querySelector('[data-et3-action="restore-workbench"]')?.focus();
  return true;
}
```

Make the existing `close-workbench` action return `collapseWorkbench()`.

- [ ] **Step 3: Bind only the chat surface and share the AbortController lifecycle**

Implement:

```javascript
function handleWorkbenchOutsideClick(event) {
  if (state.collapsed || !isWorkbenchOutsideBlankTarget(event?.target)) return false;
  return collapseWorkbench();
}

[document.getElementById('mainChat'), document.querySelector?.('.taiji-home-shell')]
  .filter(Boolean)
  .forEach(surface => surface.addEventListener?.(
    'click',
    event => handleWorkbenchOutsideClick(event),
    { signal },
  ));
```

Place the scoped binding in `bindWorkbenchEvents(root)` so rerenders abort the old listener and do not leak handlers.

- [ ] **Step 4: Run the focused tests and confirm GREEN**

Run:

```bash
python3 -m pytest hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v3.py -k 'outside_blank or workbench_binds' -q
```

Expected: all selected tests PASS.

### Task 3: Add user-visible Electron regression coverage

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/tests/expert_team_v3_electron_smoke.js`
- Test: `hermes-local-lab/sources/hermes-webui/tests/expert_team_v3_electron_smoke.js`

- [ ] **Step 1: Extend the existing collapse/restore fixture**

Before the existing close-button assertion:

```javascript
await page.locator('#expertTeamV3Workbench [data-et3-revision]').fill('这是尚未提交的复核草稿');
await page.locator('#msgInner').click({ position: { x: 8, y: 8 } });
assert(await page.locator('#expertTeamV3Workbench').evaluate(root => root.classList.contains('is-collapsed')), 'Blank chat background did not collapse the workbench');
await page.getByRole('button', { name: '展开专家团工作台' }).click();
assert((await page.locator('#expertTeamV3Workbench [data-et3-revision]').inputValue()) === '这是尚未提交的复核草稿', 'Outside-click collapse lost the draft');
```

Then click a workbench heading or form field and assert the workbench remains expanded. Keep the existing close-button and restore assertions to prove both paths coexist.

- [ ] **Step 2: Run the existing expert-team Electron smoke**

Run the repository-provided command that invokes `tests/expert_team_v3_electron_smoke.js` as identified by the frontend UX QA skill and package scripts.

Expected: Electron smoke PASS and its screenshot/evidence output is generated without layout regression.

### Task 4: Verify and close out through the standard main workflow

**Files:**
- Verify all files listed above and the design/plan documents.

- [ ] **Step 1: Run focused and repository verification**

Run:

```bash
python3 -m pytest hermes-local-lab/sources/hermes-webui/tests/test_expert_team_frontend_v3.py -q
scripts/verify.sh
```

Expected: focused frontend tests PASS; unified verification PASS.

- [ ] **Step 2: Complete frontend UX QA**

Record the desktop blank-background close, internal-interaction protection, close button, restore, draft retention, and narrow/full-width behavior in the required Chinese frontend UX QA report. Mark any unexecuted visual or accessibility checks as unverified.

- [ ] **Step 3: Perform exact staging and final Sol audit**

Stage only the expert-team JavaScript, its two tests, and this task's design/plan documents. Require PASS on status, unstaged diff, cached name-status, cached diff check, and full cached diff before commit.

- [ ] **Step 4: Commit and push main**

After the final staged audit passes, commit with a conventional message such as:

```bash
git commit -m "feat: close expert workbench from blank area"
git fetch origin
git push origin main
```

Expected: local `main` and `origin/main` point to the new commit; unrelated untracked files remain untouched.
