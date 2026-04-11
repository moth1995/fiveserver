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
	CloseFn     func()
	RemoteAddr  string
}

func (c *ConnSender) SendData(id uint16, data []byte) error { return c.SendDataFn(id, data) }
func (c *ConnSender) SendZeros(id uint16, length int) error { return c.SendZerosFn(id, length) }
func (c *ConnSender) Send(pkt Packet) error                 { return c.SendFn(pkt) }
func (c *ConnSender) Close()                                { c.CloseFn() }

// HandlerFunc handles one packet on a connection.
type HandlerFunc func(s *Session, pkt Packet) error

// Dispatcher maps packet IDs to HandlerFuncs.
// Mirrors Python PacketDispatcher: unknown packets call DefaultHandler if set,
// otherwise they are logged and dropped.
type Dispatcher struct {
	handlers       map[uint16]HandlerFunc
	DefaultHandler HandlerFunc // called for any unregistered packet ID (may be nil)
}

func NewDispatcher() *Dispatcher {
	return &Dispatcher{handlers: make(map[uint16]HandlerFunc)}
}

// Register binds id to h, overwriting any prior binding.
func (d *Dispatcher) Register(id uint16, h HandlerFunc) {
	d.handlers[id] = h
}

// Dispatch calls the handler for pkt.Header.ID.
// If no handler is registered, DefaultHandler is called if set; otherwise the
// packet is logged and dropped. Mirrors Python PacketDispatcher.defaultHandler.
func (d *Dispatcher) Dispatch(s *Session, pkt Packet) error {
	h, ok := d.handlers[pkt.Header.ID]
	if !ok {
		if d.DefaultHandler != nil {
			return d.DefaultHandler(s, pkt)
		}
		log.Printf("[dispatch] %s: no handler for pkt 0x%04x — dropping", s.Conn.RemoteAddr, pkt.Header.ID)
		return nil
	}
	return h(s, pkt)
}

// echoDefaultHandler returns a HandlerFunc that mirrors Python PacketDispatcher.defaultHandler:
// responds with (pktID + 1) and 4 zero bytes for any unregistered packet.
func echoDefaultHandler() HandlerFunc {
	return func(s *Session, pkt Packet) error {
		log.Printf("[dispatch] %s: default echo for pkt 0x%04x", s.Conn.RemoteAddr, pkt.Header.ID)
		return s.Conn.SendData(pkt.Header.ID+1, []byte{0, 0, 0, 0})
	}
}

// ---- Session ----------------------------------------------------------------

// Session holds per-connection state for one TCP client.
// Created by the server layer on each accepted connection; destroyed when the
// connection closes.
type Session struct {
	Conn       *ConnSender
	User       *model.ConnectedUser // nil until 0x3003 authenticate succeeds
	Dispatcher *Dispatcher
	Hub        *Hub
	OnClose    func() // called by server layer when the connection closes (may be nil)
}

// ---- Hub --------------------------------------------------------------------

// MatchState tracks the two sessions in an active match.
// Keyed in Hub by the "home" session pointer.
type MatchState struct {
	Home    *Session
	Away    *Session
	Started time.Time
}

// offlineGracePeriod is the delay after a disconnect before a user is considered
// truly offline. Allows port-transition reconnects without resetting online time.
const offlineGracePeriod = 5 * time.Second

// Hub holds server-wide shared state: online sessions, configuration, and lobbies.
// All exported methods are thread-safe.
//
// Two separate maps mirror the Python architecture:
//
//	byHash    — keyed by user.Hash, populated at 0x3003 authentication.
//	            Used for the "already online" check (matches Python onlineUsers).
//	byProfile — keyed by profile.Name, populated at 0x4100 profile selection.
//	            Used for message routing / sendToUser (matches Python lobbyConnection).
//
// onlineSince tracks the original connect time per user hash across port
// transitions. offlineTimers debounces UserOffline so brief reconnects
// (e.g. news→login→menu→main) don't reset the online clock.
type Hub struct {
	mu            sync.RWMutex
	byHash        map[string]*Session      // key: user.Hash  (set at login)
	byProfile     map[string]*Session      // key: profile.Name (set at profile select)
	matches       map[*Session]*MatchState // key: home session
	cfg           *config.Config
	lobbies       []*model.Lobby         // live lobby instances initialised from cfg.Lobbies
	onlineSince   map[string]time.Time   // hash → original connect time (persists across port transitions)
	offlineTimers map[string]*time.Timer // hash → pending offline-cleanup timer
}

func NewHub(cfg *config.Config) *Hub {
	h := &Hub{
		byHash:        make(map[string]*Session),
		byProfile:     make(map[string]*Session),
		matches:       make(map[*Session]*MatchState),
		cfg:           cfg,
		onlineSince:   make(map[string]time.Time),
		offlineTimers: make(map[string]*time.Timer),
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

// ---- online tracking (byHash) -----------------------------------------------

// UserOnline marks a user as online after successful authentication (0x3003).
// Cancels any pending offline timer for this hash (port transition reconnect).
// Preserves the original ConnectedAt from onlineSince so the online clock
// never resets across port transitions.
// Mirrors Python FiveServerFactory.userOnline(usr).
func (h *Hub) UserOnline(s *Session) {
	if s.User == nil {
		return
	}
	hash := s.User.User.Hash
	h.mu.Lock()
	// Cancel any pending offline cleanup — user reconnected within grace period.
	if t, ok := h.offlineTimers[hash]; ok {
		t.Stop()
		delete(h.offlineTimers, hash)
	}
	// Preserve original connect time across port transitions.
	if since, ok := h.onlineSince[hash]; ok {
		s.User.ConnectedAt = since
	} else {
		h.onlineSince[hash] = s.User.ConnectedAt
	}
	h.byHash[hash] = s
	h.mu.Unlock()
	log.Printf("[hub] UserOnline {id=%d} addr=%s", s.User.User.ID, s.Conn.RemoteAddr)
}

// UserOffline removes a user from byHash and byProfile, then starts a
// grace-period timer. If the user reconnects (port transition) before the
// timer fires, UserOnline will cancel it and onlineSince is kept intact.
// Only after offlineGracePeriod with no reconnect is onlineSince cleaned up.
// Mirrors Python FiveServerFactory.userOffline(usr).
func (h *Hub) UserOffline(s *Session) {
	if s.User == nil {
		return
	}
	hash := s.User.User.Hash
	name := ""
	if s.User.Profile != nil {
		name = s.User.Profile.Name
	}
	h.mu.Lock()
	delete(h.byHash, hash)
	if s.User.Profile != nil {
		if current, ok := h.byProfile[name]; ok && current == s {
			delete(h.byProfile, name)
		}
	}
	// Cancel any previous offline timer before starting a new one.
	if t, ok := h.offlineTimers[hash]; ok {
		t.Stop()
	}
	// After the grace period, clean up onlineSince if the user is still absent.
	h.offlineTimers[hash] = time.AfterFunc(offlineGracePeriod, func() {
		h.mu.Lock()
		defer h.mu.Unlock()
		if _, online := h.byHash[hash]; !online {
			delete(h.onlineSince, hash)
			log.Printf("[hub] user hash=%s confirmed offline (grace period elapsed)", hash)
		}
		delete(h.offlineTimers, hash)
	})
	h.mu.Unlock()
	log.Printf("[hub] UserOffline {%s} addr=%s", name, s.Conn.RemoteAddr)
}

// IsUserOnline checks whether a user is currently authenticated.
// O(1) hash lookup — mirrors Python FiveServerFactory.isUserOnline(usr).
func (h *Hub) IsUserOnline(u *model.User) bool {
	h.mu.RLock()
	defer h.mu.RUnlock()
	_, ok := h.byHash[u.Hash]
	return ok
}

// ---- profile routing (byProfile) --------------------------------------------

// AddSession registers a session by its selected profile name for message routing.
// Call after the user selects a profile (0x4100).
func (h *Hub) AddSession(s *Session) {
	if s.User == nil || s.User.Profile == nil {
		return
	}
	h.mu.Lock()
	h.byProfile[s.User.Profile.Name] = s
	h.mu.Unlock()
	log.Printf("[hub] AddSession {%s} addr=%s", s.User.Profile.Name, s.Conn.RemoteAddr)
}

// RemoveSession removes a session from the profile routing map.
// Only removes if the stored pointer matches s (prevents a stale disconnect
// on one port from evicting a newer session registered on another port).
func (h *Hub) RemoveSession(s *Session) {
	if s.User == nil || s.User.Profile == nil {
		return
	}
	h.mu.Lock()
	current, exists := h.byProfile[s.User.Profile.Name]
	if exists && current == s {
		delete(h.byProfile, s.User.Profile.Name)
		h.mu.Unlock()
		log.Printf("[hub] RemoveSession {%s} addr=%s", s.User.Profile.Name, s.Conn.RemoteAddr)
	} else {
		h.mu.Unlock()
		if exists {
			log.Printf("[hub] RemoveSession {%s} addr=%s — SKIPPED (hub points to different session addr=%s)", s.User.Profile.Name, s.Conn.RemoteAddr, current.Conn.RemoteAddr)
		}
	}
}

// GetSession looks up a session by profile name (for message routing).
func (h *Hub) GetSession(profileName string) (*Session, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	s, ok := h.byProfile[profileName]
	return s, ok
}

// Sessions returns a snapshot of all sessions that have selected a profile.
// Safe to iterate without holding the lock.
func (h *Hub) Sessions() []*Session {
	h.mu.RLock()
	defer h.mu.RUnlock()
	out := make([]*Session, 0, len(h.byProfile))
	for _, s := range h.byProfile {
		out = append(out, s)
	}
	return out
}

// AuthenticatedSessions returns a snapshot of all sessions that have
// completed authentication (0x3003), including those who haven't yet
// selected a profile. Mirrors Python onlineUsers.
func (h *Hub) AuthenticatedSessions() []*Session {
	h.mu.RLock()
	defer h.mu.RUnlock()
	out := make([]*Session, 0, len(h.byHash))
	for _, s := range h.byHash {
		out = append(out, s)
	}
	return out
}

// OnlineCount returns the number of authenticated users (by hash).
// Mirrors Python len(self.onlineUsers).
func (h *Hub) OnlineCount() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.byHash)
}

// AtCapacity returns true when MaxUsers has been reached.
// Mirrors Python FiveServerConfig.atCapacity().
func (h *Hub) AtCapacity() bool {
	return h.OnlineCount() >= h.cfg.MaxUsers
}

// KickSession removes a user from all maps immediately and closes the TCP
// connection. Clears onlineSince and any pending offline timer right away
// since a kick is an authoritative logout, not a port transition.
// Removing from the hub first ensures the "already online" guard clears
// even if the OnClose hook fires after a reconnect attempt.
func (h *Hub) KickSession(s *Session) {
	if s.User == nil {
		s.Conn.Close()
		return
	}
	hash := s.User.User.Hash
	h.mu.Lock()
	delete(h.byHash, hash)
	if s.User.Profile != nil {
		if current, ok := h.byProfile[s.User.Profile.Name]; ok && current == s {
			delete(h.byProfile, s.User.Profile.Name)
		}
	}
	if t, ok := h.offlineTimers[hash]; ok {
		t.Stop()
		delete(h.offlineTimers, hash)
	}
	delete(h.onlineSince, hash)
	h.mu.Unlock()
	s.Conn.Close()
}
