package model

// User mirrors the `users` table in sql/schema.sql.
type User struct {
	ID                 int
	Username           string
	Serial             string
	Hash               string // 32-char hex string stored in DB (Blowfish-encrypted)
	ResetNonce         string // null when not locked
	Deleted            bool
	TotalOnlineSeconds int64
}

// UserInfo holds per-connection game metadata (not persisted).
// Mirrors Python UserInfo in model/user.py.
type UserInfo struct {
	GameName   string
	RosterHash string
}

// ProfileSettings holds the two opaque settings blobs from the `settings` table.
type ProfileSettings struct {
	Settings1 []byte
	Settings2 []byte
}

// NetworkState holds per-connection game-lobby state set during selectLobby (0x4202).
// Mirrors Python UserState fields.
type NetworkState struct {
	IP1         []byte // 16 bytes, internal IP (from pkt.Data[1:17])
	IP2         []byte // 16 bytes, external IP (from pkt.Data[19:35])
	UDPPort1    uint16
	UDPPort2    uint16
	SomeField   uint16
	InRoom      bool
	NoLobbyChat int
	Room        *Room // nil when not in a room
	TeamID      int
}

// Stats holds aggregated match statistics for a profile.
// Mirrors Python Stats in model/user.py.
type Stats struct {
	ProfileID     int
	Wins          int
	Losses        int
	Draws         int
	GoalsScored   int
	GoalsAllowed  int
	StreakCurrent int
	StreakBest    int
}
