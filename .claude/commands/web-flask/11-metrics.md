# Step 11 — Stats / Metrics Dashboard

Replace the placeholder `/stats` pages with a real metrics dashboard.
Requires `sql/metrics.sql` (step 15 of go-migration) to be applied first.

Read first:
- flask-web/blueprints/stats.py
- flask-web/db.py
- flask-web/templates/stats/home.html
- sql/schema.sql + sql/metrics.sql

Create branch `flask/step-11-metrics` from `web-flask`.

---

## Routes (all under /stats, same BasicAuth as admin)

| Route | Description |
|---|---|
| `GET /stats` | Metrics dashboard (default: current month) |
| `GET /stats/users` | (remove — data now on dashboard) |

Query params for `/stats`: `month=YYYY-MM`, `from=YYYY-MM-DD`, `to=YYYY-MM-DD`

---

## DB queries to add to db.py

```python
def stats_matches_by_month(conn, year: int, month: int) -> dict:
    """Total matches, wins/draws/losses breakdown, avg goals for the month."""

def stats_active_users(conn, date_from: str, date_to: str) -> int:
    """COUNT(DISTINCT user_id) FROM user_activity WHERE recorded_at BETWEEN ..."""

def stats_top_teams(conn, year: int, month: int, limit: int = 10) -> list[dict]:
    """Most used team_id_home + team_id_away combined, with match count."""

def stats_top_roster_hashes(conn, year: int, month: int, limit: int = 10) -> list[dict]:
    """Most frequent home_roster_hash + away_roster_hash from matches."""

def stats_matches_per_day(conn, year: int, month: int) -> list[dict]:
    """[{day: 1, count: 12}, ...] for sparkline/bar chart."""

def stats_new_users_by_month(conn, year: int, month: int) -> int:
    """COUNT of users registered in the given month."""
```

---

## Template: templates/stats/home.html

Single page, sections:

1. **Filter bar** — month picker (`<input type="month">`) + date-range pair
   for active users, submit button
2. **Summary cards row** — Matches This Month | Active Users | New Registrations
3. **Matches per day** — simple HTML/CSS bar chart (no JS lib, CSS height %)
4. **Top Teams** — ranked table (team ID + match count; name mapping TBD)
5. **Top Roster Hashes** — ranked table (hash truncated + count)

---

## Tests: tests/test_stats.py

Add test cases for each new DB query function (mock cursor).
Existing auth tests should still pass unchanged.

---

## Verification

```bash
python -m unittest flask-web/tests/test_stats.py
# Visit /stats?month=2026-04 — all sections render without error
# Visit /stats?from=2026-04-01&to=2026-04-07 — active users updates
```

Merge `flask/step-11-metrics` → `web-flask` when done.
