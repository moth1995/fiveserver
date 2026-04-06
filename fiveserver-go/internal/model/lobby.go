package model

import (
	"sync"
	"time"
)

// ---- Lobby ------------------------------------------------------------------

const (
	MaxChatMessages = 50
	MaxChatAgeDays  = 5
)

// Lobby mirrors the Python Lobby class in model/lobby.py.
// Players and Rooms are protected by the Hub's RWMutex in the protocol layer.
type Lobby struct {
	Index           int
	Name            string
	TypeCode        byte
	TypeStr         string
	ShowMatches     bool
	CheckRosterHash bool
	MaxPlayers      int
	RoomOrdinal     int

	mu          sync.RWMutex
	players     map[string]*ConnectedUser // keyed by user hash
	rooms       map[string]*Room
	chatHistory []*ChatMessage
}

func NewLobby(name string, maxPlayers int) *Lobby {
	return &Lobby{
		Name:            name,
		MaxPlayers:      maxPlayers,
		ShowMatches:     true,
		CheckRosterHash: true,
		players:         make(map[string]*ConnectedUser),
		rooms:           make(map[string]*Room),
	}
}

// Bytes serialises the lobby for packet construction, matching Python Lobby.__bytes__.
// [1 byte typeCode] [32 bytes name, null-padded] [2 bytes player count big-endian]
func (l *Lobby) Bytes() []byte {
	l.mu.RLock()
	count := len(l.players)
	l.mu.RUnlock()
	b := make([]byte, 1+32+2)
	b[0] = l.TypeCode
	copy(b[1:33], PadWithZeros(l.Name, 32))
	b[33] = byte(count >> 8)
	b[34] = byte(count)
	return b
}

func (l *Lobby) Enter(u *ConnectedUser) {
	l.mu.Lock()
	l.players[u.User.Hash] = u
	l.mu.Unlock()
}

func (l *Lobby) Exit(u *ConnectedUser) {
	l.mu.Lock()
	delete(l.players, u.User.Hash)
	l.mu.Unlock()
}

func (l *Lobby) PlayerCount() int {
	l.mu.RLock()
	defer l.mu.RUnlock()
	return len(l.players)
}

func (l *Lobby) Players() []*ConnectedUser {
	l.mu.RLock()
	defer l.mu.RUnlock()
	out := make([]*ConnectedUser, 0, len(l.players))
	for _, u := range l.players {
		out = append(out, u)
	}
	return out
}

func (l *Lobby) GetPlayerByProfileID(id int) *ConnectedUser {
	l.mu.RLock()
	defer l.mu.RUnlock()
	for _, u := range l.players {
		if u.Profile != nil && u.Profile.ID == id {
			return u
		}
	}
	return nil
}

func (l *Lobby) AddRoom(r *Room) {
	l.mu.Lock()
	l.RoomOrdinal++
	r.ID = l.RoomOrdinal
	l.rooms[r.Name] = r
	l.mu.Unlock()
}

func (l *Lobby) DeleteRoom(r *Room) {
	l.mu.Lock()
	delete(l.rooms, r.Name)
	l.mu.Unlock()
}

func (l *Lobby) GetRoomByID(id int) *Room {
	l.mu.RLock()
	defer l.mu.RUnlock()
	for _, r := range l.rooms {
		if r.ID == id {
			return r
		}
	}
	return nil
}

func (l *Lobby) Rooms() []*Room {
	l.mu.RLock()
	defer l.mu.RUnlock()
	out := make([]*Room, 0, len(l.rooms))
	for _, r := range l.rooms {
		out = append(out, r)
	}
	return out
}

func (l *Lobby) AddChatMessage(msg *ChatMessage) {
	l.mu.Lock()
	l.chatHistory = append(l.chatHistory, msg)
	if len(l.chatHistory) > MaxChatMessages {
		l.chatHistory = l.chatHistory[len(l.chatHistory)-MaxChatMessages:]
	}
	l.mu.Unlock()
}

func (l *Lobby) ChatHistory() []*ChatMessage {
	l.mu.RLock()
	defer l.mu.RUnlock()
	out := make([]*ChatMessage, len(l.chatHistory))
	copy(out, l.chatHistory)
	return out
}

func (l *Lobby) PurgeOldChat() {
	cutoff := time.Now().AddDate(0, 0, -MaxChatAgeDays)
	l.mu.Lock()
	fresh := l.chatHistory[:0]
	for _, m := range l.chatHistory {
		if m.Timestamp.After(cutoff) {
			fresh = append(fresh, m)
		}
	}
	l.chatHistory = fresh
	l.mu.Unlock()
}

// ---- Chat -------------------------------------------------------------------

// SystemProfile is the sender profile for server-generated chat messages.
var SystemProfile = &Profile{ID: 0, Name: "SYSTEM"}

type ChatMessage struct {
	From      *Profile
	To        *Profile // nil = broadcast
	Text      string
	Special   interface{}
	Timestamp time.Time
}

func NewChatMessage(from *Profile, text string) *ChatMessage {
	return &ChatMessage{From: from, Text: text, Timestamp: time.Now()}
}

// ---- Room -------------------------------------------------------------------

// RoomPhase mirrors Python RoomState constants.
type RoomPhase int

const (
	RoomIdle               RoomPhase = 1
	RoomMatchSideSelect    RoomPhase = 2
	RoomMatchSettingsSelect RoomPhase = 3
	RoomMatchTeamSelect    RoomPhase = 4
	RoomMatchStripSelect   RoomPhase = 5
	RoomMatchFormationSelect RoomPhase = 6
	RoomMatchStarted       RoomPhase = 7
	RoomMatchFinished      RoomPhase = 8
	RoomMatchSeriesEnding  RoomPhase = 10
)

type Room struct {
	ID              int
	Name            string
	Phase           RoomPhase
	MatchTime       int
	MatchSettings   *MatchSettings
	UsePassword     bool
	Password        string
	Players         []*ConnectedUser
	ParticipatingPlayers []*ConnectedUser
	ReadyCount      int
	Owner           *ConnectedUser
	Match           *Match
	MatchStarter    *ConnectedUser
	TeamSelection   *TeamSelection
	Lobby           *Lobby
}

func NewRoom(lobby *Lobby) *Room {
	return &Room{
		Name:      "unnamed",
		MatchTime: 5,
		Phase:     RoomIdle,
		Lobby:     lobby,
	}
}

// Enter adds a user to the room. The first user becomes Owner.
// Mirrors Python Room.enter().
func (r *Room) Enter(u *ConnectedUser) {
	r.Players = append(r.Players, u)
	if r.Owner == nil {
		r.Owner = u
	}
	if u.State != nil {
		u.State.InRoom = true
		u.State.Room = r
	}
}

// Exit removes a user from the room and clears their state.
// Mirrors Python Room.exit().
func (r *Room) Exit(u *ConnectedUser) {
	players := r.Players[:0]
	for _, p := range r.Players {
		if p != u {
			players = append(players, p)
		}
	}
	r.Players = players
	// Reassign owner if they left
	if r.Owner == u {
		if len(r.Players) > 0 {
			r.Owner = r.Players[0]
		} else {
			r.Owner = nil
		}
	}
	if u.State != nil {
		u.State.InRoom = false
		u.State.Room = nil
	}
}

// GetByName looks up a room in a lobby by name (used for duplicate check).
func (l *Lobby) GetRoomByName(name string) (*Room, bool) {
	l.mu.RLock()
	defer l.mu.RUnlock()
	r, ok := l.rooms[name]
	return r, ok
}

func (r *Room) IsOwner(u *ConnectedUser) bool {
	if r.Owner == nil || u.Profile == nil {
		return false
	}
	return r.Owner.Profile != nil && r.Owner.Profile.Name == u.Profile.Name
}

func (r *Room) IsMatchStarter(u *ConnectedUser) bool {
	if r.MatchStarter == nil || u.Profile == nil {
		return false
	}
	return r.MatchStarter.Profile != nil && r.MatchStarter.Profile.Name == u.Profile.Name
}

func (r *Room) IsEmpty() bool { return len(r.Players) == 0 }

func (r *Room) IsAtPregameSettings() bool {
	return RoomIdle < r.Phase && r.Phase < RoomMatchStarted
}

// ---- Match ------------------------------------------------------------------

// MatchPhase mirrors Python MatchState constants.
type MatchPhase int

const (
	MatchNotStarted      MatchPhase = 0
	MatchFirstHalf       MatchPhase = 1
	MatchHalfTime        MatchPhase = 2
	MatchSecondHalf      MatchPhase = 3
	MatchBeforeExtraTime MatchPhase = 4
	MatchETFirstHalf     MatchPhase = 5
	MatchETBreak         MatchPhase = 6
	MatchETSecondHalf    MatchPhase = 7
	MatchBeforePenalties MatchPhase = 8
	MatchPenalties       MatchPhase = 9
	MatchFinished        MatchPhase = 10
)

// Match mirrors the Python Match class in model/lobby.py and the `matches` table.
type Match struct {
	ID            int
	HomeProfileID int
	AwayProfileID int
	HomeTeamID    int
	AwayTeamID    int
	ScoreHome     int
	ScoreAway     int
	StartTime     time.Time
	PlayedOn      time.Time
	HomeExit      interface{} // disconnect tracking
	AwayExit      interface{}
}

// MatchSettings mirrors Python MatchSettings in model/lobby.py.
type MatchSettings struct {
	MatchTime             int
	TimeLimit             int
	NumberOfPauses        int
	ChatDuringGameplay    int
	Condition             int
	Injuries              int
	MaxSubstitutions      int
	MatchTypeEx           int
	MatchTypePK           int
	Time                  int
	Season                int
	Weather               int
}

// TeamSelection mirrors Python TeamSelection in model/lobby.py.
type TeamSelection struct {
	HomeTeamID      int
	AwayTeamID      int
	HomeCaptain     *Profile
	AwayCaptain     *Profile
	HomeMorePlayers []*Profile
	AwayMorePlayers []*Profile
}

// GetHomeOrAway returns 0x00 for home side, 0x01 for away, 0xFF if not found.
func (ts *TeamSelection) GetHomeOrAway(u *ConnectedUser) byte {
	if u.Profile == nil {
		return 0xFF
	}
	if ts.HomeCaptain != nil && ts.HomeCaptain.ID == u.Profile.ID {
		return 0x00
	}
	for _, p := range ts.HomeMorePlayers {
		if p.ID == u.Profile.ID {
			return 0x00
		}
	}
	if ts.AwayCaptain != nil && ts.AwayCaptain.ID == u.Profile.ID {
		return 0x01
	}
	for _, p := range ts.AwayMorePlayers {
		if p.ID == u.Profile.ID {
			return 0x01
		}
	}
	return 0xFF
}
