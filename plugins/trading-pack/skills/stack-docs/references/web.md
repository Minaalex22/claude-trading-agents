# Web server, pages and login

- **Server:** `trading/dashboard.py`, standard library only (`http.server.ThreadingHTTPServer` +
  `BaseHTTPRequestHandler`). Routes are plain `if path == "..."` branches in `do_GET` / `do_POST`
  inside `make_handler`. `serve(events_dir, host, port, auth, jarvis)` starts it.
- **Static files:** `trading/web/` (`galaxy.*`, `3d.*`, `command.*`, `app.*`). Add a new asset to the
  `STATIC` map in `dashboard.py` or it won't be served.
- **Pages:** `/`, `/log`, `/3d`, `/jarvis`, `/orderflow`, `/markets`, `/markets/<SYMBOL>`, `/galaxy`,
  `/login`. Health check: `/health`.
- **Read APIs:** `/api/state`, `/api/org`, `/api/prices`, `/api/agents`, `/api/live`,
  `/api/platforms`, `/api/markets`, `/api/orderflow`, `/api/jarvis`, `/api/report` (bearer
  `REPORT_TOKEN`), and the `/events` stream.
- **Write APIs (POST):** `/api/agents`, `/api/live/decide`, `/api/orderflow/trades`,
  `/api/street/note`, `/api/bridge/result`, `/api/jarvis/{unlock,ask,lock,setup}`, `/login`, `/logout`.
  POST bodies are size-capped (`MAX_FORM_BYTES`).
- **Login:** `trading/webauth.py` `WebAuth` (password + optional TOTP two-step, signed cookie,
  failure lockout). Check `self._authed()` in the handler before any private route.
  Outside gate: `auth_gate/` (GitHub/Google sign-in, `TRADING_GATE_ALLOWED`).
- **Deploy:** `render.yaml` (Render Blueprint), `Dockerfile`, `docker-compose.yml`.
- **Tests:** `python -m unittest discover -s tests -t .` (CI runs Linux, Windows, Mac, Python 3.10-3.14,
  so keep code 3.10 compatible).
