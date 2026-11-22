# Agents, brains and departments

- **Departments:** `trading/org.py` (`Department`, `Manager`, `department_sizes`, `summarize`,
  `manager_reports`). Four departments: Trend, Reversion, Research, Risk. Managers: Atlas (trading),
  Sage (research), Sentinel (risk). `AGENT_COUNT` splits evenly.
- **Agents:** `trading/agents.py`. Each agent has a strategy, a symbol and a look-back window.
- **Brains:** `trading/brain.py` `AgentBrain` — `learn(reward)`, `shape(signal, neutral, low, high)`
  (returns `None` when sitting out), `skill`, `sitting_out`, `snapshot()`. Optional Claude "minds"
  in `trading/mind.py` / `trading/brains.py` (off unless `MINDS` is set).
- **Training:** `trading/academy.py` (courses and exams), `trading/masters.py` (great traders'
  rules), `trading/methods.py` (book-author methods), `trading/coach.py`.
- **Engine loop:** `trading/engine.py` runs ticks, `trading/desk.py` is the paper desk,
  `trading/events.py` writes the event log the pages read.
- **Entry point:** `run_agent.py`.
