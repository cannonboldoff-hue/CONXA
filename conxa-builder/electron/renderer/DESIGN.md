---
name: Conxa Build Studio
description: Record a workflow once; everything after that is automatic.
colors:
  conxa-clay: "#d97757"
  clay-hover: "#e08565"
  charcoal-base: "#1a1a1a"
  charcoal-sidebar: "#1e1e1e"
  charcoal-surface: "#252525"
  charcoal-raised: "#2c2c2c"
  warm-ivory: "#e8e6e3"
  soft-gray: "#a3a3a3"
  slate-gray: "#8e8e8e"
  iron-gray: "#6b6b6b"
  action-pink: "#e88fb4"
  progress-blue: "#93b4e6"
  recovery-green: "#4ade80"
  caution-amber: "#fbbf24"
  alert-red: "#f87171"
typography:
  headline:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "normal"
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "normal"
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "normal"
rounded:
  sm: "6px"
  md: "8px"
  lg: "10px"
  xl: "14px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
components:
  button-brand:
    backgroundColor: "{colors.conxa-clay}"
    textColor: "#1a1a1a"
    rounded: "{rounded.lg}"
    padding: "6px 10px"
  button-brand-hover:
    backgroundColor: "{colors.clay-hover}"
    textColor: "#1a1a1a"
    rounded: "{rounded.lg}"
    padding: "6px 10px"
  button-outline:
    backgroundColor: "{colors.charcoal-base}"
    textColor: "{colors.warm-ivory}"
    rounded: "{rounded.lg}"
    padding: "6px 10px"
  card-surface:
    backgroundColor: "{colors.charcoal-surface}"
    textColor: "{colors.warm-ivory}"
    rounded: "{rounded.md}"
    padding: "16px"
  badge-action:
    backgroundColor: "transparent"
    textColor: "{colors.slate-gray}"
    rounded: "4px"
    padding: "2px 8px"
---

# Design System: Conxa Build Studio

## 1. Overview

**Creative North Star: "The Workbench"**

Build Studio is a craftsman's workspace, not a showroom. Every surface sits at one of three flat charcoal steps — base, sidebar, surface — and nothing on screen competes for attention except the single clay-orange accent, reserved for the one action currently worth taking: the primary CTA — the one next action in view. Selection is a neutral tint, not clay. Depth comes from a hairline border and a shift in background tone, never a shadow; a workbench doesn't need theatrical lighting, it needs every tool exactly where the operator expects it.

That restraint is deliberate given who's standing at the bench: a non-technical operator recording a workflow needs the UI to stay legible and low-anxiety, while a technical operator debugging a failed compile needs exact, trustworthy status information. The system resolves that tension by keeping color meaningful rather than decorative — status is never color-only (see the StatusDot pattern in §6) — and by keeping typography and spacing plain enough that nothing reads as "designed for effect."

This system explicitly rejects two things: legacy RPA/dev-tooling density (raw config panels, exposed JSON editors, an interface that assumes the operator already knows what a selector is) and consumer-app playfulness (mascots, cartoon illustration, casual copy). The target is the professional-but-approachable middle this PRODUCT.md calls for — precise in its numbers, plain in its language.

**Key Characteristics:**
- Three flat charcoal steps (#1a1a1a → #1e1e1e → #252525), no shadows, ever.
- One accent color, Conxa Clay, spent only on the single next action in view. Selection is a 7% neutral tint.
- Status is shape-plus-color, never color alone (StatusDot's glyph pattern).
- Inter throughout, small and quiet — the UI gets out of the way of the data it's showing.
- Categorical action colors (click/type/navigate/etc.) are a separate, deliberately distinct palette from the brand accent — see §2.

## 2. Colors

A near-monochrome charcoal workspace with one warm accent held in reserve for the current primary action.

### Primary
- **Conxa Clay** (#d97757): The brand accent. Primary CTA fill and focus rings. Never used for anything but "this is the current, primary action." (Amended 2026-10-18: it no longer tints selected items — selection is `rgba(255,255,255,0.07)`.)
- **Clay Hover** (#e08565): Hover state for clay-filled elements — a touch lighter and warmer, not a different hue.

### Neutral
- **Charcoal Base** (#1a1a1a): Page background — the deepest, most-resting surface.
- **Charcoal Sidebar** (#1e1e1e): Navigation/sidebar background — one step up from base.
- **Charcoal Surface** (#252525): Cards, panels, and raised content areas — the lightest neutral surface at rest. Cards in the conversation and user messages sit here.
- **Charcoal Raised** (#2c2c2c, added 2026-10-18): a fourth step for fields and rows nested inside a card, and popovers. Never a page background.
- **Warm Ivory** (#e8e6e3): Primary text. Warm off-white, not pure white — keeps the whole palette in one temperature family.
- **Soft Gray** (#a3a3a3, 7.4:1): secondary prose — AI-written supporting lines.
- **Slate Gray** (#8e8e8e, 5.4:1): meta text — labels, timestamps, inactive action badges. The floor for anything a person must read.
- **Iron Gray** (#6b6b6b): disabled only, never for text a person needs to read. (Amended 2026-10-18: the former #5a5a5a was 2.6:1 and is retired for text.)

### Status
- **Recovery Green** (#4ade80): Published / synced / healthy state.
- **Caution Amber** (#fbbf24): Unpublished changes / needs-attention state.
- **Alert Red** (#f87171): Compile error / failed state.
- **Progress Blue** (#93b4e6, added 2026-10-18): in progress — recording, compiling, running.

Glyph vocabulary, identical in the sidebar, inspector, cards and group table: `●` ready/passed, `▲` needs you, `✕` failed, `◐` in progress, `○` not started. Defined once in `renderer/src/lib/status.ts`.

### Named Rules
**The One Accent Rule.** Conxa Clay marks exactly one thing per screen: the single next action in view (the inspector's primary button, the composer's send, the one actionable answer in a question card). It is never used decoratively, and never doubles as a status color — status has its own green/amber/red vocabulary, kept visually distinct from the brand accent so "this is active" and "this is healthy" never get confused.

## 3. Typography

**Display Font:** Inter (with system-ui fallback)
**Body Font:** Inter (with system-ui fallback)

**Character:** One typeface, weighted for legibility over expression. Inter is chosen precisely because it disappears — the operator should notice the compile confidence score, not the font.

### Hierarchy
- **Headline** (600, 1.5rem, line-height 1.25): Page-level titles (e.g. a plugin or session name at the top of a screen).
- **Title** (600, 1rem, line-height 1.4): Card and section titles within a screen.
- **Body** (400, 0.875rem, line-height 1.5): Default UI text — labels, descriptions, form fields, table cells.
- **Label** (500, 0.6875rem, no tracking): Small meta text — the ActionBadge chip, status captions, timestamps.

### Named Rules
**The No-Shout Rule.** Nothing in this system uses uppercase tracking or oversized display type. The largest text in the app is 1.5rem. Precision tools don't need a hero headline.

## 4. Elevation

No box-shadow scale exists anywhere in the system. Depth is communicated by two things only: a **background tone step** (Charcoal Base → Sidebar → Surface, each a small lightness increase) and a **hairline border** (`--border-subtle` at 8% white, `--border-default` at 10% white). A raised or focused element gets a brighter border and a lighter background, not a shadow.

### Shadow Vocabulary
None. This system is intentionally flat.

### Named Rules
**The Flat Workbench Rule.** If a component needs to signal "this is above/separate from its surroundings," reach for the next charcoal step up and a slightly brighter border — never a drop-shadow. A shadow on a near-black surface reads as murky, not elevated.

## 5. Components

### Buttons
- **Shape:** 10px radius (`{rounded.lg}`).
- **Brand (primary):** Conxa Clay background, `#1a1a1a` text, hover shifts to Clay Hover. Reserved for the one primary action per screen — "Compile," "Publish," "Build Installer."
- **Outline / Secondary / Ghost:** Charcoal-base or transparent background, Warm Ivory or Slate Gray text, border-driven distinction rather than fill.
- **Destructive:** Low-opacity Alert Red fill (`bg-destructive/10`), full-strength red text — never a solid red button.

### Chips / Badges
- **Action badge:** Transparent background, 1px border in the action's category color, 4px radius, 11px text, capitalized. Category colors are their own small palette (`click`/`select` → pink #e88fb4 (amended 2026-10-18 — clay is never a step-type colour), `type` → #5b9bd5 blue, `navigate` → #9d7cd8 purple, `scroll`/`hover` → grays) — deliberately separate from the brand accent so action-type identity doesn't compete with "this is the primary action."
- **Status glyph (StatusDot):** Not a filled badge — a small glyph whose *shape* changes with status (`●` ready, `▲` needs you, `✕` failed, `◐` in progress, `○` not started) in addition to its color, so status is never carried by hue alone. This is the system's signature accessibility pattern; carry the shape-plus-color rule into any new status indicator.

### Cards / Containers
- **Corner Style:** 8px radius (`{rounded.md}`).
- **Background:** Charcoal Surface at rest.
- **Shadow Strategy:** None — see §4. Separation comes from the border and the tone step against whatever sits behind it.
- **Border:** `--border-subtle` (8% white) at rest, brightening on hover/focus.
- **Internal Padding:** 16px (`{spacing.md}`).

### Inputs / Fields
- **Style:** Charcoal-base background, `--input` border, 10px radius.
- **Focus:** Ring in `--ring` at 50% opacity, border shifts to match.
- **Error / Disabled:** Alert Red ring/border for invalid state; 50% opacity + no pointer events when disabled.

### Navigation
- **Sidebar:** Charcoal Sidebar background, Slate Gray link color at rest, Warm Ivory with a 4% tint on hover and a 7% tint when selected — no underline, no clay.

### Overlay Motion (signature interaction)
Radix-driven popovers, dialogs, and tooltips use a shared `.anim-pop` entrance/exit: 150ms ease-out-expo pop-in (`scale(0.96) → scale(1)`, slight upward slide), 110ms ease-in pop-out. `prefers-reduced-motion` collapses both to near-instant (0.01ms). Any new overlay component should reuse `.anim-pop` rather than inventing a new transition.

## 6. Do's and Don'ts

### Do:
- **Do** spend Conxa Clay on exactly one thing per screen: the single next action in view (The One Accent Rule).
- **Do** signal status with shape and color together, never color alone — follow StatusDot's glyph pattern for any new status indicator.
- **Do** keep depth to a tone-step-plus-border; never introduce a box-shadow (The Flat Workbench Rule).
- **Do** keep every text role in Inter, capped at 1.5rem — this is a precision tool, not a marketing page (The No-Shout Rule).
- **Do** explain status and errors in plain language even where the underlying number (confidence score, recovery tier) is exact and technical.

### Don't:
- **Don't** use Conxa Clay as a status color — green/amber/red carry health/state; clay carries "this is the action."
- **Don't** add a drop-shadow anywhere in this system.
- **Don't** introduce a raw JSON editor or dense config-panel view as the default way to expose data — surface it through named fields and plain-language labels instead, per PRODUCT.md's anti-reference against legacy RPA tooling.
- **Don't** add mascots, cartoon illustration, or casual copy — this is a precision tool, not a consumer app.
- **Don't** use uppercase tracked text or oversized display type; the largest headline in the system is 1.5rem.
