# Constellation Design Bible

Knowledge-Ops "Constellation" — Magic Unicorn's governed organizational memory.
Thesis: **watch your organization think.**

This document is enforceable. Reviewers should reject any change that violates
a MUST in this file. The implementation lives in:

- `knowledge_ops/static/ui/tokens.css` — every color, radius, font, and motion
  value, for all four themes.
- `knowledge_ops/static/ui/base.css` — the component vocabulary views consume.
- `knowledge_ops/static/ui/fonts.css` + `ui/fonts/` — vendored Fraunces
  (SIL OFL 1.1), woff2 only, zero external requests.

---

## 1. Vision

A **deep-space observatory for organizational memory**. The constellation
canvas is the hero; all chrome recedes. The interface should feel like standing
at an eyepiece: dark, calm, precise instruments around a luminous field of
knowledge. Wonder + substance — playful and a little unconventional, but every
flourish is backed by evidence.

Governance is sacred to the aesthetic, not just the copy. Verified facts get
weight and glow; heuristic retrieval stays visually modest ("related evidence —
heuristic, not verification"). The design must never dress a guess up as a
finding.

Principles:

1. **The constellation is the hero.** Surfaces are quiet; color belongs to
   sources and evidence, not to decoration.
2. **Chrome recedes.** Borders are hairlines, backgrounds are near-flat,
   density is calm. Content measure tops out at ~72ch.
3. **Glow is a scarce resource.** Spent decisively (see §6), never ambient
   everywhere.
4. **Data looks like data.** Mono face + `tabular-nums` for counts, scores,
   paths, timestamps.
5. **No emoji-as-UI. No generic gradient-hero clichés.** The one brand gradient
   is used sparingly and deliberately.

---

## 2. Themes

Themes are set as `data-theme` on `<html>`: `aurora` (default) · `daylight` ·
`nebula` · `midnight`. Un-themed first visits on light-mode systems get
daylight values via `prefers-color-scheme`. Every contract token exists in all
four themes — a view MUST render correctly in all four with zero view-side
changes.

| Theme | One-line character |
|---|---|
| **Aurora** | Near-black blue-violet ground; violet→teal nebula; starlight text; glow spent sparingly. |
| **Daylight** | Warm paper and ink; the constellation becomes an ink-and-gold engraving. Glow off. |
| **Nebula** | Saturated drifting gradients + glass surfaces. The demo-reel theme. Glow at full. |
| **Midnight** | Pure OLED black, restrained cyan, minimal glow. For long nights on call. |

### 2.1 Palette — Aurora (default)

| Token | Hex / value |
|---|---|
| `--mu-color-bg` | `#080a14` |
| `--mu-color-bg-elevated` | `#0d1020` |
| `--mu-color-surface` | `#10142a` |
| `--mu-color-surface-strong` | `#171c38` |
| `--mu-color-border` | `rgba(167,139,250,.16)` |
| `--mu-color-border-strong` | `rgba(155,124,246,.55)` |
| `--mu-color-text` | `#eef0fb` |
| `--mu-color-muted` | `#a9b0d2` |
| `--mu-color-dim` | `#7c84ac` |
| `--mu-color-primary` | `#9b7cf6` |
| `--mu-color-accent` | `#46c2b3` |
| `--mu-color-success` | `#54f0b4` |
| `--mu-color-warning` | `#ffd166` |
| `--mu-color-danger` | `#ff6b8a` |
| `--mu-gradient-brand` | `135deg #a78bfa → #46c2b3` |
| `--mu-glow` | `0.8` |
| `--mu-canvas-bg` / `-star` / `-edge` | `#05060d` / `#ccd3f6` / `rgba(155,124,246,.28)` |
| src: wiki / org-memory / meetings / stable / files / graph / metrics | `#b487ff` `#ff9ed8` `#5eead4` `#8ab8ff` `#ffd166` `#54f0b4` `#f0a354` |

### 2.2 Palette — Daylight

| Token | Hex / value |
|---|---|
| `--mu-color-bg` | `#f6f1e7` |
| `--mu-color-bg-elevated` | `#fbf7ef` |
| `--mu-color-surface` | `#fffdf8` |
| `--mu-color-surface-strong` | `#f0e8d8` |
| `--mu-color-border` | `rgba(58,47,38,.18)` |
| `--mu-color-border-strong` | `rgba(109,77,181,.5)` |
| `--mu-color-text` | `#241d13` |
| `--mu-color-muted` | `#5b5344` |
| `--mu-color-dim` | `#6e6455` |
| `--mu-color-primary` | `#6d4db5` |
| `--mu-color-accent` | `#0f766e` |
| `--mu-color-success` | `#166534` |
| `--mu-color-warning` | `#8a5306` |
| `--mu-color-danger` | `#be123c` |
| `--mu-gradient-brand` | `135deg #6d4db5 → #0f766e` |
| `--mu-glow` | `0` (engraving, not lightshow) |
| `--mu-canvas-bg` / `-star` / `-edge` | `#f2ebdc` / `#2c2417` (ink) / `rgba(150,116,47,.55)` (gold hairline) |
| src: wiki / org-memory / meetings / stable / files / graph / metrics | `#6d28d9` `#a8336f` `#0f766e` `#1d4ed8` `#8a5306` `#166534` `#b4470b` |

### 2.3 Palette — Nebula

| Token | Hex / value |
|---|---|
| `--mu-color-bg` | `#0b0620` |
| `--mu-color-bg-elevated` | `#130b2e` |
| `--mu-color-surface` | `rgba(255,255,255,.055)` (glass) |
| `--mu-color-surface-strong` | `rgba(255,255,255,.11)` (glass) |
| `--mu-color-border` | `rgba(255,255,255,.16)` |
| `--mu-color-border-strong` | `rgba(216,180,254,.6)` |
| `--mu-color-text` | `#f7f3ff` |
| `--mu-color-muted` | `#c5bae8` |
| `--mu-color-dim` | `#9a90c6` |
| `--mu-color-primary` | `#b66eff` |
| `--mu-color-accent` | `#2dd4bf` |
| `--mu-color-success` | `#4ade80` |
| `--mu-color-warning` | `#fbbf24` |
| `--mu-color-danger` | `#fb7185` |
| `--mu-gradient-brand` | `135deg #c084fc → #818cf8 → #2dd4bf` |
| `--mu-glow` | `1` |
| `--mu-canvas-bg` / `-star` / `-edge` | `#070313` / `#ead9ff` / `rgba(182,110,255,.4)` |
| src: wiki / org-memory / meetings / stable / files / graph / metrics | `#c084fc` `#f472b6` `#2dd4bf` `#60a5fa` `#facc15` `#4ade80` `#fb923c` |

Glass surfaces get `backdrop-filter: blur(14px)` via `--mu-surface-blur`; the
page background drifts slowly (36s alternate loop, disabled under
reduced-motion).

### 2.4 Palette — Midnight

| Token | Hex / value |
|---|---|
| `--mu-color-bg` | `#000000` |
| `--mu-color-bg-elevated` | `#05070a` |
| `--mu-color-surface` | `#0a0d11` |
| `--mu-color-surface-strong` | `#11161c` |
| `--mu-color-border` | `rgba(255,255,255,.14)` |
| `--mu-color-border-strong` | `rgba(103,232,249,.42)` |
| `--mu-color-text` | `#e6edf3` |
| `--mu-color-muted` | `#9aa7b1` |
| `--mu-color-dim` | `#71808c` |
| `--mu-color-primary` | `#22d3ee` |
| `--mu-color-accent` | `#67e8f9` |
| `--mu-color-success` | `#34d399` |
| `--mu-color-warning` | `#fbbf24` |
| `--mu-color-danger` | `#f87171` |
| `--mu-gradient-brand` | `135deg #a5f3fc → #22d3ee` |
| `--mu-glow` | `0.35` |
| `--mu-canvas-bg` / `-star` / `-edge` | `#000000` / `#d8e6ee` / `rgba(103,232,249,.22)` |
| src: wiki / org-memory / meetings / stable / files / graph / metrics | `#a78bfa` `#f0abcf` `#5eead4` `#7dd3fc` `#fcd34d` `#6ee7b7` `#fdba74` |

### 2.5 Contrast (enforced)

All of the following pairs pass WCAG AA (≥ 4.5:1 for text) **in all four
themes**, verified programmatically; the worst case in the system is daylight
accent-on-paper at 4.86:1:

- `text|muted|dim` on `bg|bg-elevated|surface|surface-strong`
- `accent|success|warning|danger` on `bg` and `surface`
- `--mu-ink-on-brand` on both gradient endpoints (≥ 5.3:1 everywhere)

Any palette edit MUST re-run a contrast check on those pairs before merge.

---

## 3. Typography

Three faces, three jobs:

| Role | Token | Face | Used for |
|---|---|---|---|
| Display | `--mu-font-display` | **Fraunces** (500, 500 italic, 600 — latin woff2, vendored) | `h1.title`, `h2.section`, brand, `.stat__n`, `.empty .big`, md headings, blockquotes |
| Text | `--mu-font-sans` | system UI stack | everything else |
| Data | `--mu-font-mono` | system mono stack | scores, counts, paths, badges, excerpts, `.cite__meta` |

Fraunces was chosen for its wonky old-style warmth — an astronomer's ledger,
not a startup landing page. Fallback stack is `ui-serif / Iowan Old Style /
Georgia`, so a failed font load still reads in character.

Type scale (1.0rem base, line-height 1.55 body):

| Step | Size | Notes |
|---|---|---|
| `h1.title` | `clamp(1.9rem, 1.4rem + 2.2vw, 2.7rem)` | Fraunces 600, lh 1.12, `text-wrap: balance`; `.grad` span = gradient-clipped italic |
| `h2.section` | `1.22rem` | Fraunces 600 |
| `.lede` | `1.06rem` | max 62ch, muted |
| body | `1rem` | max ~72ch in `.md`, `.answer`, `.cite__text` |
| `.stat__n` | `1.9rem` | Fraunces 600, tabular-nums, gradient-clipped |
| meta (`.item__sub`, `.cite__meta`) | `0.72–0.76rem` | dim, tabular-nums |
| `.kicker` | `0.72rem` | 750 weight, **0.22em tracking**, uppercase, accent |
| `.lbl` | `0.76rem` | uppercase, 0.06em tracking |

Rules:

- Numbers that can change MUST be `tabular-nums` (already applied to `.stat__n`,
  `.pill`, `.item__sub`, `.cite__meta`, `.mono`, md tables, nav status).
- Fraunces is a display face: never set body copy or controls in it.
- Italic Fraunces is reserved for the `.grad` span and md blockquotes.

---

## 4. Space, shape, layout

- Radii: `--mu-radius-sm` 10px (controls, list items), `-md` 14px (cards,
  dialogs), `-lg` 22px (hero canvas frames only).
- Container: `--mu-container` 1180px; shell = 212px sidebar + fluid main.
- Breakpoints: **860px** (sidebar becomes sticky top grid, grids collapse to
  one column) and **480px** (single-column ops form, stacked list items,
  brand tag hidden).
- Borders are 1px hairlines everywhere; emphasis comes from
  `--mu-color-border-strong`, never from thicker borders.
- Elevation ladder: `bg → bg-elevated → surface → surface-strong`. List items
  and code sit on `bg-elevated`; cards on `surface`; selected/featured on
  `surface-strong`.

---

## 5. Motion language

Slow drift and decisive settles. **Nothing bounces, ever** — no overshoot
easings, no scale-pop, no springs.

| Token | Value | Use |
|---|---|---|
| `--mu-dur-quick` | 140ms | hover, focus, color/border transitions |
| `--mu-dur-settle` | 320ms | view entrance (`mu-settle`: 8px rise + fade), toasts |
| `--mu-dur-drift` | 1600ms | constellation node drift base unit |
| `--mu-ease-settle` | `cubic-bezier(0.22, 1, 0.36, 1)` | all settles — fast out, long calm landing |
| `--mu-ease-drift` | `cubic-bezier(0.45, 0, 0.2, 1)` | ambient drift (nebula ground, canvas idle) |

Rules:

- Hover transforms are ≤ 2px translations (`.btn` -1px Y, `.item` +2px X).
  No scaling.
- Ambient loops (nebula ground drift, constellation idle) must be ≥ 30s per
  cycle and alternate — perceptible only when you stop to look.
- `prefers-reduced-motion: reduce` collapses ALL animation/transition to
  0.01ms, kills the nebula drift, and the constellation MUST render static
  (draw once, no rAF loop).

---

## 6. Glow usage rules

`--mu-glow` (0–1) is the theme's glow budget: aurora 0.8, daylight **0**,
nebula 1, midnight 0.35. Canvas code multiplies all bloom/halo radii by it;
CSS does the same (e.g. status dots).

Glow is permitted on exactly four things:

1. **Focused constellation nodes** (`constellation:focus`) — the hero moment.
2. **Verified evidence** — grounded verdicts may glow; heuristic results NEVER
   do. Glow is an evidence signal, not decoration.
3. **The brand mark** and `.btn--primary:hover` / `.card--strong` via
   `--mu-shadow-glow`.
4. **Live status dots** (`.dot--ok` / `.dot--bad`).

Everything else is matte. If a new component "would look cool glowing," the
answer is no.

---

## 7. Source identity

Each knowledge source has one hue, constant across the app: badge, node color,
edge tint, legend. Tokens: `--mu-src-wiki`, `--mu-src-org-memory`,
`--mu-src-meetings`, `--mu-src-stable`, `--mu-src-files`, `--mu-src-graph`,
`--mu-src-metrics`.

Badges (`.src-badge` + `.src--<name>`) are outline-style: tinted text +
45%-tinted hairline border on transparent — color-coded but quiet, so a result
list doesn't turn into confetti. Legacy class aliases (`.src-wiki`,
`.src-org_memory`, `.src-falkordb`→graph, `.src-file_index`→files,
`.src-center_deep`/`.src-postgres`→metrics, `.src-qdrant`→meetings,
`.src-rest`→stable) are kept in base.css.

---

## 8. States & governance surfaces

- **Locked (403)**: quiet `.pill--locked` "requires knowledge-admin" state.
  MUST never be styled like an error and never blamed on the session.
- **Session expired (401)**: `.error` with a reload/sign-in instruction.
- **Empty states**: `.empty` with a Fraunces `.big` line — use real example
  content from the actual corpus ("Try: Majiks Launch Runbook"), never lorem.
- **Heuristic vs verified**: heuristic evidence renders in default `.cite`
  styling with the literal caveat copy; verified/grounded results may use
  `.answer` (primary left rule) and glow per §6.2.
- Async panes carry `aria-live`; `.loading` + `.spinner` for in-flight,
  `.toast` for outcomes — toasts confirm only after success.

---

## 9. Accessibility floor (non-negotiable)

- 44px minimum target on every interactive element (`.btn`, `.btn--sm`,
  `.side__btn`, `.item`, `.cite__more`, `.dialog__close`, footer links, checks).
- `:focus-visible`: 3px accent outline + 2px offset, everywhere; inputs add
  `--mu-focus-ring`.
- Labels on all inputs (`.lbl`); keyboard operability for everything including
  the constellation (focusable nodes / list fallback).
- `color-scheme` is declared per theme so native controls match.
- Contrast per §2.5. Reduced-motion per §5.

---

## 10. Do / Don't

**Do**

- Style views ONLY with tokens + the base vocabulary. New view, zero new colors.
- Test every change in all four themes; daylight is the one that catches
  glow/transparency sins.
- Use real corpus names in examples and empty states.
- Let whitespace and the display face carry personality; keep chrome quiet.
- Keep the caveat copy on heuristic evidence, always.

**Don't**

- Don't hardcode a color, shadow, radius, or font in a view. Ever.
- Don't add glow outside the four permitted uses; don't glow heuristics.
- Don't use emoji as UI iconography.
- Don't animate with bounce/overshoot, or loop anything faster than 30s.
- Don't set body copy in Fraunces, or ship proportional digits in data.
- Don't call a 403 a session problem, or style locked states as errors.
- Don't add external assets of any kind — the host is offline; everything is
  vendored.

---

## 11. File & license notes

- Fraunces v38, latin subset, woff2 (≈59KB total for 3 styles), fetched from
  Google Fonts static hosting and vendored under
  `knowledge_ops/static/ui/fonts/` with `OFL.txt`. SIL Open Font License 1.1.
- `tokens.css` contains a deliberate duplicate of the daylight block inside
  `@media (prefers-color-scheme: light)` for un-themed first visits — edit
  both or first-visit light users drift from themed daylight.
- Internal tokens (`--mu-ink-on-brand`, `--mu-field-bg`, `--mu-page-bg`,
  `--mu-surface-blur`, `--mu-focus-ring`, `--mu-dur-*`, `--mu-ease-*`) are for
  base.css only; views stick to the contract tokens.
