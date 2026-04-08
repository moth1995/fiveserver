# Step 14 — Go Internal API (Flask bridge endpoints)

Add a localhost-only HTTP server inside the Go binary that exposes all
endpoints the Flask admin/stats layer needs.

Read first:
- fiveserver-go/internal/protocol/dispatcher.go  (Hub, Session, MatchState)
- fiveserver-go/cmd/fiveserver/main.go           (wiring)
- fiveserver-go/internal/server/tcp.go           (server package reference)

Create branch `go-server/step-14-internal-api` from `go-server`.

---

## New file: fiveserver-go/internal/server/internal_api.go

```go
package server

// StartInternalAPI starts a localhost-only HTTP server on addr ("127.0.0.1:8199").
// All endpoints are read-only. hub is the shared dispatcher Hub.
func StartInternalAPI(addr string, hub *protocol.Hub) error
```

### Endpoints

#### GET /internal/online-users
Returns all currently connected sessions.

```json
{
  "users": [
    {"username": "PlayerA", "lobby": "Russia", "since": "2026-01-01T12:00:00Z"}
  ]
}
```

Data source: `hub.users` map (read with RLock). `since` = session connect time
(add `ConnectedAt time.Time` field to `protocol.Session` if not present).

---

#### GET /internal/lobby-stats
Returns per-lobby player counts and active matches (fiveserver/PES5 only —
no Match6/sixserver logic).

```json
{
  "lobbies": [
    {
      "name": "Russia",
      "player_count": 4,
      "matches": [
        {
          "room_name": "Room 1",
          "match_time": 62,
          "score": "2:1",
          "home_team_id": 5,
          "away_team_id": 12,
          "home_profile": "PlayerA",
          "away_profile": "PlayerB"
        }
      ]
    }
  ]
}
```

Data source: `hub.lobbies` slice. For each lobby, iterate `lobby.Players`
(or equivalent) and `hub.matches` for in-progress match state.
`match_time` is seconds elapsed since `MatchState.Started`.

---

#### GET /internal/activity-ping
Called by the Flask activity recorder (or a future cron job) every 5 minutes
to snapshot currently online user IDs. Returns the same shape as
`/internal/online-users` but includes the internal user DB `id`.

```json
{
  "users": [
    {"user_id": 42, "username": "PlayerA", "lobby": "Russia"}
  ]
}
```

Data source: `hub.users` map → `session.User.Id` (already stored on login).

---

## Wire in main.go

After `hub := protocol.NewHub(cfg)`:

```go
go server.StartInternalAPI("127.0.0.1:8199", hub)
```

---

## Go code style

- Stdlib only (`net/http`, `encoding/json`) — no new go.mod deps
- Bind strictly to `127.0.0.1` (not `0.0.0.0`)
- Always return HTTP 200 + JSON (empty lists on no data, never 500)
- All hub reads under `hub.mu.RLock()` / `hub.mu.RUnlock()`

## Verification

```bash
go build ./...
curl http://127.0.0.1:8199/internal/online-users   # → {"users":[]}
curl http://127.0.0.1:8199/internal/lobby-stats    # → {"lobbies":[...]}
curl http://127.0.0.1:8199/internal/activity-ping  # → {"users":[]}
go test ./tests/...
```

Merge `go-server/step-14-internal-api` → `go-server` when done.
