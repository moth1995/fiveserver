package db

import (
	"context"
	"fmt"
	"time"

	"github.com/fiveserver/fiveserver-go/internal/model"
)

// RecordMatch atomically inserts a match result and updates both players'
// winning streaks, exactly as Python MatchData._storeTxn does.
// Returns the new match ID.
func RecordMatch(ctx context.Context, sc *StorageController, m *model.Match) (int, error) {
	if sc == nil {
		return 0, ErrNoDB
	}
	tx, err := sc.Write.DB().BeginTx(ctx, nil)
	if err != nil {
		return 0, fmt.Errorf("db/match: begin tx: %w", err)
	}
	defer tx.Rollback()

	// 1. Insert match row
	res, err := tx.ExecContext(ctx,
		`INSERT INTO matches (profile_id_home, profile_id_away, score_home, score_away, team_id_home, team_id_away)
		 VALUES (?, ?, ?, ?, ?, ?)`,
		m.HomeProfileID, m.AwayProfileID,
		m.ScoreHome, m.ScoreAway,
		m.HomeTeamID, m.AwayTeamID,
	)
	if err != nil {
		return 0, fmt.Errorf("db/match: insert: %w", err)
	}
	matchID, err := res.LastInsertId()
	if err != nil {
		return 0, fmt.Errorf("db/match: last insert id: %w", err)
	}

	// 2. Update streaks — mirrors Python _writeStreak()
	writeStreak := func(profileID int, win bool) error {
		var wins, best int
		row := tx.QueryRowContext(ctx,
			`SELECT wins, best FROM streaks WHERE profile_id=?`, profileID)
		_ = row.Scan(&wins, &best) // ignore ErrNoRows: defaults stay 0

		if win {
			wins++
			if wins > best {
				best = wins
			}
		} else {
			wins = 0
		}
		_, err := tx.ExecContext(ctx,
			`INSERT INTO streaks (profile_id, wins, best) VALUES (?, ?, ?)
			 ON DUPLICATE KEY UPDATE wins=VALUES(wins), best=VALUES(best)`,
			profileID, wins, best)
		return err
	}

	switch {
	case m.ScoreHome > m.ScoreAway:
		err = writeStreak(m.HomeProfileID, true)
		if err == nil {
			err = writeStreak(m.AwayProfileID, false)
		}
	case m.ScoreHome < m.ScoreAway:
		err = writeStreak(m.HomeProfileID, false)
		if err == nil {
			err = writeStreak(m.AwayProfileID, true)
		}
	default: // draw
		err = writeStreak(m.HomeProfileID, false)
		if err == nil {
			err = writeStreak(m.AwayProfileID, false)
		}
	}
	if err != nil {
		return 0, fmt.Errorf("db/match: update streak: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("db/match: commit: %w", err)
	}
	return int(matchID), nil
}

// MatchRow is the result shape returned by GetMatchesByProfileID.
// Matches are normalised so the requesting player is always "my" side,
// matching the feature/pes5-last-10-matches branch logic in data.py.
type MatchRow struct {
	OpponentProfileID int
	MyScore           int
	OppScore          int
	MyTeamID          int
	OppTeamID         int
	PlayedOn          time.Time
}

// GetMatchesByProfileID returns the last `limit` matches for a profile,
// normalised as "requesting player always home". Mirrors the UNION ALL query
// introduced in feature/pes5-last-10-matches (commit 10c5ff6).
func GetMatchesByProfileID(ctx context.Context, sc *StorageController, profileID, limit int) ([]*MatchRow, error) {
	if sc == nil {
		return nil, ErrNoDB
	}
	q := `
SELECT opponent_profile_id, my_score, opp_score, my_team_id, opp_team_id, played_on
FROM (
    SELECT id,
           profile_id_away  AS opponent_profile_id,
           score_home       AS my_score,
           score_away       AS opp_score,
           team_id_home     AS my_team_id,
           team_id_away     AS opp_team_id,
           played_on
    FROM matches WHERE profile_id_home = ?
    UNION ALL
    SELECT id,
           profile_id_home  AS opponent_profile_id,
           score_away       AS my_score,
           score_home       AS opp_score,
           team_id_away     AS my_team_id,
           team_id_home     AS opp_team_id,
           played_on
    FROM matches WHERE profile_id_away = ?
) AS t
ORDER BY t.id DESC LIMIT ?`

	rows, err := sc.Read.DB().QueryContext(ctx, q, profileID, profileID, limit)
	if err != nil {
		return nil, fmt.Errorf("db/match: get last matches: %w", err)
	}
	defer rows.Close()

	var out []*MatchRow
	for rows.Next() {
		var r MatchRow
		if err := rows.Scan(
			&r.OpponentProfileID,
			&r.MyScore, &r.OppScore,
			&r.MyTeamID, &r.OppTeamID,
			&r.PlayedOn,
		); err != nil {
			return nil, fmt.Errorf("db/match: scan: %w", err)
		}
		out = append(out, &r)
	}
	return out, rows.Err()
}

// GetStreakByProfileID returns the current win streak and all-time best for a profile.
func GetStreakByProfileID(ctx context.Context, sc *StorageController, profileID int) (wins, best int, err error) {
	if sc == nil {
		return 0, 0, ErrNoDB
	}
	row := sc.Read.DB().QueryRowContext(ctx,
		`SELECT wins, best FROM streaks WHERE profile_id=?`, profileID)
	if scanErr := row.Scan(&wins, &best); scanErr != nil {
		// No row = streak never recorded = (0, 0)
		return 0, 0, nil
	}
	return wins, best, nil
}

// GetStatsByProfileID returns aggregated match statistics for a profile.
// Mirrors Python ProfileLogic.getStats which queries wins/losses/draws/goals/streaks.
func GetStatsByProfileID(ctx context.Context, sc *StorageController, profileID int) (*model.Stats, error) {
	if sc == nil {
		return nil, ErrNoDB
	}
	q := `
SELECT
  SUM(CASE WHEN (profile_id_home=? AND score_home>score_away) OR (profile_id_away=? AND score_home<score_away) THEN 1 ELSE 0 END),
  SUM(CASE WHEN (profile_id_home=? AND score_home<score_away) OR (profile_id_away=? AND score_home>score_away) THEN 1 ELSE 0 END),
  SUM(CASE WHEN (profile_id_home=? OR profile_id_away=?) AND score_home=score_away THEN 1 ELSE 0 END),
  SUM(CASE WHEN profile_id_home=? THEN score_home WHEN profile_id_away=? THEN score_away ELSE 0 END),
  SUM(CASE WHEN profile_id_home=? THEN score_away WHEN profile_id_away=? THEN score_home ELSE 0 END)
FROM matches
WHERE profile_id_home=? OR profile_id_away=?`

	id := profileID
	row := sc.Read.DB().QueryRowContext(ctx, q, id, id, id, id, id, id, id, id, id, id, id, id)
	s := &model.Stats{ProfileID: profileID}
	if err := row.Scan(&s.Wins, &s.Losses, &s.Draws, &s.GoalsScored, &s.GoalsAllowed); err != nil {
		return nil, fmt.Errorf("db/match: stats: %w", err)
	}

	// Streak is stored in a separate table
	wins, best, err := GetStreakByProfileID(ctx, sc, profileID)
	if err == nil {
		s.StreakCurrent = wins
		s.StreakBest = best
	}
	return s, nil
}

// UpdateStreak explicitly sets the streak for a profile (used by admin / correction flows).
func UpdateStreak(ctx context.Context, sc *StorageController, profileID, wins, best int) error {
	if sc == nil {
		return ErrNoDB
	}
	q := `INSERT INTO streaks (profile_id, wins, best) VALUES (?, ?, ?)
	      ON DUPLICATE KEY UPDATE wins=VALUES(wins), best=VALUES(best)`
	if _, err := sc.Write.DB().ExecContext(ctx, q, profileID, wins, best); err != nil {
		return fmt.Errorf("db/match: update streak: %w", err)
	}
	return nil
}
