package db

import (
	"context"
	"fmt"
)

// AddFriend inserts a friend relationship. Idempotent (INSERT IGNORE).
func AddFriend(ctx context.Context, sc *StorageController, profileID, friendID int) error {
	if sc == nil {
		return ErrNoDB
	}
	q := `INSERT IGNORE INTO friends (profile_id, friend_profile_id) VALUES (?, ?)`
	if _, err := sc.Write.DB().ExecContext(ctx, q, profileID, friendID); err != nil {
		return fmt.Errorf("db/friends: add friend: %w", err)
	}
	return nil
}

// RemoveFriend deletes a friend relationship.
func RemoveFriend(ctx context.Context, sc *StorageController, profileID, friendID int) error {
	if sc == nil {
		return ErrNoDB
	}
	q := `DELETE FROM friends WHERE profile_id=? AND friend_profile_id=?`
	if _, err := sc.Write.DB().ExecContext(ctx, q, profileID, friendID); err != nil {
		return fmt.Errorf("db/friends: remove friend: %w", err)
	}
	return nil
}

// BlockProfile inserts a blocked relationship. Idempotent (INSERT IGNORE).
func BlockProfile(ctx context.Context, sc *StorageController, profileID, blockedID int) error {
	if sc == nil {
		return ErrNoDB
	}
	q := `INSERT IGNORE INTO blocked (profile_id, blocked_profile_id) VALUES (?, ?)`
	if _, err := sc.Write.DB().ExecContext(ctx, q, profileID, blockedID); err != nil {
		return fmt.Errorf("db/friends: block profile: %w", err)
	}
	return nil
}

// UnblockProfile deletes a blocked relationship.
func UnblockProfile(ctx context.Context, sc *StorageController, profileID, blockedID int) error {
	if sc == nil {
		return ErrNoDB
	}
	q := `DELETE FROM blocked WHERE profile_id=? AND blocked_profile_id=?`
	if _, err := sc.Write.DB().ExecContext(ctx, q, profileID, blockedID); err != nil {
		return fmt.Errorf("db/friends: unblock profile: %w", err)
	}
	return nil
}

// FriendEntry is a single row returned by GetFriendsAndBlockedForProfile.
type FriendEntry struct {
	ProfileID int
	Flag      byte   // 0 = friend, 1 = blocked
	Name      string
}

// GetFriendsAndBlockedForProfile returns all friends (flag=0) and blocked
// profiles (flag=1) for the given profile, ordered by flag then name.
// Mirrors Python getFriendsAndBlocked_3080 data source.
func GetFriendsAndBlockedForProfile(ctx context.Context, sc *StorageController, profileID int) ([]FriendEntry, error) {
	if sc == nil {
		return nil, ErrNoDB
	}
	const q = `
		SELECT f.friend_profile_id, 0, p.name
		FROM friends f
		JOIN profiles p ON p.id = f.friend_profile_id
		WHERE f.profile_id = ?
		UNION ALL
		SELECT b.blocked_profile_id, 1, p.name
		FROM blocked b
		JOIN profiles p ON p.id = b.blocked_profile_id
		WHERE b.profile_id = ?
		ORDER BY 2 ASC, 3 ASC`
	rows, err := sc.Read.DB().QueryContext(ctx, q, profileID, profileID)
	if err != nil {
		return nil, fmt.Errorf("db/friends: get friends and blocked: %w", err)
	}
	defer func() { _ = rows.Close() }()
	var entries []FriendEntry
	for rows.Next() {
		var e FriendEntry
		if err := rows.Scan(&e.ProfileID, &e.Flag, &e.Name); err != nil {
			return nil, fmt.Errorf("db/friends: scan entry: %w", err)
		}
		entries = append(entries, e)
	}
	return entries, rows.Err()
}
