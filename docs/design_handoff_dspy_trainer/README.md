# Handoff: dspy-trainer — DSPy Agent Evaluation Platform

## Overview
`dspy-trainer` is a single-organization web app for evaluating DSPy agents. A developer uploads a **Module Bundle** (`module.py` + `metric.py`), authors a bundle-scoped **Dataset** (`input` + `label` records), creates an **Evaluation Plan** that selects that dataset, and the app runs the agent against the plan many times in parallel, judging each attempt pass/fail with a rationale. MLflow instruments every run at the trace level.

This handoff describes an older design prototype. The current web shell in `web/src` is unauthenticated and routes directly into the app shell without an Auth0 or hosted login step.

This bundle covers five connected flows:
1. **Historical auth concept** — an older Auth0-style hosted sign-in mock that is not part of the current shell.
2. **Team** — member list + a link-based invite modal.
3. **Module Bundles** — list, drag/drop upload with sandbox validation, and a diagnostics detail view.
4. **Datasets** — bundle-scoped dataset list + dedicated item editor for input/label JSON records.
5. **Evaluation Plans** — dataset selector + stress config (runs-per-question, max workers).
6. **Live run monitor (the hero)** — real-time job dashboard with per-item pass/fail, judge results, workers, and an MLflow card.

## About the Design Files
The files in this bundle are **design references authored in HTML/React (via in-browser Babel)** — a working prototype demonstrating the intended look, layout, and behavior. **They are not production code to copy directly.** The task is to **recreate these designs inside the target codebase's environment**, using its established framework, component library, routing, and data layer. If no front-end environment exists yet, choose an appropriate stack (React + a router + a data-fetching layer is the natural fit) and implement the designs there.

All application data in the prototype is **mocked** (`app/mockdata.js`) and the live run is a **client-side simulation** (`app/app.jsx`). In production these are replaced by the real eval API + a streaming/polling transport (SSE or WebSocket recommended for the live monitor). The auth-specific prototype screens are historical reference material only and should not be treated as current product behavior.

## Fidelity
**High-fidelity (hifi).** Final colors, typography, spacing, and interactions are specified below and in `styles.css` as design tokens. Recreate the UI pixel-faithfully using the codebase's libraries. Exact values are given as design tokens; the prototype expresses most colors in `oklch()` — convert to your system's format as needed (approximate hex equivalents are provided).

---

## Global Layout & Shell

- **App frame:** fixed full-viewport, never scrolls at the document level (`html, body { height:100%; overflow:hidden }`). Internal regions scroll independently.
- **Structure:** `Sidebar (232px, fixed)` + `main column (flex:1)`. Main column = `Topbar (52px)` over a routed screen area.
- **Sidebar (232px wide, bg `--bg-deep`):**
  - Org switcher header (52px tall, matches topbar): 26px rounded-square accent logo (`bolt` icon) + org name (13px/600) + "dspy-trainer" mono label (10px) + chevron.
  - Primary nav: Overview, Module Bundles, Datasets, Evaluation Plans, Eval Jobs. Each row: 16px icon (accent when active) + label (13.5px). Active row bg `--surface`, text `--text`; idle text `--text-muted`, hover bg `--panel-2`.
  - Eval Jobs shows a pulsing accent dot when a job is live.
  - Divider, then secondary nav: Team, Settings.
  - Footer: user avatar (28px) + name + email (ellipsised) + sign-out icon button.
- **Topbar (52px, bg `--bg`):** left = breadcrumb trail (org › section › item; last crumb `--text`/500, others `--text-muted`, clickable to navigate). Right = a faux search button with `⌘K` kbd hint + per-screen action buttons.
- **Page pattern:** `.page` (column, overflow hidden) → optional `.page-head` (fixed, 22/32/16 padding, bottom border) + `.page-body` (scrolls, padding 24/32/80). Content max-widths: dashboard 1100px, lists 1000px, builder/detail 720–940px.

---

## Screens / Views

### 1. Historical Auth (`screen_auth.jsx`)
- **Purpose:** archived sign-in / request-access concept from an earlier prototype.
- **Layout:** two columns. Left (`flex:1`, centered, bg `--bg-deep`): a 360px form. Right (46%, max 720px, bg `--bg`, left border, radial accent glow top-right): brand panel.
- **Left form, top→bottom:** logo+wordmark; heading (23px/600) "Sign in to your workspace" (or "Create your account" in signup); subtitle (`--text-muted`); OAuth buttons (full-width, 40px tall, left-aligned, provider glyph + label): **GitHub**, **Google**, then a 2-up row **Microsoft** / **SSO / SAML**; "or" divider; email input (40px) + primary "Continue with email" button; toggle link between sign-in/request-access; footer lockup "Secured by Auth0 · SOC 2 Type II" with shield icon.
- **Right panel:** "LIVE EVAL MONITOR" mono label; a self-animating preview card (running plan, pass/fail counts ticking every ~1.4s); a 19px headline; three feature bullets (icon + text).
- **Behavior:** this was a prototype-only interaction. The current shell does not hand off to Auth0 Universal Login and does not gate access on a sign-in step.

### 2. Dashboard / Overview (`screen_dashboard.jsx`)
- **Purpose:** at-a-glance status + entry points.
- **Layout (max 1100px):**
  - Greeting row: "Good morning, Kira" (display 27px) + subtitle; right: **Upload bundle** + **New plan** buttons.
  - **Live strip** (only when a job is running): full-width card, accent-tinted border, gradient bg. Left: pulsing dot + plan name + mono meta (bundle, workers, runs). Right: "Open live monitor" primary button. Below: segmented progress bar + 4 stats (pass/fail/running/queued). Entire card is clickable → run monitor.
  - **KPI row:** 4 cards (`Pass rate · 7d`, `Eval jobs · 7d`, `Tasks judged`, `Avg item latency`), each = mono label + 24px value + delta + a CSS sparkline (`.bars`).
- **Recent runs** table (plan, bundle, status badge, segmented progress + count, pass %, started, chevron). Rows clickable → run monitor.
  - **Two-up:** "Needs attention" (fail-tinted + warn-tinted alert rows with Fix/Review buttons) and "Quick start" (3 actionable rows: download example, upload module, invite team).

### 3. Module Bundles (`screen_bundles.jsx`)
Three sub-views routed by params (`upload`, `id`, else list).
- **List (max 1000px):** header + "Example bundle" (download) and "Upload bundle" (primary) actions. Each bundle = `.panel.card-pad` row: 40px rounded icon (accent if valid, fail if invalid) + name + version pill + status badge + mono signature; right: error/warn counts, LM target + size/age, chevron. Click → detail.
- **Upload:** dropzone (`1.5px dashed`, turns accent on drag-over) → on drop/click runs a stepped **validation** animation: each step shows spinner→check with mono detail (`Unpacking`, `Sandbox`, `Importing module.py`, `Importing metric.py`, `Resolving signature`, `Smoke-running 1 sample`). On completion: pass banner (with any warnings) + "Create eval plan" CTA. Also shows an "Expected structure" code block and a "Download example bundle" card.
- **Detail (max 940px):** header (icon, name, version, status, mono meta) + actions (download; **Use in plan** if valid, **Re-validate** if invalid). Signature card (code block + LM target / module / metric meta). Segmented tabs: **Diagnostics** (rows with ok/warn/err icon + message + mono code), **module.py**, **metric.py** (syntax-tinted code blocks).

### 4. Datasets (`screen_datasets.jsx`)
- **List (max 1000px):** each dataset row = `layers` icon + name + mono meta (bundle, item count, compact key preview, updated time) + actions (`Edit`, `Duplicate`, `Delete`).
- **Editor:** tabbed editor with `Details` and `Items`.
  - **Details tab:** dataset name, optional description, and bundle picker restricted to validated bundles.
  - **Items tab:** left-side stacked item list (`Input 1`, `Input 2`, etc.) with add/select affordances; right-side item editor with duplicate/delete actions, bundle-schema summary, and full-width JSON editors for `input` and `label`.

### 5. Evaluation Plan builder (`screen_plans.jsx`)
- **List (max 1000px):** each plan row = `layers` icon + name + mono meta (dataset, runs per input, workers, LM profile); **Run** button on drafts; click → run monitor (if run) or edit.
- **Builder:** `.page-head` with Cancel / Save / **Save & run**. Body is a two-pane split:
  - **Main (scrolls):** Plan name input; Module bundle picker (selectable cards of *valid* bundles, accent ring when selected); Dataset picker filtered to the selected bundle; LM profile selector.
  - **Config rail (312px, bg `--bg-deep`, left border):** "AGENT RUN PLAN" label; two **steppers** (Runs per input, Max workers) with −/value/+ controls; a workload card computing `dataset items × runs = total tasks`.

### 5. Live Run Monitor — HERO (`screen_runs.jsx`)
- **Purpose:** watch a job execute in real time, then review results.
- **List view:** jobs table (plan, status, segmented progress + count, pass %, avg score, started). Live job reads from simulation state; others from mock data.
- **Detail (the hero), header:** back link + plan name (nowrap) + status badge + mono meta with job id. Actions: MLflow run; while running → **Pause** + **Stop**; while paused → **Resume**; when finished → Export + **Re-run**.
- **Body = main column + 296px right rail:**
  - **KPI strip:** 6 cells in a 1px-gap grid (hairline dividers): Pass rate (large, tone by threshold), Passed, Failed, Avg score, Avg latency, Elapsed (mono `m:ss`, ticks every 1s).
  - **Progress card:** live dot + "X of Y tasks complete · R running · Q queued" + remaining count; segmented progress bar; legend (pass/fail/running/queued).
  - **Eval Run Items:** filter segmented control (All / Pass / Fail / Running) + a table (status dot, question (ellipsised), run #attempt, status badge or running spinner, score colored by verdict, latency, flag pills). New completions briefly flash (green/red). Click a finished row → **drawer**.
  - **Right rail:** **Workers** panel (one slot per `maxWorkers`; active slots show running dot + current question, idle slots dimmed); **Pass/fail by question** (per-question mini segmented bars + `pass/done` count); **MLflow tracking** card (parent run id, traces linked, experiment, "Open in MLflow").
- **Run-item drawer (right slide-over, 560px, opaque `--panel`, scrim behind):** judge-result hero (tinted by verdict, score + PASS/FAIL + rationale + flag pills); Input prompt (code); two-up **Label payload (gold)** vs **Prediction payload** (code); **Judge raw_response** (JSON code block); footer meta (latency, attempt, MLflow trace id) + Trace button.

### 6. Team (`screen_team.jsx`)
- **Purpose:** manage members; invite via link.
- **Layout (max 880px):** header + **Invite members**; a "seats used" card (icon + count + progress bar); members table (member avatar/name/email, role (Owner = accent pill), status with OAuth provenance, last active, row actions). Invited rows show a dashed mail-circle avatar + "Resend".
- **Invite modal (link-only):** title "Invite team members". A read-only shareable invite link (`https://dspy-trainer.app/join/<org-slug>#<token>`, mono, ellipsised) + **Copy** button (flips to "Copied", fires toast); a hint naming the org + selected role; a **Reset** button (regenerates token, invalidates old link, fires toast); a **Role granted by this link** picker (Member / Admin / Owner, accent ring on selection). Footer: "Link active · expires in 7 days" + primary **Copy invite link**.

---

## Interactions & Behavior

- **Routing:** client-side. Route = `{ name, params }`. Names: `dashboard`, `bundles`, `bundle` (`{upload}` or `{id}`), `plans`, `plan-new` (`{id?}`, `{bundleId?}`), `runs`, `run` (`{jobId}`), `team`, `settings`. Map sub-routes to parent nav highlight. In production use the app's router; breadcrumbs derive from the route.
- **Current shell behavior:** there is no auth gating in the live app shell. This prototype previously showed an unauthenticated entry screen before routing into the dashboard.
- **Live run simulation (replace with real transport):** a seeded job starts running on sign-in. A timer (~1.1s tick) resolves ~30% of in-flight tasks per tick and refills up to `maxWorkers`; each resolution computes a deterministic pass/fail, score, latency, prediction, rationale, and flags. Completion sets job → `succeeded` and fires a toast. **Pause/Resume/Stop** gate the timer. **Re-run** rebuilds all tasks as pending and restarts.
  - *Production:* open the job, then stream `Eval Run Item` updates (SSE/WS). Update counts/progress on each event; flash rows on state change; keep the worker panel bound to currently-running items.
- **Copy link:** `navigator.clipboard.writeText`; button label swaps to "Copied" for 1.8s + toast.
- **Toasts:** bottom-right, auto-dismiss ~4s, slide-in. `{ title, sub?, icon?, tone? }`.
- **Modal/Drawer:** Escape closes; scrim click closes; rendered via portal; modal scales-in, drawer slides from right.
- **Validation flow:** stepped reveal (spinner → check) is cosmetic in the prototype; back it with real sandbox validation events and surface `Diagnostics` (ok/warn/err).

## Animations & Transitions
- `fadeUp` 0.3s, `fadeIn` 0.25s (cubic-bezier(.2,.7,.3,1)) for content entrance.
- `modalIn` 0.2s (scale .97→1), `drawerIn` 0.26s (translateX), scrim `fadeIn` 0.15s.
- `pulse` 1.6s infinite on live/running status dots (expanding ring via box-shadow).
- `flashPass` / `flashFail` 1.1s background fade on newly-completed run-item rows.
- Progress bar widths transition 0.5s cubic-bezier(.2,.7,.3,1). Buttons: bg/border 0.13s, 0.5px press translate.
- `spin` 0.7s linear for spinners.

## State Management
- **Global:** the prototype tracked `authed`, `route`, `live` (the running job: `{ jobId, planId, bundleId, name, status, startedAt, maxWorkers, runsPerQuestion, mlflowParent, items[] }`), and a toast queue. The current app shell no longer uses auth-gating state.
- **Per-screen:** bundle upload `phase`/`step`; plan builder `name/bundleId/runs/workers/rows[]`; run monitor `filter`/selected item/`elapsed`; team `members`/`showInvite`; invite modal `role`/`token`/`copied`.
- **Data fetching (production):** list endpoints for bundles/plans/jobs/team; bundle validation (async, returns diagnostics); plan create/run; job detail + a stream of run-item events; MLflow links.

---

## Design Tokens

> Source of truth: `styles.css` `:root`. Colors are `oklch()`; approximate hex in parentheses.

### Color — surfaces (cool near-black neutral)
| Token | oklch | ~hex |
|---|---|---|
| `--bg` | 0.165 0.004 264 | #181819 |
| `--bg-deep` | 0.135 0.004 264 | #131314 |
| `--panel` | 0.198 0.005 264 | #1e1e20 |
| `--panel-2` | 0.222 0.006 264 | #232325 |
| `--surface` | 0.238 0.006 264 | #27272a |
| `--surface-hover` | 0.275 0.007 264 | #2f2f33 |

### Color — borders & text
| Token | oklch | ~hex |
|---|---|---|
| `--border` | 0.30 0.007 264 | #353539 |
| `--border-soft` | 0.26 0.006 264 | #2c2c2f |
| `--border-strong` | 0.40 0.009 264 | #4c4c52 |
| `--text` | 0.965 0.002 264 | #f4f4f5 |
| `--text-2` | 0.80 0.005 264 | #c6c6c9 |
| `--text-muted` | 0.66 0.006 264 | #9b9ba0 |
| `--text-faint` | 0.52 0.006 264 | #76767b |

### Color — accent (green) & semantic
| Token | oklch | ~hex | Use |
|---|---|---|---|
| `--accent` | 0.74 0.15 156 | #34c27a | primary, live, links |
| `--accent-hi` | 0.82 0.15 156 | #57e09a | hover |
| `--accent-press` | 0.68 0.15 156 | #25a866 | active |
| `--accent-ink` | 0.20 0.04 156 | #11241a | text on accent |
| `--accent-dim` | accent @ 13% | — | tinted bg |
| `--accent-line` | accent @ 30% | — | focus ring/borders |
| `--pass` | 0.76 0.15 156 | #43cc83 | pass (== accent family) |
| `--fail` | 0.67 0.19 25 | #e0573f | fail/danger |
| `--warn` | 0.80 0.13 78 | #d8a93e | warnings/flags |
| `--info` | 0.72 0.12 245 | #4aa3e8 | info |
| `--run` | 0.73 0.12 232 | #4fa6d6 | running/in-flight |
| (each semantic also has a `-dim` @ ~14–15% for tinted backgrounds) | | | |

### Typography
- **Sans (UI):** `Geist` (Google Fonts), weights 300–700. Base 14px / line-height 1.5. `font-feature-settings: 'cv01','cv03','ss01'`.
- **Mono (IDs, scores, code, payloads, labels):** `Geist Mono`, weights 400–600.
- **Scale:** display 27/600/-0.02em · h1 19/600 · h2 15/600 · body 14 · sm 13 · xs 12 · mono section-label 11/500/0.07em/uppercase (`--text-faint`) · caption 12 (`--text-faint`).

### Spacing / Radius / Shadow
- **Gap scale:** 4 / 8 / 12 / 16 / 20 / 24 px (`.gap-1`…`.gap-6`).
- **Radius:** `--r-sm` 5px · `--r` 8px · `--r-lg` 12px · `--r-xl` 16px · pills 999px.
- **Shadow:** `--sh-sm` `0 1px 2px /.4`; `--sh` `0 4px 16px /.35, 0 1px 2px /.4`; `--sh-lg` `0 18px 50px /.55, 0 4px 12px /.4`.
- **Layout constants:** topbar 52px, sidebar 232px.

### Component specs (key ones)
- **Button:** 32px tall (sm 27, lg 40), radius 5px, font 13/500. Variants: default (`--surface` + `--border`), `primary` (`--accent` bg / `--accent-ink` text / 600), `ghost`, `outline`, `danger` (`--fail-dim`/`--fail`). Icon-only = square. Icons 15px (sm 13).
- **Badge/pill:** 21px tall, mono 11px, radius 999px; status variants tint bg + colored text + a 6px status dot.
- **Status dot:** 7px; `running`/`live` pulse.
- **Input/textarea/select:** 34px (textarea auto), `--bg-deep` bg, 1px `--border`, radius 5px, 13px; focus = `--accent-line` border + 3px `--accent-dim` ring.
- **Toggle:** 34×20px pill, knob 16px, on = `--accent`.
- **Segmented control:** `--bg-deep` track, active segment `--surface` + shadow.
- **Table:** mono uppercase 11px headers with bottom hairline; 11/14px cells; row hover `oklch(1 0 0 /.018)`.
- **Code block:** `--bg-deep`, mono 12.5/1.6, syntax tints — keys `--info`, strings `--accent`, comments `--text-faint`, numbers `--warn`.

## Assets
- **Fonts:** Geist + Geist Mono via Google Fonts (`@import` in `styles.css`). Swap to self-hosted in production.
- **Icons:** an inline stroke-icon set (feather/lucide-style, 1.75 stroke) defined in `app/ui.jsx` (`ICONS` map + `<Icon>`). Provider glyphs (Google/Microsoft) are small inline multi-fill SVGs. **Recommendation:** replace with your icon library (e.g. lucide-react) using the same names where possible.
- **No raster images / no brand assets** beyond the generated `bolt` logo mark — substitute the real product logo.

## Files (in this bundle)
- `dspy-trainer.html` — entry; loads React 18 + Babel (CDN, pinned), `styles.css`, then the scripts below, mounts `<App/>` in `#root`.
- `styles.css` — all design tokens (`:root`) + global styles + primitive component classes. **Primary reference for tokens.**
- `app/mockdata.js` — mock data model + helpers (`window.DB`): `ORG`, `USER`, `team`, `bundles`, `plans`, `jobs`, `QUESTIONS`, and `counts/passRate/avgScore/samplePrediction` helpers. **Defines the domain entities** (Module Bundle, Evaluation Plan, Eval Job, Eval Run Item, diagnostics, judge result shape).
- `app/ui.jsx` — primitives + icon set: `Icon, Button, Badge, Dot, Progress, SegProgress, Avatar, Modal, Drawer, Empty, ToastHost/useToast, ago, dur`.
- `app/shell.jsx` — `Sidebar, Topbar, AppShell`, nav definitions.
- `app/screen_auth.jsx` — historical auth screen + animated preview from the earlier prototype.
- `app/screen_dashboard.jsx` — Overview (KPI cards, live strip, recent jobs).
- `app/screen_bundles.jsx` — bundle list / upload+validation / detail (+ sample `module.py`/`metric.py`).
- `app/screen_plans.jsx` — plan list + builder.
- `app/screen_runs.jsx` — job list + live monitor + run-item drawer.
- `app/screen_team.jsx` — team + link invite modal.
- `app/app.jsx` — prototype root: routing, historical auth state, the live-run simulation engine, Settings screen.

## Domain model (recreate as real types)
- **Module Bundle:** `{ id, name, version, status: valid|invalid|validating, uploadedAt, size, author, signature, lmTarget, dspyVersion, diagnostics: [{ level: ok|warn|err, code, msg }] }`.
- **Evaluation Plan:** `{ id, name, bundleId, status: draft|queued|running|succeeded|failed, questions: [{ id, input, expected }], runsPerQuestion, maxWorkers, createdAt, createdBy }`.
- **Eval Job:** `{ id, planId, bundleId, status, startedAt, runsPerQuestion, maxWorkers, items: EvalRunItem[], mlflowParent }`.
- **Eval Run Item:** `{ id, jobId, qId, qIndex, attempt, input, expected, status: pending|queued|running|pass|fail, score, durationMs, traceId, prediction: { ... }, rationale, flags: string[] }`.
- **Judge result (per item):** `{ score (0–1), passed: bool, rationale, flags[], raw_response }`. Pass threshold in the sample metric is `score >= 0.7`.
- **Agent Run Plan / Tasks:** `total tasks = questions × runsPerQuestion`; concurrency capped at `maxWorkers`.

## Implementation notes
- **Critical layout rule:** the app must be height-constrained to the viewport with internal scroll regions — do **not** vertically center the shell. (In the prototype a shared `.row`/`.center` utility set `align-items:center`, which clipped tall pages; ensure your shell uses `align-items: stretch` and `min-height: 0` on flex children that contain scroll areas.)
- The live monitor is the product's centerpiece — invest in a robust streaming update path and smooth row/count transitions.
- Replace the in-browser-Babel + global-`window` component wiring with your build system's modules/imports.
