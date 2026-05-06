package db

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"
)

// MessageEntry is the wire-level result for the 0x4784 packet.
// Bitfield: 0 = inbox (regular), 0xFFFFFFFF = outbox.
type MessageEntry struct {
	ID         int64
	FromID     int
	ToID       int
	SenderName string
	Body       string
	SentAt     time.Time
	Bitfield   uint32
}

// SendMessage stores a private message from one profile to another.
func SendMessage(ctx context.Context, sc *StorageController, fromID, toID int, body string) error {
	if sc == nil {
		return ErrNoDB
	}
	q := `INSERT INTO messages (from_id, to_id, body) VALUES (?, ?, ?)`
	if _, err := sc.Write.DB().ExecContext(ctx, q, fromID, toID, body); err != nil {
		return fmt.Errorf("db/messages: send: %w", err)
	}
	return nil
}

// GetMessagesForProfile returns inbox (bitfield=0) followed by outbox (bitfield=0xFFFFFFFF)
// messages for profileID, up to 50 each, ordered by sent_at DESC within each group.
func GetMessagesForProfile(ctx context.Context, sc *StorageController, profileID int) ([]*MessageEntry, error) {
	if sc == nil {
		return nil, ErrNoDB
	}
	q := `SELECT m.id, m.from_id, m.to_id, COALESCE(p.name,''), m.body, m.sent_at
	      FROM messages m LEFT JOIN profiles p ON p.id = m.from_id
	      WHERE m.to_id=? ORDER BY m.sent_at DESC LIMIT 50`
	inboxRows, inboxErr := sc.Read.DB().QueryContext(ctx, q, profileID)
	inbox, err := scanMessageEntries(inboxRows, inboxErr, 0)
	if err != nil {
		return nil, err
	}
	q2 := `SELECT m.id, m.from_id, m.to_id, COALESCE(p.name,''), m.body, m.sent_at
	       FROM messages m LEFT JOIN profiles p ON p.id = m.from_id
	       WHERE m.from_id=? ORDER BY m.sent_at DESC LIMIT 50`
	outboxRows, outboxErr := sc.Read.DB().QueryContext(ctx, q2, profileID)
	outbox, err := scanMessageEntries(outboxRows, outboxErr, 0xFFFFFFFF)
	if err != nil {
		return nil, err
	}
	return append(inbox, outbox...), nil
}

func scanMessageEntries(rows *sql.Rows, queryErr error, bitfield uint32) ([]*MessageEntry, error) {
	if queryErr != nil {
		return nil, fmt.Errorf("db/messages: query: %w", queryErr)
	}
	defer func() { _ = rows.Close() }()
	var out []*MessageEntry
	for rows.Next() {
		var e MessageEntry
		e.Bitfield = bitfield
		if err := rows.Scan(&e.ID, &e.FromID, &e.ToID, &e.SenderName, &e.Body, &e.SentAt); err != nil {
			return nil, fmt.Errorf("db/messages: scan: %w", err)
		}
		out = append(out, &e)
	}
	if err := rows.Err(); err != nil && !errors.Is(err, sql.ErrNoRows) {
		return nil, fmt.Errorf("db/messages: rows: %w", err)
	}
	return out, nil
}
