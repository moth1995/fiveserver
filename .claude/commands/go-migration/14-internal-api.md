# Step 14 — Go Internal API (Flask bridge endpoints)

Add a localhost-only HTTP server inside the Go binary that exposes the
endpoints the Flask admin/stats layer needs to show live data.

Read first:
- fiveserver-go/internal/protocol/dispatcher.go  (Hub, Session, MatchState)
- fiveserver-go/cmd/fiveserver/main.go           (wiring)
- fiveserver-go/internal/server/tcp.go           (server package reference)

Create branch `go-server/step-14-internal-api` from `go-server`.

---

## New file: fiveserver-go/internal/server/internal_api.go

```go
package server

// StartInternalAPI starts a localhost-only HTTP server.
// addr defaults to "127.0.0.1:8199" but is configurable via GO_API_PORT.
// hub is the shared dispatcher Hub.
func StartInternalAPI(addr string, hub *protocol.Hub) error
```

### Endpoints

#### GET /online-users
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

#### GET /lobby-stats
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

## Wire in main.go

After `hub := protocol.NewHub(cfg)`:

```go
apiAddr := fmt.Sprintf("127.0.0.1:%s", getEnvOrDefault("GO_API_PORT", "8199"))
go server.StartInternalAPI(apiAddr, hub)
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
curl http://127.0.0.1:8199/online-users   # → {"users":[]}
curl http://127.0.0.1:8199/lobby-stats    # → {"lobbies":[...]}
go test ./tests/...
```

Merge `go-server/step-14-internal-api` → `go-server` when done.
