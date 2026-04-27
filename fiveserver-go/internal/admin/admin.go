package admin

import (
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/fiveserver/fiveserver-go/internal/config"
	"github.com/fiveserver/fiveserver-go/internal/logger"
	"github.com/fiveserver/fiveserver-go/internal/protocol"
)

// Server exposes an HTTP admin API.
type Server struct {
	hub        *protocol.Hub
	cfg        *config.Config
	configPath string
}

func NewServer(hub *protocol.Hub, cfg *config.Config, configPath string) *Server {
	return &Server{hub: hub, cfg: cfg, configPath: configPath}
}

// ListenAndServe starts the HTTP server on addr (e.g. "0.0.0.0:8180").
// Blocks until the server stops.
func (srv *Server) ListenAndServe(addr string) error {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /admin/chat", srv.handleBroadcast)
	mux.HandleFunc("POST /admin/kick", srv.handleKick)
	mux.HandleFunc("GET /admin/config", srv.handleGetConfig)
	mux.HandleFunc("POST /admin/reload-config", srv.handleReloadConfig)
	mux.HandleFunc("GET /stats/users", srv.handleUsers)
	logger.Infof("[admin] HTTP server listening on %s", addr)
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

	logger.Infof("[admin] broadcast %q to %d lobby/lobbies", req.Message, sent)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]int{"lobbies": sent})
}

// handleUsers handles GET /api/users — returns all online sessions.
func (srv *Server) handleUsers(w http.ResponseWriter, r *http.Request) {
	type userEntry struct {
		Profile       string `json:"profile"`
		Username      string `json:"username"`
		Lobby         string `json:"lobby"`
		Addr          string `json:"addr"`
		GameVersion   byte   `json:"game_version"`
		OnlineSeconds int    `json:"online_seconds"`
	}

	sessions := srv.hub.AuthenticatedSessions()
	out := make([]userEntry, 0, len(sessions))
	for _, s := range sessions {
		if s.User == nil {
			continue
		}
		profile := ""
		if s.User.Profile != nil {
			profile = s.User.Profile.Name
		}
		lobby := ""
		if l, ok := srv.hub.GetLobby(s.User.LobbyIndex); ok {
			lobby = l.Name
		}
		out = append(out, userEntry{
			Profile:       profile,
			Username:      s.User.User.Username,
			Lobby:         lobby,
			Addr:          s.Conn.RemoteAddr,
			GameVersion:   s.User.GameVersion,
			OnlineSeconds: int(time.Since(s.User.ConnectedAt).Seconds()),
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(out)
}

// handleKick handles POST /api/kick — closes the connection for a named profile.
//
//	{"profile": "PlayerName"}
func (srv *Server) handleKick(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Profile string `json:"profile"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad request: "+err.Error(), http.StatusBadRequest)
		return
	}
	if req.Profile == "" {
		http.Error(w, "profile is required", http.StatusBadRequest)
		return
	}

	s, ok := srv.hub.GetSession(req.Profile)
	if !ok {
		http.Error(w, fmt.Sprintf("profile %q not online", req.Profile), http.StatusNotFound)
		return
	}

	logger.Infof("[admin] kicking %s (addr=%s)", req.Profile, s.Conn.RemoteAddr)
	srv.hub.KickSession(s)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"kicked": req.Profile})
}

// handleGetConfig handles GET /api/config — returns live-reloadable config fields.
func (srv *Server) handleGetConfig(w http.ResponseWriter, r *http.Request) {
	c := srv.cfg
	type configView struct {
		Debug         bool                    `json:"debug"`
		LogLevel      string                  `json:"log_level"`
		LogFile       string                  `json:"log_file"`
		ServerName    string                  `json:"server_name"`
		GreetingText  string                  `json:"greeting_text"`
		MaxUsers      int                     `json:"max_users"`
		Lobbies       []string                `json:"lobbies"`
		BannedList    string                  `json:"banned_list"`
		StoreSettings bool                    `json:"store_settings"`
		ShowStats     bool                    `json:"show_stats"`
		Chat          config.ChatConfig       `json:"chat"`
		Disconnects   config.DisconnectsConfig `json:"disconnects"`
		Roster        config.RosterConfig     `json:"roster"`
	}
	lobbyNames := make([]string, len(c.Lobbies))
	for i, l := range c.Lobbies {
		lobbyNames[i] = l.Name
	}
	view := configView{
		Debug:         c.Debug,
		LogLevel:      c.Log.Level,
		LogFile:       c.Log.File,
		ServerName:    c.ServerName,
		GreetingText:  c.Greeting.Text,
		MaxUsers:      c.MaxUsers,
		Lobbies:       lobbyNames,
		BannedList:    c.BannedList,
		StoreSettings: c.StoreSettings,
		ShowStats:     c.ShowStats,
		Chat:          c.Chat,
		Disconnects:   c.Disconnects,
		Roster:        c.Roster,
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(view)
}

// handleReloadConfig handles POST /api/reload-config — re-reads the YAML file
// and applies live-reloadable fields without restarting the server.
func (srv *Server) handleReloadConfig(w http.ResponseWriter, r *http.Request) {
	changes, err := srv.cfg.Reload(srv.configPath)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	logger.SetLevel(srv.cfg.Log.Level)
	logger.Infof("[admin] config reloaded: %d change(s)", len(changes))
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"changes": changes,
		"count":   len(changes),
	})
}
