package model

import "time"

// Profile mirrors the `profiles` table in sql/schema.sql and the Python
// Profile class in model/user.py.
type Profile struct {
	ID            int
	UserID        int
	Ordinal       int    // 0-2, position in the user's profile list
	Name          string // up to 32 chars, unique
	FavPlayer     int64
	FavTeam       int64
	Rank          int
	Points        int
	Disconnects   int
	SecondsPlayed int64
	UpdatedOn     time.Time
	Settings      ProfileSettings
}

// ConnectedUser represents a fully authenticated in-memory session.
// Replaces the Python User instance that lived on the protocol connection.
// Conn is stored as interface{} to avoid an import cycle with the server package.
type ConnectedUser struct {
	User        *User
	Profile     *Profile  // nil until a profile is selected (0x3040)
	Profiles    []*Profile // all profiles for this user (loaded at login)
	GameVersion string    // "pes5", "we9", "we9le"
	LobbyIndex  int       // -1 when not in a lobby
	Conn        interface{} // *server.Conn
	Info        *UserInfo
	NeedsLobbyChatReplay bool
	State                *NetworkState // nil until selectLobby (0x4202)
}
