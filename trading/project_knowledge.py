"""Project knowledge for JARVIS and Mini-JARVIS: everything about the project, from the files in trading/knowledge/.

The folder holds the whole-system and agents blueprints, the four brain-code guides, Claude's 11 functions and the
day-trader skills blueprints, the every-request checklist and `00-latest-status.md` (newest results, unlock rules,
parked items). To teach JARVIS something new, add or edit a Markdown file there; no code change is needed.

- `text()`: all of it as one block (about 20k tokens). The Claude brains send it as a prompt-cached system block, so
  after the first question it is billed at the cached rate.
- `search(question)`: the few best-matching sections, for small local models and for the built-in brain.
- `quick_answer(question)`: short spoken answers to the questions mina asks most (unlock, results, parked, bridges).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

FOLDER = Path(__file__).with_name("knowledge")
STOP = set("a an and are as at be by can do does for from has have how i in is it its me my of on or our so that the "
           "this to was we what when where which who why will with you your about tell show give".split())


@lru_cache(maxsize=1)
def files() -> tuple[tuple[str, str], ...]:
    out = []
    for path in sorted(FOLDER.glob("*.md")):
        text = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("!["))
        out.append((path.stem, text.strip()))
    return tuple(out)


@lru_cache(maxsize=1)
def text() -> str:
    return "\n\n".join(f"===== {name} =====\n{body}" for name, body in files())


@lru_cache(maxsize=1)
def sections() -> tuple[tuple[str, str], ...]:
    """Each file split at its headings: (title, body)."""
    out = []
    for name, body in files():
        title, buf, fence = name, [], False
        for line in body.splitlines():
            if line.startswith("```"):
                fence = not fence
            if fence or line.startswith("```"):
                buf.append(line)
                continue
            if line.startswith("#") and buf:
                out.append((title, "\n".join(buf).strip()))
                buf = []
            if line.startswith("#"):
                title = f"{name}: {line.lstrip('#').strip()}"
            buf.append(line)
        if buf:
            out.append((title, "\n".join(buf).strip()))
    return tuple(out)


def _words(s: str) -> set[str]:
    words = (w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP and len(w) > 1)
    return {w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words}


@lru_cache(maxsize=1)
def _idf() -> dict[str, float]:
    """Rare words count more than common ones (inverse document frequency over the sections)."""
    import math
    docs = [_words(t + " " + b) for t, b in sections()]
    df: dict[str, int] = {}
    for d in docs:
        for w in d:
            df[w] = df.get(w, 0) + 1
    return {w: math.log(1 + len(docs) / n) for w, n in df.items()}


def search(question: str, k: int = 3, max_chars: int = 6000) -> list[tuple[str, str]]:
    """The k best sections for a question, by matched words weighted by rarity (title matches count double)."""
    q = _words(question)
    if not q:
        return []
    idf = _idf()
    scored = []
    for title, body in sections():
        score = (sum(idf.get(w, 0) for w in q & _words(body))
                 + 2 * sum(idf.get(w, 0) for w in q & _words(title)))
        if score:
            scored.append((score, title, body))
    scored.sort(key=lambda x: -x[0])
    out, used = [], 0
    for score, title, body in scored[:k]:
        body = body[: max(0, max_chars - used)]
        if body:
            out.append((title, body))
            used += len(body)
    return out


def search_text(question: str, k: int = 3, max_chars: int = 6000) -> str:
    return "\n\n".join(f"[{t}]\n{b}" for t, b in search(question, k, max_chars))


QUICK = [
    (r"\b(unlock|go live|going live|ready (for|to) (go )?(real|live)|real money (yet|ready)|when can we trade real)",
     "Real money stays locked until an Orbit arm passes three gates on real-price paper trades: at least 50 chosen "
     "trades with 60 percent or more wins, profit factor 1.35 or more and drawdown 8 percent or less; sim and real win "
     "rates within 8 points; and your sign-off. Then the Demo/Live switch, live keys on Render and typing GO LIVE."),
    (r"\b(results?|profit factor|backtest|replay|how (are|is) (we|the desk) doing on real|losing arm|loser|"
     r"which arm)",
     "On real daily prices 2020 to 2026 after costs, the desk now has profit factor 2.58 on the 2020 to 2022 years it was "
     "tuned on and 1.72 on unseen 2023 to 2026, ending up 18.8 thousand dollars. Forex majors are benched; USDJPY is the "
     "last losing arm. Real money stays locked until the live gates pass on real-price paper trades."),
    (r"\b(parked|needs? me|waiting on me|what do i (need|have) to do|my (to ?do|jobs?)|left for me)",
     "Five things wait for you: run the ATAS one-liner on your Mac once; add your MetaTrader 5 account in MetaApi; add "
     "live keys on Render when you want to go live; upload the LangChain guide PDF; and a paid data feed for 0DTE whale "
     "flow."),
    (r"\b(bridges|bridge status|which platforms|all (the )?platforms)",
     "Bridges: the paper desk runs 24/7 on live prices. MetaTrader 5 via MetaApi is ready for demo once you add the "
     "account. eToro and Betfair need keys on Render and stay locked until the gate. Polymarket is read-only with paper "
     "bets, as it isn't legal in Australia. Mitrade gives live prices and a practice desk, BTCC is practice only since "
     "its API closed, moomoo is practice, and ATAS sends data only."),
    (r"\b(what|which|list|all( the)?) (pages|screens)\b",
     "About 36 pages, all behind the login: News first, Pipeline and News Lab, Trades, Wallet, One account, Markets with "
     "a page per market and per product, the 24/7 session desks, Orbit, Polymarket, Mitrade, BTCC, the Agents floor, "
     "Galaxy, 3D, Hunter vs Judge, Decision, Smart money, Order flow, Pairs, Calculator, Time Machine, Audit, Brief and "
     "close, the event log, and me on /jarvis."),
    (r"\b(sleeves?|orbit arms?|five arms|5 arms)\b",
     "Orbit has five arms: crypto 30 percent, US shares 30 percent, gold 20 percent, USDJPY 20 percent and forex majors "
     "0 percent, because they are benched. Each arm's size also moves with its heat."),
    (r"\b(what (is|does) (this|the) (project|system|app)|explain (the )?(whole )?(project|system)|big picture)",
     "ATS Matrix is one web app running a desk of 1,400 AI agents on live prices with a paper broker. Agents read charts "
     "and news and vote; Sage names the regime, Sentinel sizes the trade and Atlas picks the department. Every order "
     "passes a nine-stage check and the guards, is booked into the Orbit portfolio and is written to the audit log. Real "
     "money stays locked."),
]


def quick_answer(question: str) -> str | None:
    q = question.lower()
    for pattern, answer in QUICK:
        if re.search(pattern, q):
            return answer
    return None


def spoken(question: str) -> str | None:
    """A short answer read from the best matching section, for the built-in brain."""
    hits = search(question, k=1)
    if not hits:
        return None
    title, body = hits[0]
    q = _words(question)
    if len(q & (_words(body) | _words(title))) < min(2, len(q)):
        return None
    lines = [re.sub(r"[*`#|]", "", line).strip() for line in body.splitlines()]
    lines = [line for line in lines if line and not set(line) <= set("-: ")]
    text_ = " ".join(lines[1:] if len(lines) > 1 else lines)
    text_ = re.sub(r"\s+", " ", text_)
    if len(text_) > 600:
        text_ = text_[:600].rsplit(" ", 1)[0] + "..."
    return f"From {title.split(': ', 1)[-1]}: {text_}"
