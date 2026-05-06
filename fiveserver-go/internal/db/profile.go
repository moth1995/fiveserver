package db

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/fiveserver/fiveserver-go/internal/model"
)

const profileSelectCols = `id, user_id, ordinal, name, fav_player, fav_team,
	` + "`rank`" + `, points, disconnects, updated_on, seconds_played`

func scanProfile(rows *sql.Rows) (*model.Profile, error) {
	var p model.Profile
	var updatedOn time.Time
	err := rows.Scan(
		&p.ID, &p.UserID, &p.Ordinal, &p.Name,
		&p.FavPlayer, &p.FavTeam,
		&p.Rank, &p.Points, &p.Disconnects,
		&updatedOn, &p.SecondsPlayed,
	)
	if err != nil {
		return nil, err
	}
	p.UpdatedOn = updatedOn
	return &p, nil
}

// GetProfileByID fetches a single non-deleted profile by primary key.
func GetProfileByID(ctx context.Context, sc *StorageController, id int) (*model.Profile, error) {
	if sc == nil {
		return nil, ErrNoDB
	}
	q := `SELECT ` + profileSelectCols + ` FROM profiles WHERE deleted=0 AND id=?`
	rows, err := sc.Read.DB().QueryContext(ctx, q, id)
	if err != nil {
		return nil, fmt.Errorf("db/profile: query by id: %w", err)
	}
	defer func() { _ = rows.Close() }()
	if !rows.Next() {
		if err := rows.Err(); err != nil {
			return nil, fmt.Errorf("db/profile: rows: %w", err)
		}
		return nil, ErrNotFound
	}
	p, err := scanProfile(rows)
	if err != nil {
		return nil, fmt.Errorf("db/profile: scan: %w", err)
	}
	return p, nil
}

// GetProfilesByUserID returns all non-deleted profiles for a user, ordered by
// updated_on ASC (matches Python ProfileData.getByUserId).
func GetProfilesByUserID(ctx context.Context, sc *StorageController, userID int) ([]*model.Profile, error) {
	if sc == nil {
		return nil, ErrNoDB
	}
	q := `SELECT ` + profileSelectCols + `
	      FROM profiles WHERE deleted=0 AND user_id=?
	      ORDER BY updated_on ASC`
	rows, err := sc.Read.DB().QueryContext(ctx, q, userID)
	if err != nil {
		return nil, fmt.Errorf("db/profile: query by user_id: %w", err)
	}
	defer func() { _ = rows.Close() }()
	var out []*model.Profile
	for rows.Next() {
		p, err := scanProfile(rows)
		if err != nil {
			return nil, fmt.Errorf("db/profile: scan row: %w", err)
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

// CreateProfile inserts a new profile. Returns the new profile ID.
func CreateProfile(ctx context.Context, sc *StorageController, userID int, name string, ordinal int) (int, error) {
	if sc == nil {
		return 0, ErrNoDB
	}
	q := `INSERT INTO profiles (user_id, ordinal, name) VALUES (?, ?, ?)`
	res, err := sc.Write.DB().ExecContext(ctx, q, userID, ordinal, name)
	if err != nil {
		return 0, fmt.Errorf("db/profile: create: %w", err)
	}
	id, err := res.LastInsertId()
	if err != nil {
		return 0, fmt.Errorf("db/profile: last insert id: %w", err)
	}
	return int(id), nil
}

// DeleteProfile soft-deletes a profile.
func DeleteProfile(ctx context.Context, sc *StorageController, id int) error {
	if sc == nil {
		return ErrNoDB
	}
	q := `UPDATE profiles SET deleted=1 WHERE id=?`
	if _, err := sc.Write.DB().ExecContext(ctx, q, id); err != nil {
		return fmt.Errorf("db/profile: delete: %w", err)
	}
	return nil
}

// UpdateProfileStats persists mutable profile fields. Mirrors Python ProfileData.store().
func UpdateProfileStats(ctx context.Context, sc *StorageController, p *model.Profile) error {
	if sc == nil {
		return ErrNoDB
	}
	q := `INSERT INTO profiles (id, user_id, ordinal, name, fav_player, fav_team, ` + "`rank`" + `, points, disconnects, seconds_played)
	      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	      ON DUPLICATE KEY UPDATE
	        deleted=0, user_id=VALUES(user_id), ordinal=VALUES(ordinal), name=VALUES(name),
	        fav_player=VALUES(fav_player), fav_team=VALUES(fav_team),
	        ` + "`rank`" + `=VALUES(` + "`rank`" + `),
	        points=VALUES(points), disconnects=VALUES(disconnects),
	        seconds_played=VALUES(seconds_played)`
	_, err := sc.Write.DB().ExecContext(ctx, q,
		p.ID, p.UserID, p.Ordinal, p.Name,
		p.FavPlayer, p.FavTeam,
		p.Rank, p.Points, p.Disconnects, p.SecondsPlayed,
	)
	if err != nil {
		return fmt.Errorf("db/profile: update stats: %w", err)
	}
	return nil
}

// GetLeaderboard returns the top `limit` profiles ordered by points DESC,
// seconds_played DESC (used for rank display).
func GetLeaderboard(ctx context.Context, sc *StorageController, limit int) ([]*model.Profile, error) {
	if sc == nil {
		return nil, ErrNoDB
	}
	q := `SELECT ` + profileSelectCols + `
	      FROM profiles WHERE deleted=0
	      ORDER BY points DESC, seconds_played DESC
	      LIMIT ?`
	rows, err := sc.Read.DB().QueryContext(ctx, q, limit)
	if err != nil {
		return nil, fmt.Errorf("db/profile: leaderboard: %w", err)
	}
	defer func() { _ = rows.Close() }()
	var out []*model.Profile
	for rows.Next() {
		p, err := scanProfile(rows)
		if err != nil {
			return nil, fmt.Errorf("db/profile: scan leaderboard: %w", err)
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

// GetProfileSettings returns stored settings blobs for a profile.
func GetProfileSettings(ctx context.Context, sc *StorageController, profileID int) (*model.ProfileSettings, error) {
	if sc == nil {
		return nil, ErrNoDB
	}
	q := `SELECT settings1, settings2 FROM settings WHERE profile_id=?`
	row := sc.Read.DB().QueryRowContext(ctx, q, profileID)
	var s model.ProfileSettings
	err := row.Scan(&s.Settings1, &s.Settings2)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return &model.ProfileSettings{}, nil
		}
		return nil, fmt.Errorf("db/profile: get settings: %w", err)
	}
	return &s, nil
}

// StoreProfileSettings upserts settings blobs for a profile.
func StoreProfileSettings(ctx context.Context, sc *StorageController, profileID int, s *model.ProfileSettings) error {
	if sc == nil {
		return ErrNoDB
	}
	q := `INSERT INTO settings (profile_id, settings1, settings2)
	      VALUES (?, ?, ?)
	      ON DUPLICATE KEY UPDATE settings1=VALUES(settings1), settings2=VALUES(settings2)`
	if _, err := sc.Write.DB().ExecContext(ctx, q, profileID, s.Settings1, s.Settings2); err != nil {
		return fmt.Errorf("db/profile: store settings: %w", err)
	}
	return nil
}

// ComputeRanks recomputes the rank column for all non-deleted profiles ordered by
// points DESC, seconds_played DESC (rank 1 = most points). Runs in a single
// transaction so no partial updates are visible. Mirrors Python ProfileData._computeRanksTxn.
func ComputeRanks(ctx context.Context, sc *StorageController) error {
	if sc == nil {
		return ErrNoDB
	}
	tx, err := sc.Write.DB().BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("db/profile: compute ranks begin tx: %w", err)
	}
	defer tx.Rollback() //nolint:errcheck

	rows, err := tx.QueryContext(ctx,
		`SELECT id FROM profiles WHERE deleted=0 ORDER BY points DESC, seconds_played DESC`)
	if err != nil {
		return fmt.Errorf("db/profile: compute ranks query: %w", err)
	}
	var ids []int
	for rows.Next() {
		var id int
		if err := rows.Scan(&id); err != nil {
			rows.Close() //nolint:errcheck
			return fmt.Errorf("db/profile: compute ranks scan: %w", err)
		}
		ids = append(ids, id)
	}
	rows.Close() //nolint:errcheck
	if err := rows.Err(); err != nil {
		return fmt.Errorf("db/profile: compute ranks iterate: %w", err)
	}

	for i, id := range ids {
		if _, err := tx.ExecContext(ctx,
			"UPDATE profiles SET `rank`=? WHERE id=?",
			i+1, id,
		); err != nil {
			return fmt.Errorf("db/profile: compute ranks update rank %d: %w", i+1, err)
		}
	}
	return tx.Commit()
}
