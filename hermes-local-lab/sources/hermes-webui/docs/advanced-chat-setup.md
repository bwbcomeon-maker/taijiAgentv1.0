# Advanced chat setup

Two optional features for self-hosted Hermes WebUI deployments. **Most users need neither** — the defaults (in-process chat, no prefill) work out of the box.

## Session recall prefill

WebUI can attach ephemeral prefill messages to new browser-originated
agent turns. This is useful when a deployment already has a local recall or
router script for Joplin, Obsidian, Notion, llm-wiki, or another third-party
notes source and wants browser chat to know where durable context lives.

Prefer a compact router-style prefill (for example, "Joplin has the durable
project context; use the available notes/search tools before answering
detail-dependent questions") instead of dumping the full note corpus into every
new browser session. The prefill should point the agent toward retrieval; the
notes/search tools should provide the specific facts on demand.

Static JSON remains supported through `prefill_messages_file` or
`HERMES_PREFILL_MESSAGES_FILE`. For dynamic recall, opt in explicitly with a
WebUI-specific script hook:

```yaml
webui_prefill_messages_script:
  - python3
  - /path/to/notes_recall.py
webui_prefill_messages_script_timeout: 5
```

or:

```bash
HERMES_WEBUI_PREFILL_MESSAGES_SCRIPT="python3 /path/to/notes_recall.py" \
HERMES_WEBUI_PREFILL_MESSAGES_SCRIPT_TIMEOUT=5 \
./ctl.sh restart
```

The script may print either an OpenAI-style JSON message list, a JSON object with
a `messages` list, or plain text; plain text is wrapped as one `user` prefill
message so dynamic recall text becomes ordinary context instead of an extra
system instruction. If the hook must provide system-level guidance, emit JSON
messages with an explicit `role: "system"` entry instead. Script output is capped
at 256 KiB before parsing. Parsed prefill context is then bounded by
`webui_prefill_context_max_chars` or `HERMES_WEBUI_PREFILL_CONTEXT_MAX_CHARS`
(default: 12,000 characters; set to `0` to disable). When a dynamic script
exceeds the budget and a compact static prefill file is configured, WebUI falls
back to that file. If no compact fallback is available, WebUI injects a short
retrieval instruction instead of sending the oversized note/body payload with
every new browser turn. The browser only receives a compact status event
(`source`, `label`, message count, compaction metadata, and redacted errors),
never the prefill message bodies.

## Gateway-backed browser chat

By default, browser chat runs through WebUI's in-process legacy runtime. Advanced
self-hosted deployments can opt into routing new browser turns through a running
Hermes Gateway API server while preserving the existing WebUI `/api/chat/start`
and `/api/chat/stream` browser contract:

```bash
HERMES_WEBUI_CHAT_BACKEND=gateway \
HERMES_WEBUI_GATEWAY_BASE_URL=http://127.0.0.1:8642 \
HERMES_WEBUI_GATEWAY_API_KEY=... \
./ctl.sh restart
```

`HERMES_WEBUI_CHAT_BACKEND` is intentionally strict: only `gateway`,
`api_server`, or `api-server` enable the bridge. Generic truthy values such as
`1` or `true` are ignored so existing deployments do not change execution
ownership accidentally. If `HERMES_WEBUI_GATEWAY_API_KEY` is omitted, WebUI falls
back to `API_SERVER_KEY` when present. When Gateway returns HTTP 401, WebUI
reports a `gateway_auth_error` that points at this WebUI↔Gateway key mismatch
rather than showing the Gateway's generic provider-style "Invalid API key" body.
`/api/health/agent` also includes a redacted `gateway_chat` block so operators can
see whether gateway mode, base URL, and API-key presence are configured without
exposing the key value. That `gateway_chat` field is an operator diagnostic
payload only; it is not currently rendered as a user-facing health banner in the
browser UI.

The bridge is best used by operators who already run Hermes Gateway/API Server
locally and want browser-originated chat to use the same runtime/tool path as
messaging surfaces. Browser image turns use the same fail-closed preparation
boundary in Legacy and Gateway modes: a native vision main model receives image
content parts, while a text-only main model receives a successful description
from the configured auxiliary vision model. If any image cannot be analyzed,
WebUI emits a typed image error and does not call the main model.

Gateway `/v1/runs` accepts a plain string, an OpenAI-style `role`/`content`
message array, or a bare array of multimodal content parts. WebUI sends the
canonical message-array form. Image-only turns are valid; `file`, `input_file`,
non-image data URLs, and unsupported URL schemes are rejected before a run is
created.

## Image understanding verification and privacy

Saving an image-understanding Provider, model, and key means **configured**, not
verified. Use Settings > Model Configuration > Test image understanding to send
a fixed, non-sensitive probe image to the exact selected Provider and model.
The probe disables fallback routing, so a different visual backend cannot make
the selected configuration appear healthy. Evidence is scoped per profile and
becomes unverified when the profile, Provider, model, base URL, API mode, or key
changes.

The verification API returns only status, time, Provider/model identifiers, a
safe error code/message, and a diagnostic ID. It does not return or persist the
model's answer, raw Provider exception, local probe path, or credential. The
probe can take up to two minutes; the browser allows 150 seconds and discards
stale responses if configuration changes while the request is running.

Uploaded images are sent to the configured external visual Provider. Do not
upload screenshots containing credentials, private keys, personal records, or
other data that the Provider is not authorized to process. Visual descriptions
are force-redacted locally before entering the main model, session, log, or
public error path, but this text redaction happens after the external visual
Provider has received the image and is not a substitute for image-level masking.

If analysis fails, the chat keeps the user turn and attachment and shows visible
Retry image analysis and Open image configuration actions. Retry reuses the
already uploaded descriptor in page memory; persisted browser in-flight state
continues to store only safe filenames, not local attachment paths.
