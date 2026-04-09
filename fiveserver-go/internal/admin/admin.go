package admin

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"

	"github.com/fiveserver/fiveserver-go/internal/protocol"
)

// Server exposes an HTTP admin API.
type Server struct {
	hub *protocol.Hub
}

func NewServer(hub *protocol.Hub) *Server {
	return &Server{hub: hub}
}

// ListenAndServe starts the HTTP server on addr (e.g. "0.0.0.0:8180").
// Blocks until the server stops.
func (srv *Server) ListenAndServe(addr string) error {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/chat", srv.handleBroadcast)
	log.Printf("[admin] HTTP server listening on %s", addr)
	return http.ListenAndServe(addr, mux)
}

// broadcastRequest is the JSON body for POST /api/chat.
// lobby is optional; when omitted the message is sent to all lobbies.
type broadcastRequest struct {
	Message string `json:"message"`
	Lobby   string `json:"lobby"` // optional lobby name
}

// handleBroadcast handles POST /api/chat
//
//	{"message": "hello", "lobby": "Russia"}   — one lobby
//	{"message": "hello"}                       — all lobbies
func (srv *Server) handleBroadcast(w http.ResponseWriter, r *http.Request) {
	var req broadcastRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad request: "+err.Error(), http.StatusBadRequest)
		return
	}
	if req.Message == "" {
		http.Error(w, "message is required", http.StatusBadRequest)
		return
	}

	sent := 0
	if req.Lobby != "" {
		for _, l := range srv.hub.Lobbies() {
			if l.Name == req.Lobby {
				protocol.BroadcastSystemChat(srv.hub, l, req.Message)
				sent++
				break
			}
		}
		if sent == 0 {
			http.Error(w, fmt.Sprintf("lobby %q not found", req.Lobby), http.StatusNotFound)
			return
		}
	} else {
		for _, l := range srv.hub.Lobbies() {
			protocol.BroadcastSystemChat(srv.hub, l, req.Message)
			sent++
		}
	}

	log.Printf("[admin] broadcast %q to %d lobby/lobbies", req.Message, sent)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]int{"lobbies": sent})
}
