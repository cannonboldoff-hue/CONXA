# Product

## Register

product

## Platform

web

## Users

Mixed skill levels operate Build Studio: non-technical staff (product managers, ops, support leads) record workflows because they know the target software, while technical staff step in for Human Edit and recovery-tier debugging when a compile produces a low-confidence selector or a step needs its element changed. The app has to hold a non-expert's hand through recording and compiling, while still exposing enough precision (selector confidence, identity signals, recovery tiers) for a technical operator to diagnose and fix a broken step.

## Product Purpose

Build Studio turns recorded browser workflows into a distributed, self-healing skill package, entirely on the operator's local machine. The task flow is a linear pipeline — Workflows (create + record) → Compile → Human Edit (fix issues) → Test Skill → Publish Skill Package → Build Installer — and each screen is one stage of that pipeline. Success is a compiled skill package that a customer's AI agent can execute reliably without the operator ever writing a selector by hand. Many workflows can target the same company and compile together into one shared skill package, rather than creating separate packages per recording.

## Positioning

Record it once here; everything after that — compilation, recovery, distribution, updates — happens automatically. The operator's only job is teaching the workflow once.

## Brand Personality

Precise and guided, at once. Every screen carries correctness-critical information — selector confidence, compile status, recovery tier — and that information has to read as exact and dependable, never approximate. At the same time the app can't assume technical fluency: status, next steps, and error explanations need plain language and a clear "what do I do now," because a non-technical operator is often the one looking at the screen when something needs a decision.

## Anti-references

Not legacy RPA/dev tooling: no dense raw-config panels, no exposed JSON editors, no interface that assumes the operator already knows what a selector or a DOM node is. Not consumer-app playful either: no mascots, no cartoonish illustration, no casual copy — this is a precision tool for work that has to be right, not a consumer product. The target is a professional-but-approachable middle ground: exact status communicated in plain language.

## Design Principles

- Show confidence, don't hide it: selector durability, identity-signal quality, and recovery tier are the product's core trust mechanism — surface them, don't bury them behind a details toggle.
- Guide the next action, always: every screen in the pipeline should make the next step (and what happens if you skip it) obvious to someone who has never seen a DOM inspector.
- Precision in status, plainness in explanation: numbers, badges, and states should be exact; the copy explaining them should assume no technical vocabulary.
- Recoverable by design: mistakes in Record, Compile, or Human Edit should be visibly undoable or re-editable — the tool should never feel like one wrong click breaks the whole session.
- One pipeline, not six tools: Record/Compile/Human Edit/Test/Publish/Build Installer should feel like stages of one continuous flow, not six disconnected screens with their own conventions.

## Accessibility & Inclusion

WCAG 2.1 AA baseline.
