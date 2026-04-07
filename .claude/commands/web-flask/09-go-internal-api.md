Add the /internal/online-users HTTP endpoint to the Go server.

Read first:
- fiveserver-go/internal/protocol/dispatcher.go   (Hub and Session definitions)
- fiveserver-go/cmd/fiveserver/main.go            (wiring — where to start the internal server)

Create branch `web-flask/step-09-go-internal-api` from `web-flask` before making any changes.

## What to implement

Add a small read-only HTTP server inside the Go binary that Flask's admin panel can query.

### New file: fiveserver-go/internal/server/internal_api.go

```go
package server

// StartInternalAPI starts a localhost-only HTTP server on addr (e.g. "127.0.0.1:8199").
// It exposes GET /internal/online-users → JSON list of connected sessions.
// hub is the shared dispatcher Hub.
func StartInternalAPI(addr string, hub *protocol.Hub) error
```

The endpoint returns:
```json
{
  "users": [
    {"username": "PlayerName", "lobby": "England", "since": "2025-01-01T12:00:00Z"}
  ]
}
```

Data comes from `hub.Sessions` (or equivalent field — read dispatcher.go to find the correct field name for connected sessions). The Hub must be read safely (use RLock if it has a mutex).

The HTTP server:
- Binds to `127.0.0.1:8199` only (not 0.0.0.0)
- Runs in a goroutine (`go http.ListenAndServe(addr, mux)`)
- No authentication (localhost-only is sufficient)
- Returns 200 + JSON always (empty list if no users connected)

### Wire in main.go

In `fiveserver-go/cmd/fiveserver/main.go`, after creating the Hub, add:
```go
go server.StartInternalAPI("127.0.0.1:8199", hub)
```

### No changes to Flask in this step

Flask's `_get_online_users()` in `blueprints/admin.py` already calls `http://127.0.0.1:8199/internal/online-users`. This step makes the Go side ready.

## Go code style

- Fully typed (Go is already strongly typed)
- Error returned from `StartInternalAPI` if `ListenAndServe` fails immediately
- Use `encoding/json` from stdlib
- Do not add new dependencies to go.mod

## Verification

1. `go build ./...` in `fiveserver-go/` — no errors
2. Start the Go server, then: `curl http://127.0.0.1:8199/internal/online-users` → returns `{"users":[]}`
3. `go test ./tests/...` — no regressions

After verification, merge `web-flask/step-09-go-internal-api` → `web-flask`.
