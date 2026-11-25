# JARVIS Trading desktop app

A real app for your Mac or Windows computer. Double-click it and it opens straight onto your trading
pages (News, Trades, Markets, Polymarket, Agents, JARVIS...) in its own window. No browser.

**Download:** https://github.com/Minaalex22/claude-trading-agents/releases/tag/desktop-latest

- Sign in once with your dashboard password; the app keeps you signed in.
- The **Pages** menu (or Cmd/Ctrl + 1-9) jumps between pages. It reopens the last page you used.
- Links to other websites open in your normal browser; your own pages stay in the app.
- **Connect ATAS** (File menu, or the button on One account): one click installs the JarvisFeed add-on into
  ATAS and links it to your trading system. No Terminal. Then add JarvisFeed to a chart in ATAS.
- If the server is asleep, the app waits for it to wake up (up to two minutes) and then opens.

It talks to the live app at https://ats-matrix-6la7.onrender.com. To point it somewhere else, set
`"server"` in the app's `settings.json` (Mac: ~/Library/Application Support/JARVIS Trading/).

Installers are built by `.github/workflows/desktop.yml` on every change to `desktop/` and published on
the Releases page under the tag `desktop-latest`.
