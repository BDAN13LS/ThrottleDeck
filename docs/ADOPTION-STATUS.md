# Caller adoption status

Only the table between the markers is generated. Run the update command while
your local broker is running; prose outside the markers is preserved.

<!-- BEGIN GENERATED ADOPTION STATUS -->
| Project | Polymarket US | Kalshi | Evidence since broker start |
|---|---:|---:|---|
| **ThrottleDeck Broker** | owns the budget | owns the budget | instance `local-template` |
| **Execution Bot** | ◻ not observed | ◻ not observed | configured; no served traffic yet |
| **Market Research** | ◻ not observed | ◻ not observed | configured; no served traffic yet |
| **Signal Collector** | ◻ not observed | ◻ not observed | configured; no served traffic yet |
<!-- END GENERATED ADOPTION STATUS -->

```powershell
.\.venv\Scripts\python.exe scripts\update-adoption-status.py
```

An observed count proves that the named caller path used the broker during the
current broker process. A zero means only that it was not observed since that
process started; it does not prove a caller went direct, was inactive overall,
or has completed every part of a migration. WebSockets and writes remain outside
the read broker by design.

This repository is public. Treat a generated table as local operational data if
your project names are private; do not commit that personalized snapshot.
