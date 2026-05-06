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
