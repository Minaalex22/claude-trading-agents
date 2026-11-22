---
name: frontend-design
description: Use when building or changing any page, dashboard, HUD, or chart for the trading desk. Applies our black and orange galaxy look instead of generic AI-looking UI.
---

# Frontend design: the galaxy HUD look

Our pages look like a mission-control HUD: black space, one hot orange, thin grey lines, mono numbers.
Copy the tokens from `trading/web/galaxy.css` rather than inventing new ones.

## Tokens

```css
:root {
  --bg: #000; --o: #ff5c00; --o2: #ff6b1a; --amber: #ffa040; --w: #fff;
  --g: #8a8a8a; --g2: #555; --line: #1c1c1c;
  --sans: "Space Grotesk", "Inter", ui-sans-serif, system-ui, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
```

## Do

- Black background, white text, **orange only for what matters**: the live value, the selected item,
  the up move, the one button that acts.
- Numbers in `var(--mono)` with `font-variant-numeric: tabular-nums` so columns don't jump.
- Small uppercase mono labels with wide letter-spacing (`10-11px`, `.14em`) above big values.
- 1px `var(--line)` borders to split panels. No heavy cards, no drop shadows except orange glow on
  a live node (`box-shadow: 0 0 12px var(--o)`).
- Layout like the reel: header with metrics on top, ranked list on the left, the visual (galaxy,
  chart, order flow) in the centre, a terminal-style log at the bottom.
- Gains in orange, losses in grey (`.gx-up` / `.gx-down`). No red and green.
- Works at phone width: the side list stacks above the visual below 720px.
- Plain JS and CSS files in `trading/web/`, served by `trading/dashboard.py`. No build step, no
  frameworks unless the page already uses one.

## Don't

- No purple gradients, no rainbow charts, no rounded pastel cards, no emoji icons.
- No second accent colour. If you need a second series, use amber `--amber` or grey.
- No placeholder "Lorem ipsum" or fake metrics: wire the real `/api/...` data or show "no data yet".

## Check your work

Open the page in a browser (Playwright is fine), screenshot it at desktop and phone width, click every
button, and read the console for errors before saying it's done.
