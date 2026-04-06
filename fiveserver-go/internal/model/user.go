package model

// User mirrors the `users` table in sql/schema.sql.
type User struct {
	ID         int
	Username   string
	Serial     string
	Hash       string  // 32-char hex string stored in DB (Blowfish-encrypted)
	ResetNonce string  // null when not locked
	Deleted    bool
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
