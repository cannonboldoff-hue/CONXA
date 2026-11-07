# Frontend Refactor Report — `conxa-cloud/frontend`

> **Read-only audit.** No source files were changed. Compiled from four parallel deep-audits:
> 1. UI / product / release / viz components
> 2. Marketing components
> 3. App routes, API layer, lib, dashboard pages, proxy
> 4. Config, dependencies, build setup
>
> Date: 2026-08-22

---

## Executive Summary

The frontend is structurally sound: strict TypeScript with near-zero `any`, a disciplined react-query setup with a central query-key registry, thin server-component page wrappers, and a well-built API proxy that strips spoofable internal headers. The debt falls into five buckets:

| Bucket | Scale | Risk |
|---|---|---|
| Unused heavy dependencies (3D/Spline experiments) | ~45 MB install weight | Medium |
| Dead code (components, exports, assets) | ~900+ lines + 40 KB assets | Low |
| Duplication (badges, formatters, dialogs, tables, section shells) | 30+ duplicated sites | Medium |
| Hardcoded hex palette (~250 literals in marketing) vs existing token system | ~250 sites | Low |
| Missing frontend CI gate | 0 workflows | High |

**One security item found:** the `/api/v1` proxy forwards unauthenticated requests upstream instead of rejecting them.

---

## 1. P0 — Must Do First

### 1.1 Proxy does not enforce authentication
`app/api/v1/[...path]/route.ts:43–60` — when no Clerk session exists, the request is still forwarded upstream (just without an `Authorization` header). If any backend runs with `SKILL_AUTH_REQUIRED=false`, the proxy becomes an open relay.
- Reject requests without a session except an explicit webhook-path allowlist (`/api/v1/webhooks/*`, already exempted in `proxy.ts:14`).
- Also remove `origin` from the 503 body (`route.ts:108`) — it leaks `API_ORIGIN` to clients.
- Add an `AbortSignal.timeout()` on the upstream fetch (`route.ts:94`) and consider stripping `cookie`.

### 1.2 Remove unused heavy dependencies
Zero imports verified across all source trees:
- `three` (35.3 MB), `@react-three/fiber`, `@react-three/drei`, `@types/three`
- `@splinetool/react-spline`, `@splinetool/runtime` (only consumer is dead code — see §2)
- `zustand` — no store exists anywhere
- `d3-array` + `@types/d3-array` — transitive via d3-scale; direct entries redundant

### 1.3 Add frontend CI
No GitHub workflow lints/type-checks/builds/tests this app. The only test (`test/releaseState.test.mjs`) is orphaned. Add a workflow running: `npm run lint`, `npx tsc --noEmit`, the node test script, `npm run build`.

---

## 2. Dead Code (~zero-risk deletions)

| Item | Location |
|---|---|
| `SplineScene.tsx` — fully dead; also returns `null` because its scene URL was never filled in | `src/components/marketing/3d/SplineScene.tsx` |
| Entire `jobsApi.ts` module (70 lines incl. its hand-rolled retry) | `src/api/jobsApi.ts` |
| 7 unused functions + orphaned types in productApi (`fetchDashboard`, `fetchUsage`, `fetchInstallerDomain`, `fetchJobs`, `cancelJob`, `fetchRelease`, `patchRelease`) | `src/api/productApi.ts:129–180` |
| Legacy tracker types/functions (`Run*`, `fetchTrackingRuns`, `fetchTrackingCompanies`, `fetchTrackingDiagnostics`, duplicate `RuntimeRegistration` trio) | `src/api/workflowsApi.ts:42–68, 105–136, 535–555, 601–619` |
| `toneStyle()` — never imported | `src/lib/tone.ts:15–46` |
| 6 unused shadcn primitives (~410 lines): select, tabs, scroll-area, separator, checkbox, textarea | `src/components/ui/*` |
| Dead exports in ProductPrimitives (~110 of 170 lines): PageHeader (name-collides with layout/PageHeader!), StatusBadge, StatCard, ResourceToolbar, DataTable, ActivityTimeline, UsageMeter, GlobalCreateMenu, literal-null ConfirmDialog/RenameDialog | `src/components/product/ProductPrimitives.tsx` |
| `ChartFrame.ChartEmpty` — documented, never rendered | `src/components/viz/ChartFrame.tsx:81–101` |
| Unreferenced assets: `public/favicon.svg`, `public/icons.svg`, `src/assets/{hero.png, react.svg, vite.svg}` | — |

---

## 3. Duplication (consolidation targets)

### Cross-page (dashboard area)
- **6 status-badge implementations over one concept:** `lib/tone.ts`, FleetPage STATUS_TONE (:25–35), SettingsPage StatusBadge (:53–59), TeamPage StatusPill (:65–80), AuditPage ActionBadge (:119–135), RunDetailPage/LiveActivity pills, ReleaseStatusBadge → consolidate into one tone map in `lib/tone.ts`.
- **Formatters ×11:** `formatRelative` ×3, epoch-formatting ×3, `titleCase` ×3, number formatting ×4 overlapping → one `src/lib/format.ts`. Also centralize the hardcoded 95/85 rate-tone thresholds (WorkflowsPage:16 = WorkflowDetailPage:17).
- **`SummaryMetric` ×3** (AuditPage:94 ≡ FleetPage:63 byte-for-byte; TeamPage:95 variant) plus `SummaryStat` ×2 → one shared metric component.
- **Search+filter bar ×2**, hand-rolled tables ×4, bar-meter rows ×5, KPI-strip markup ×3 → extract FilterToolbar / DataTable / MeterBar primitives.
- **Query options drift:** same endpoints cached with different staleTime/retry/refetchInterval per page (entitlements ×4 sites, me ×2, trackingDashboard omits refetchInterval in ImpactPage) → shared hooks `useEntitlements()`, `useMe()`, `useTrackingDashboard(range)`; route the 3 inline invalidate keys through `queryKeys.ts` (FleetPage:192, DashboardShell:64, ImpactPage:57).
- **Two competing loading/error systems:** DashboardStates primitives vs ProductPrimitives vs inline skeletons → pick one.

### Release components
- **`ReleaseDialog` ≈ `RollbackDialog` 90% identical** (state scaffold :35–45, pending guard :50–54, chrome :62, confirm handler :83–87 in both) → one `ConfirmMutationDialog` + `useConfirmMutation` hook.
- Clerk appearance blocks duplicated verbatim between AccountControls:13–24 and AppChrome:236–263 → mobile topbar should render `<AccountControls />`.
- `formatTimestamp` defined twice (ReleaseHistoryTable:11, ReleaseAuditLog:25).

### Marketing
- **Draw-on-scroll scaffold ×3 + Reveal variant** (PathVsGuesswork:8–14, TheGap:32–36, Reliability:60–64, Reveal:13–16) → one `useDrawInView()` hook.
- Tick/dash SVG icons ×4 (Comparison:65–78, PricingTable:68–79, TheGap:115–132); avatar block ×5 inside ChatPanel.
- Section shell `<section id class="relative bg-[…] px-6 py-28">` repeated verbatim ×9 → `<MarketingSection>` wrapper.
- Easing constant `[0.16, 1, 0.3, 1]` pasted ×9 → shared `EASE`.
- Button override blob `"border-white/10 bg-white/[0.04]…"` bolted onto `variant="outline"` ×12 across the app → add proper cva variants (`surface`, `success`) to `ui/button.tsx`.

### API layer
- `fetchRuntimeRegistrations` + `RuntimeRegistration` duplicated workflowsApi ↔ telemetryApi (with *different shapes*); `JobRecord` duplicated jobsApi ↔ productApi.
- `workflowsApi.ts` is 793 lines mixing types and fetchers → split into tracking/release/skill-pack modules.

---

## 4. Oversized Files (>300 lines)

| File | Lines | Suggested split |
|---|---|---|
| `src/content/publicDocs.ts` | 1,978 | Move doc content to markdown loaded at build time (only if authoring frequency grows) |
| `src/BillingPage.tsx` | 826 | Split into `src/billing/` components (PlanCard, AddonPanel, EnterpriseContactDialog…) |
| `src/api/workflowsApi.ts` | 793 | Split by domain; ~450 lines are pure types |
| `src/SettingsPage.tsx` | 501 | Split into `src/settings/` |
| `marketing/docs/PublicDocs.tsx` | 460 | Dedupe desktop/mobile sidebar rendering (:92–134 vs :136–194); optional file split |
| `src/TeamPage.tsx` | 364 | Extract dialogs/badges |
| `marketing/hero/BrowserSim.tsx` | 331 | Cohesive; keep, but gate animations (§5) |
| `src/AuditPage.tsx` | 341 | Benefits from §3 extractions |
| `src/SkillPackageVersionsPage.tsx` | 323 | Same |
| `layout/AppChrome.tsx` | 274 | Natural seams exist: SidebarNav (:66–170), mobile header (:199–265), nav config (:34–53) |

---

## 5. Performance & Runtime Concerns

### Marketing homepage burns CPU indefinitely
- `hero/BrowserSim.tsx:100–124`: infinite `while (!cancelled)` loop driving per-char typing at 60–100 ms sleeps — runs even when scrolled off-screen or tab hidden. No IntersectionObserver, no visibilitychange, no reduced-motion support.
- `hero/ChatPanel.tsx:49–96`: parallel timer chain restarting every 32 s, same gaps.
- Fix: pause both when off-screen/hidden; honor `useReducedMotion`. Highest-impact perf win in the codebase.
- `LenisProvider.tsx:7–23`: permanent rAF loop with no reduced-motion opt-out.

### Assets & payloads
- `public/conxa-icon.png` is **934 KB** for a nav/footer/favicon logo; rendered via raw `<img>` in MarketingNav:39–45 (footer correctly uses next/image). Recompress + sized favicon/OG variants.
- HowItWorks mounts all 4 screenshots eagerly stacked (HowItWorks:135–148); lazy-load non-first shots.
- `PricingTable.tsx:102` client-fetches public plans → skeleton shift in an SEO-relevant section; consider RSC fetch.

### Bundle hygiene
- framer-motion used by marketing only; Reliability.tsx drags d3 viz components into the marketing bundle through a client boundary (:7–8).
- `shadcn` sits in devDependencies but `src/index.css:3` imports `shadcn/tailwind.css` — load-bearing for prod builds; breaks under `npm ci --omit=dev`. Move to dependencies or vendor the CSS.

---

## 6. Styling Consistency

- **~250 raw hex literals in marketing** (`#06080b`, `#0b0f14`, `#0f1620`, `#22d3ee`…) while `src/index.css` already defines tokens — the brand accent `#22d3ee` literally exists as `--tier-4`. Promote recurring surfaces (`--surface-base/raised`, fg/muted/accent) to `@theme` and sweep once.
- Dashboard side has the same problem smaller: `#06080b`×22, `#0b0f14`×21, `#0d0f12`×8 etc. across components.
- Two idioms mixed in one tree: Tailwind arbitrary gradient at AppChrome:187 vs inline style at :190.
- viz/ is the model citizen: token-based `chartTheme.ts`, `var(--status-*)`, good a11y.

---

## 7. TypeScript & Small Correctness Nits

- Zero `any` in audited areas — excellent. Remaining escapes:
  - `(e as Error)?.message` casts: ReleaseDialog:44, RollbackDialog:44.
  - Double-casts around d3-sankey: RecoverySankey:87–88.
  - Fragile optional-chain narrowing: Heatmap:80–82.
  - Untyped cast for step icon: ChatPanel:76; unused `target` prop: BrowserSim:202.
- Typo bugs: sales email `noreplay@conxa.in` presented as an enterprise contact (drops leads) — BillingPage:66–69; Cyrillic `а` inside demo filename — executionScript.ts:50.
- Magic-string error contract `'ops_tier_required'` string-matched in DashboardStates:12 and inline-repeated AuditPage:281 → typed error flag/class.
- Duplicated env fallbacks for `NEXT_PUBLIC_SALES_PHONE_*` (publicDocs.ts:64–66 ≡ BillingPage:67–69); these vars are also missing from all `.env*example` files.
- tsconfig hardening available: `noUncheckedIndexedAccess`, `noUnusedLocals/Parameters`; reconsider `allowJs`; note `skipLibCheck` hides the shadcn-devdep coupling above.
- eslint config doesn't lint `.js/.mjs`; two react-hooks rules blanket-disabled without justification comments; undocumented `postcss` override pin in package.json.
- `window.confirm()` for destructive actions (FleetPage:159, SettingsPage:212,401) while shadcn alert-dialog ships installed but unused elsewhere.

---

## 8. What's Working Well (preserve)

- Central query-key registry (`src/lib/queryKeys.ts`) used almost everywhere.
- Thin server page wrappers + client islands; docs `[slug]` route properly SSG'd with generateStaticParams/Metadata.
- Proxy's internal-header stripping (`INTERNAL_PROXY_HEADERS`) — correct anti-spoofing.
- Pure `releaseState.ts` + zero-dependency node:test pairing.
- `useRange` URL-state pattern; DashboardStates upgrade-vs-error distinction.
- viz/: consistent d3 approach, shared theme, real accessibility (`role="img"`, aria-labels, sr-only text).
- Clean env handling: server vars each used in exactly one place; three tracked `.env*example` lanes; no leaked backend URLs.

---

## 9. Recommended Execution Order

| Phase | Items |
|---|---|
| **P0** | §1.1 proxy auth enforcement + origin leak fix · §1.2 dep removal · §1.3 CI workflow · §2 dead-code sweep |
| **P1** | Animation gating (§5) · icon.png recompress · shadcn devDep fix · email typo · API-layer dedup + workflowsApi split · query-options consistency hooks |
| **P2** | Badge/tone consolidation · format.ts extraction · SummaryMetric/FilterToolbar/MeterBar/DataTable primitives · useChartTooltip + useDrawInView + ConfirmMutationDialog hooks · marketing token sweep |
| **P3** | File splits (BillingPage, SettingsPage, AppChrome) · MarketingSection wrapper · tsconfig/eslint hardening · remaining nits |

Rough effort estimate: P0 ≈ half a day, P1 ≈ 2–3 days, P2 ≈ 3–5 days, P3 ≈ ongoing.
