package protocol

import (
	"log"
	"sync"
	"time"

	"github.com/fiveserver/fiveserver-go/internal/config"
	"github.com/fiveserver/fiveserver-go/internal/model"
)

// ---- Handler registry -------------------------------------------------------

// ConnSender is a thin shim that gives protocol handlers access to the
// underlying TCP connection without importing the server package (which would
// create an import cycle: protocol → server → protocol).
// The server layer constructs one of these per accepted connection.
type ConnSender struct {
	SendDataFn  func(id uint16, data []byte) error
	SendZerosFn func(id uint16, length int) error
	SendFn      func(pkt Packet) error
	RemoteAddr  string
}

func (c *ConnSender) SendData(id uint16, data []byte) error { return c.SendDataFn(id, data) }
func (c *ConnSender) SendZeros(id uint16, length int) error { return c.SendZerosFn(id, length) }
func (c *ConnSender) Send(pkt Packet) error                 { return c.SendFn(pkt) }

// HandlerFunc handles one packet on a connection.
type HandlerFunc func(s *Session, pkt Packet) error

// Dispatcher maps packet IDs to HandlerFuncs.
// Mirrors Python PacketDispatcher: unknown packets are silently dropped
// (defaultHandler does nothing).
type Dispatcher struct {
	handlers map[uint16]HandlerFunc
}

func NewDispatcher() *Dispatcher {
	return &Dispatcher{handlers: make(map[uint16]HandlerFunc)}
}

// Register binds id to h, overwriting any prior binding.
func (d *Dispatcher) Register(id uint16, h HandlerFunc) {
	d.handlers[id] = h
}

// Dispatch calls the handler for pkt.Header.ID. Unknown IDs are logged and dropped.
func (d *Dispatcher) Dispatch(s *Session, pkt Packet) error {
	h, ok := d.handlers[pkt.Header.ID]
	if !ok {
		log.Printf("[dispatch] %s: no handler for pkt 0x%04x — dropping", s.Conn.RemoteAddr, pkt.Header.ID)
		return nil
	}
	return h(s, pkt)
}

// ---- Session ----------------------------------------------------------------

// Session holds per-connection state for one TCP client.
// Created by the server layer on each accepted connection; destroyed when the
// connection closes.
type Session struct {
	Conn        *ConnSender
	User        *model.ConnectedUser // nil until 0x3003 authenticate succeeds
	Dispatcher  *Dispatcher
	Hub         *Hub
	GameVersion string // "pes5", "we9", "we9le"
}

// ---- Hub --------------------------------------------------------------------

// MatchState tracks the two sessions in an active match.
// Keyed in Hub by the "home" session pointer.
type MatchState struct {
	Home    *Session
	Away    *Session
	Started time.Time
}

// Hub holds server-wide shared state: online sessions, configuration, and lobbies.
// All exported methods are thread-safe.
type Hub struct {
	mu      sync.RWMutex
	users   map[string]*Session   // key: profile name (set after profile select)
	matches map[*Session]*MatchState // key: home session
	cfg     *config.Config
	lobbies []*model.Lobby // live lobby instances initialised from cfg.Lobbies
}

func NewHub(cfg *config.Config) *Hub {
	h := &Hub{
		users:   make(map[string]*Session),
		matches: make(map[*Session]*MatchState),
		cfg:     cfg,
	}
	for i, lc := range cfg.Lobbies {
		l := model.NewLobby(lc.Name, cfg.MaxUsers)
		l.Index = i
		l.TypeCode = lc.TypeCode
		l.TypeStr = lc.Type
		l.ShowMatches = lc.ShowMatches
		l.CheckRosterHash = lc.CheckRosterHash
		h.lobbies = append(h.lobbies, l)
	}
	return h
}

// Lobbies returns the live lobby slice (read-only after startup).
func (h *Hub) Lobbies() []*model.Lobby { return h.lobbies }

// GetLobby returns the lobby at the given index, or false if out of range.
func (h *Hub) GetLobby(index int) (*model.Lobby, bool) {
	if index < 0 || index >= len(h.lobbies) {
		return nil, false
	}
	return h.lobbies[index], true
}

// PendingMatch returns the active MatchState for a session (either as home or away).
func (h *Hub) PendingMatch(s *Session) (*MatchState, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	if ms, ok := h.matches[s]; ok {
		return ms, true
	}
	// Also check if this session is the away side
	for _, ms := range h.matches {
		if ms.Away == s {
			return ms, true
		}
	}
	return nil, false
}

// SetPendingMatch registers a MatchState keyed by the home session.
func (h *Hub) SetPendingMatch(ms *MatchState) {
	h.mu.Lock()
	h.matches[ms.Home] = ms
	h.mu.Unlock()
}

// ClearPendingMatch removes the active match for the given session.
func (h *Hub) ClearPendingMatch(s *Session) {
	h.mu.Lock()
	// Remove whether s is home or away
	if _, ok := h.matches[s]; ok {
		delete(h.matches, s)
	} else {
		for k, ms := range h.matches {
			if ms.Away == s {
				delete(h.matches, k)
				break
			}
		}
	}
	h.mu.Unlock()
}

// Config returns the server configuration (read-only after startup).
func (h *Hub) Config() *config.Config { return h.cfg }

// AddSession registers a session by its selected profile name.
// Call after the user selects a profile (0x3040).
func (h *Hub) AddSession(s *Session) {
	if s.User == nil || s.User.Profile == nil {
		return
	}
	h.mu.Lock()
	h.users[s.User.Profile.Name] = s
	h.mu.Unlock()
}

// RemoveSession removes a session from the online map.
func (h *Hub) RemoveSession(s *Session) {
	if s.User == nil || s.User.Profile == nil {
		return
	}
	h.mu.Lock()
	delete(h.users, s.User.Profile.Name)
	h.mu.Unlock()
}

// GetSession looks up an online session by profile name.
func (h *Hub) GetSession(profileName string) (*Session, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	s, ok := h.users[profileName]
	return s, ok
}

// OnlineCount returns the number of currently authenticated sessions.
func (h *Hub) OnlineCount() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.users)
}

// AtCapacity returns true when MaxUsers has been reached.
// Mirrors Python FiveServerConfig.atCapacity().
func (h *Hub) AtCapacity() bool {
	return h.OnlineCount() >= h.cfg.MaxUsers
}

// Sessions returns a snapshot of all online sessions. Safe to iterate without
// holding the lock.
func (h *Hub) Sessions() []*Session {
	h.mu.RLock()
	defer h.mu.RUnlock()
	out := make([]*Session, 0, len(h.users))
	for _, s := range h.users {
		out = append(out, s)
	}
	return out
}
