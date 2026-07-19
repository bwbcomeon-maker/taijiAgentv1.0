# Config snapshot browser QA

Date: 2026-07-19

Runtime:

- Vite development server: `127.0.0.1:4177`
- Browser: Playwright Chromium, headless
- Protected Taiji runtime `127.0.0.1:18643` was not requested, restarted, or modified.

Verified scenarios:

1. Initial draft load returns HTTP 500.
   - An alert and `Retry loading configuration` are visible.
   - Retrying performs a second draft request and renders the form.
2. Form save returns HTTP 409.
   - Local value `42` remains visible.
   - Reload requires an explicit confirmation.
   - Cancelling does not perform another draft request and preserves `42`.
   - Confirming performs the request and replaces the value with server value `99`.
3. Raw YAML load returns HTTP 500.
   - `Retry loading raw YAML` is visible.
   - Retrying after recovery renders the YAML textarea.
4. Raw YAML is blocked by policy.
   - The server-provided reason is visible.
   - Save, retry, and textarea controls are absent.
5. Form save succeeds, but the post-save refresh returns HTTP 500.
   - The message reports that configuration was saved and the refresh failed.
   - It does not report a save failure.
   - Local value `55` remains visible.
6. At 1024 x 720, document scroll width equals client width (`1024`).
   - No horizontal viewport overflow was detected.

Automated regression run:

- `tests/hermes_cli/test_config.py tests/hermes_cli/test_web_server.py`: 308 passed, 1 dependency deprecation warning.
- `npm run build`: passed; Vite retained its existing large-chunk warning.

Evidence:

- `01-initial-load-error.png`
- `02-conflict-confirmation.png`
- `03-raw-policy-blocked.png`
- `04-save-success-refresh-failure.png`
- `05-responsive-1024x720.png`
