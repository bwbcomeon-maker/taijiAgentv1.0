# Provider Failure and Main-Model Verification Contract

- **Status:** Implemented
- **Author:** taiji Agent maintainers
- **Created:** 2026-08-26

## Problem

A complete local model configuration is not evidence that the remote Provider,
account, credential, or selected model is usable. Provider failures also cross
Agent, Gateway, SSE, session, and browser boundaries; reducing them to a string
makes the UI ambiguous and makes a refresh capable of erasing the only visible
failure state.

## Error boundary

### Model settings visual semantics

`connection_verified` is a successful connection check: the main summary uses a
small green check and explicitly says actual conversation results remain to be
observed. Only `chat_verified` is described as a verified conversation.
Configured-but-unchecked and unsupported checks use neutral information styling;
checking, initial loading, and runtime refresh use blue progress styling. Missing
required main configuration remains a warning; verification failure remains an
error with recovery guidance. A check request failure is presented as an
incomplete check, without mutating the previously saved verification fact.

Optional vision/image configuration and verification badges live in their own
capability cards, not the main-model hero. Unconfigured or unchecked optional
capabilities are neutral; progress is informational and actual failures stay
errors. This changes presentation only, not verification state persistence,
Provider selection, credentials, or request semantics.

Terminal Provider failures use `taiji.gateway.run-error.v1`. The boundary keeps
only the fixed source/code/status/transport/retry/incident fields and a constant
message. Raw Provider bodies, authorization headers, credentials, URLs containing
tokens, local paths, environment values, and stack traces must not cross it.

`incident_id` is created once at terminal classification and is reused in Agent
logs, Gateway run status/events, SSE, the session message, and diagnostics.
Provider and local Gateway authentication are separate product errors.

The WebUI rebuilds `taiji.product.error.v1` from the server-owned catalog. Client
string matching is a compatibility path only; the browser must not maintain a
second product-copy catalog.

## Durable terminal message

Before emitting a terminal `apperror`, Gateway chat owns the session lock and
verifies cancellation plus `active_stream_id`. It materializes the pending user
turn, then atomically saves one assistant message identified by
`webui-error:<turn_id>`.

- With partial public assistant text, the message keeps that text and has
  `status=incomplete`.
- Without partial text, it has `status=failed` and renders only the error card.
- `_error=true` messages are visible sidecar truth but are excluded from model
  context and `state.db` semantic checkpoints.
- SSE carries the same assistant object; the browser replaces/deduplicates by
  `message_id`.
- A stale or cancelled stream may neither save nor emit the error.
- If saving fails, in-memory pending state is restored and a non-persisted
  `session_persistence_failed` notice is emitted.

This makes refresh, reconnect, journal replay, session switching, and app restart
converge on the saved transcript instead of browser-only memory.

## Main-model verification levels

`taiji.model.verification.v1` has three positive levels:

1. `configured`: required local fields exist; remote availability is unknown.
2. `connection`: a strict non-generating Provider directory request succeeded.
3. `chat`: a real conversation succeeded on the exact current Provider, model,
   Base URL, profile, and credential fingerprint.

The strict connection check is user-triggered, GET-only, limited to five seconds
and 256 KiB, performs no retry or redirect, and has no static catalog fallback.
It sends no conversation, attachment, tool, memory, or completion request.
Providers without a safe directory probe return `unsupported`, not `failed`.

Connection and chat success expire after five minutes. Transient network,
timeout, rate-limit, and service failures expire after one minute. Credential,
account, and model failures persist until configuration changes or a later
verification succeeds. Any profile/provider/model/Base URL/credential change
invalidates old evidence immediately.

`Refresh local status` only rereads local state. It must never imply a Provider
request or remote validation.

The Settings refresh button owns its loading state independently of image
verification/loading. It reads main-model state first and reports success,
partial image results, cancellation, or failure beside the button. Concurrent
clicks do not start duplicate refreshes. A provider summary read may initialize
the shared configuration only together with its pristine form, and only if no
newer model read, save reconciliation, or baseline update has intervened; it
must not replace a form's baseline without rendering it. An untouched form rereads current server state when
reopened. Rendered default choices are not user edits. Actual unsaved edits and
secrets require explicit discard confirmation, and edits made after a pending
save remain visible when its receipt is reconciled.

The main-check POST route consumes its bounded request body before probing.
CSRF rejection, invalid/oversized Content-Length, and body-read errors close the
connection rather than leaving unread bytes for the next HTTP/1.1 request.

## Review invariants

- A configured model is never rendered as green availability evidence.
- Provider and Gateway 401 failures never share a product code or recovery path.
- Full secrets and raw Provider responses are absent from logs, run state,
  journals, sidecars, SSE, and diagnostics.
- A terminal assistant error survives two refresh cycles, session switching,
  reconnect, and app restart without duplication.
- Partial failure text remains visible but never enters the next model request.
- Actual fallback Provider/model identity, not the initially requested route,
  controls whether main-model verification may change.
