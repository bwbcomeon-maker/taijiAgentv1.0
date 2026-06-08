---
version: alpha
name: taiji Agent Light Glass Console
description: "A light glass enterprise agent control surface: local assistant work first, tool traces as quiet metadata, restrained brand chrome."
colors:
  primary: "#15233A"
  secondary: "#4F6584"
  tertiary: "#12B2C6"
  neutral: "#F4F7FD"
  surface: "#FFFFFF"
  surfaceSubtle: "rgba(255,255,255,0.74)"
  borderSubtle: "rgba(33,66,118,0.14)"
  ink: "#15233A"
  success: "#16A46C"
  warning: "#D98A16"
  error: "#D93D4A"
typography:
  body-md:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Inter, system-ui, sans-serif"
    fontSize: 15px
    fontWeight: 400
    lineHeight: 1.68
  body-sm:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Inter, system-ui, sans-serif"
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.45
  user-message:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Inter, system-ui, sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.55
  mono-xs:
    fontFamily: "SF Mono, ui-monospace, monospace"
    fontSize: 11px
    fontWeight: 500
    lineHeight: 1.55
rounded:
  sm: 4px
  md: 8px
  lg: 12px
  pill: 999px
spacing:
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
components:
  app-shell:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.primary}"
    rounded: "{rounded.sm}"
    padding: 16px
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    rounded: "{rounded.lg}"
    padding: 16px
  border-line:
    backgroundColor: "{colors.borderSubtle}"
    textColor: "{colors.primary}"
    rounded: "{rounded.sm}"
    padding: 4px
  state-success:
    backgroundColor: "{colors.success}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: 4px
  state-warning:
    backgroundColor: "{colors.warning}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: 4px
  state-error:
    backgroundColor: "{colors.error}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: 4px
  tool-call-group:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.secondary}"
    rounded: "{rounded.md}"
    padding: 4px
  tool-card:
    backgroundColor: "{colors.surfaceSubtle}"
    textColor: "{colors.secondary}"
    rounded: "{rounded.md}"
    padding: 8px
  user-message:
    backgroundColor: "{colors.tertiary}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: 12px
---

## Overview

taiji Agent WebUI should feel like a light enterprise workbench, not a demo page assembled from colorful cards. The primary artifact is the conversation and the active local workspace. Tool calls, thinking traces, context compaction records, token usage, and runtime status are useful, but they are transcript metadata and should sit below the visual priority of user and assistant prose.

The current default direction is `light + taiji-light-glass`: pale blue page chrome, white/glass panels, cyan-blue accents, deep blue-gray text, and restrained shadows. The brand resource entrypoint is `static/brand.js`; repo-local image assets live under `static/assets/taiji/` and can be replaced there when final transparent PNG/SVG/WebP/ICO source files are available.

## Colors

- **Primary (#15233A):** main text on light surfaces. It should read as deep blue-gray, not pure black.
- **Secondary (#4F6584):** metadata and secondary labels.
- **Tertiary / Accent (#12B2C6):** active states, focus rings, primary actions, links, and user bubbles.
- **Neutral (#F4F7FD / #DBEBFC):** app background gradient and page field.
- **Surface (#FFFFFF / rgba(255,255,255,0.74)):** cards, sidebar, composer, popovers, and lifted panels.
- **Semantic colors:** success/warning/error/info are state colors only, not decorative palette choices.

## Typography

Use Claude-like split typography: assistant prose gets an editorial serif stack (Georgia as the available substitute for Anthropic Serif), while user bubbles and functional UI stay in a crisp sans stack. This keeps the bot voice calmer and more readable without making controls feel bookish. Use monospace only for code, file paths, commands, tool names, and compact metadata. Avoid making whole cards feel like terminal output unless they actually are logs.

Scale should stay tight: 11px metadata, 12px labels, 14px body, 16–18px headings. Do not proliferate 10px/10.5px/12.5px one-offs unless there is a real layout constraint.

## Layout

### Desktop Taiji App Shell

At `1024px` and wider, the default `taiji-light-glass` experience uses a three-column desktop shell: brand navigation, a context secondary panel, and the main workspace. This shell is not a static landing page. `static/taiji-home.js` mounts the existing `main.main`, `#mainChat`, `#composerWrap`, and workspace drawer into the redesigned frame, then keeps visual state synchronized with the legacy business state.

The secondary panel is state-driven. Chat renders recent conversations from `/api/sessions` and `/api/projects`; other navigation entries mount their real legacy side panels (`panelTasks`, `panelKanban`, `panelSkills`, and so on) into the Taiji glass column. Non-chat modules must not show the recent-conversation title, session search, or session grouping controls. New chat, session loading, search, quick prompts, send, model/workspace/reasoning/toolset menus, and all main navigation entries must continue to call the existing WebUI functions. If a module has no data, show a Taiji empty state inside the real panel rather than falling back to hardcoded sample cards.

Narrow screens below `1024px` intentionally keep the legacy responsive layout for now. Do not force the desktop shell into phone/tablet widths until a dedicated responsive redesign exists.

Conversation rhythm:

1. User message — right aligned, compact bubble.
2. Assistant content — left aligned, prose-first, no heavy bubble.
3. Tool/thinking/context traces — quiet disclosure rows inside the assistant turn.
4. Raw logs/details — hidden until explicitly expanded.

Metadata should not break the reading flow. A turn that used ten tools should read as one assistant turn with one compact `Used 10 tools` disclosure, not ten content cards.

## Elevation & Depth

Use almost no shadows in the transcript. Shadows are reserved for popovers, dropdowns, modal dialogs, and floating controls. Cards inside chat should use either a subtle border or a subtle tint, not both aggressively.

## Shapes

- Rows/list items: `4–8px` radius.
- Cards/panels: `8–12px` radius.
- Pills: only true chips/badges use `999px`.
- Avoid stacks of nested rounded rectangles. If a card contains another card, one of them is probably unnecessary.

## Components

### Tool/thinking activity group

Collapsed by default in settled history and during live runs unless the user has explicitly opened that Activity row before. Persist open/closed disclosure state per chat and per turn, so switching away from a chat and coming back preserves the mode the user left it in. Summary line uses one disclosure for internals and stays intentionally terse, e.g. `Activity: 4 tools`. It should not repeat the always-present thinking area, list individual tool names, or add a second trailing count badge. Expanding reveals thinking and individual tool cards together. Thinking and tools should not create separate transcript rows unless there is an error or approval state that needs attention.

### Tool card

A tool card is a debug event row, not a chat message. Show icon, name, short target/preview, and status. Arguments and result snippets stay behind expansion. Result snippets should be truncated; full logs belong behind “show more”.

### Thinking/context cards

Same visual family as tool-call metadata. They should be quieter than assistant prose and should not use bright tinted full cards unless the user expands them.

### Composer

The composer is the command surface. Keep it legible and focused: modest radius, subtle border, transparent inactive chips, no theatrical hover scaling.

## Do's and Don'ts

Do:

- Collapse noisy agent internals by default.
- Use one accent color at a time.
- Prefer neutral borders and restrained surfaces.
- Make debug traces accessible and inspectable without making them visually dominant.
- Add stable class/data hooks for future visual regression tests.

Don't:

- Render every tool call as a first-class chat card.
- Mix gold, cyan, purple, orange, red, and green as decorative colors in the same viewport.
- Add new hardcoded radius/color values when a token exists.
- Use shadows, gradients, and hover transforms for routine controls.
- Hide important error or approval states; those are allowed to be prominent because they require action.
