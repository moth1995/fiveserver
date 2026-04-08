# Step 15 — Metrics Schema + Go Instrumentation

Add the DB tables and Go-side instrumentation needed for the Flask metrics
dashboard. No Flask changes in this step — Flask already queries the tables
via `db.py`.

Read first:
- sql/schema.sql                                  (existing tables)
- fiveserver-go/internal/db/match.go              (match recording)
- fiveserver-go/internal/db/pool.go               (StorageController)
- fiveserver-go/internal/protocol/main_service.go (match lifecycle hooks)
- fiveserver-go/internal/server/internal_api.go   (step 14 — internal API)

Create branch `go-server/step-15-metrics-schema` from `go-server`
(after step 14 is merged).

---

## 1. New migration: sql/metrics.sql

```sql
-- Active users snapshot: one row per user per 5-minute tick.
-- Populated directly by the Go server goroutine (no HTTP call needed).
CREATE TABLE IF NOT EXISTS user_activity (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id     INT UNSIGNED    NOT NULL,
    recorded_at TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_user_activity_time (user_id, recorded_at),
    FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- Roster hash seen per match.
-- home_roster_hash and away_roster_hash are the raw hex strings the client
-- sends during team selection (packet that carries kit/roster data).
ALTER TABLE matches
    ADD COLUMN home_roster_hash CHAR(32) DEFAULT NULL,
    ADD COLUMN away_roster_hash CHAR(32) DEFAULT NULL;
```

Run `sql/metrics.sql` against the existing database to apply the changes.

---

## 2. Go: activity snapshot goroutine

In `fiveserver-go/cmd/fiveserver/main.go`, after starting the internal API,
add a goroutine that runs every 5 minutes and INSERTs one row into
`user_activity` for each connected user:

```go
go func() {
    ticker := time.NewTicker(5 * time.Minute)
    defer ticker.Stop()
    for {
        select {
        case <-ctx.Done():
            return
        case <-ticker.C:
            for _, session := range hub.OnlineUsers() {
                if session.User != nil {
                    sc.RecordActivity(ctx, session.User.Id)
                }
            }
        }
    }
}()
```

Add `RecordActivity(ctx context.Context, userID int) error` to
`fiveserver-go/internal/db/pool.go` (or a new `fiveserver-go/internal/db/metrics.go`):

```go
func (sc *StorageController) RecordActivity(ctx context.Context, userID int) error {
    _, err := sc.write.ExecContext(ctx,
        `INSERT INTO user_activity (user_id, recorded_at) VALUES (?, NOW())`, userID)
    return err
}
```

---

## 3. Go: capture roster hashes per match

In `fiveserver-go/internal/protocol/main_service.go`, when recording a
completed match, include the roster hashes that were exchanged during team
selection. Exact packet field TBD — read main_service.go to find where
`team_id_home` / `team_id_away` are set and add `home_roster_hash` /
`away_roster_hash` alongside them.

Update the INSERT in `fiveserver-go/internal/db/match.go`:

```go
// Before:
INSERT INTO matches (profile_id_home, profile_id_away, score_home, score_away, team_id_home, team_id_away)

// After:
INSERT INTO matches (profile_id_home, profile_id_away, score_home, score_away,
                     team_id_home, team_id_away, home_roster_hash, away_roster_hash)
```

---

## Verification

```bash
# Apply migration
mysql fiveserver < sql/metrics.sql

# After starting the server and playing a match:
SELECT * FROM user_activity LIMIT 5;
SELECT home_roster_hash, away_roster_hash FROM matches ORDER BY id DESC LIMIT 5;

go build ./...
go test ./tests/...
```

Merge `go-server/step-15-metrics-schema` → `go-server` when done.
