# Knowledge-Ops brand kit — "The Observatory"

Knowledge-Ops is Magic Unicorn's governed organizational memory. The brand is
an **observatory**: you don't operate a dashboard, you watch your organization
think. Everything visual follows from that one idea.

## The mark — the Stargazer

`knowledge_ops/static/brand/ko-mark.svg`

A unicorn head drawn the way the product draws knowledge: stars joined by
hairlines. Wedge horn to the brightest star, upright ear, long muzzle, three
mane streaks, and a **brass eye** — the observer. It sits in the product's
top bar and on the landing page, and it scales from hero art down to ~24px.

- Fits the Magic Unicorn family: every MU product carries a unicorn
  (Wealth-Ops has the Steward; Knowledge-Ops has the **Stargazer**).
- Favicon is the reduced form: brand-gradient disc + horn wedge + eye dot —
  legible at 16px where the constellation is not.
- Don't recolor the eye. Don't fill the head. Don't add an outline path — the
  mark is points and lines by design.

## Color

The product ships five "skies"; **Aurora** is the brand ground.

| Token | Aurora value | Role |
| --- | --- | --- |
| bg | `#070512` | deep-space ground |
| text | `#ECE9F8` | starlight |
| primary | `#A78BFA` | violet — brand, focus, primary actions |
| accent | `#4ED3C2` | teal — links, success-adjacent |
| brass | `#D9A96C` | instrument labels, kickers, citation numbers, the eye |
| gradient | `135° #A78BFA → #4ED3C2` | the MU brand gradient (shared suite-wide) |

Brass is the observatory's signature: mono, letterspaced, uppercase labels
(`.kicker`). Semantic colors (success/warning/danger) never substitute for the
accent. The other skies (Daylight = ink chart room, Porcelain = cool gallery,
Nebula = demo reel, Midnight = OLED quiet) re-derive every token; nothing is
hardcoded.

## Type

- **Fraunces** (vendored OFL, 500/500i/600) — display: headlines, view titles,
  stat numerals. Tight leading, slight negative tracking, `text-wrap: balance`.
- **System sans** — UI text.
- **Mono** (system) — instrument readouts, paths, counts, kickers; always
  `tabular-nums`.

## Voice

Confident, concrete, honest. The product never overclaims: retrieval is
"related evidence — heuristic, not verification," and every answer cites its
sources. Banned: "revolutionary," "AI-powered," breathless hype. The poetry is
earned and quiet: *points of light · 9 sources*.

## Motion

Springs (`--mu-ease-spring`), not ease-in-out everywhere. The sky is always
alive (auto-orbit on the hero, motes along edges); everything respects
`prefers-reduced-motion`.

## Usage

- Product shell: mark + wordmark "Knowledge-Ops" + brass tag OBSERVATORY.
- Landing page: inline copies of the mark + favicon (the page must stay
  self-contained; `/ui/*` and `/brand/*` are auth-gated).
- Agent identity: the Guide chat is "powered by Unicorn Brigade" and uses
  Brigade's own agent avatars where provided (`brandops:` refs).
