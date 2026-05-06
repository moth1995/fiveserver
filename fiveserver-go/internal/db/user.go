package db

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/fiveserver/fiveserver-go/internal/model"
)

// ErrNotFound is returned when a query finds no rows.
var ErrNotFound = errors.New("db: not found")

// ErrNoDB is returned when a nil *StorageController is passed to a DB function.
var ErrNoDB = errors.New("db: no storage controller")

const userSelectCols = `id, username, serial, hash, COALESCE(reset_nonce,''), deleted`

func scanUser(row *sql.Row) (*model.User, error) {
	var u model.User
	var deleted bool
	if err := row.Scan(&u.ID, &u.Username, &u.Serial, &u.Hash, &u.ResetNonce, &deleted); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("db/user: scan: %w", err)
	}
	u.Deleted = deleted
	return &u, nil
}

// GetUserByID fetches a non-deleted user by primary key.
func GetUserByID(ctx context.Context, sc *StorageController, id int) (*model.User, error) {
	if sc == nil {
		return nil, ErrNoDB
	}
	q := `SELECT ` + userSelectCols + ` FROM users WHERE deleted=0 AND id=?`
	return scanUser(sc.Read.DB().QueryRowContext(ctx, q, id))
}

// GetUserByUsername fetches a non-deleted user by username.
func GetUserByUsername(ctx context.Context, sc *StorageController, username string) (*model.User, error) {
	if sc == nil {
		return nil, ErrNoDB
	}
	q := `SELECT ` + userSelectCols + ` FROM users WHERE deleted=0 AND username=?`
	return scanUser(sc.Read.DB().QueryRowContext(ctx, q, username))
}

// GetUserByHash fetches a non-deleted user by hash (used for authentication).
func GetUserByHash(ctx context.Context, sc *StorageController, hash string) (*model.User, error) {
	if sc == nil {
		return nil, ErrNoDB
	}
	q := `SELECT ` + userSelectCols + ` FROM users WHERE deleted=0 AND hash=?`
	return scanUser(sc.Read.DB().QueryRowContext(ctx, q, hash))
}

// GetUserByNonce fetches a non-deleted user by reset_nonce (password reset flow).
func GetUserByNonce(ctx context.Context, sc *StorageController, nonce string) (*model.User, error) {
	if sc == nil {
		return nil, ErrNoDB
	}
	q := `SELECT ` + userSelectCols + ` FROM users WHERE deleted=0 AND reset_nonce=?`
	return scanUser(sc.Read.DB().QueryRowContext(ctx, q, nonce))
}

// CreateUser inserts a new user or updates an existing one (upsert).
// Mirrors Python UserData.store(). Returns the row ID.
func CreateUser(ctx context.Context, sc *StorageController, username, serial, hash string) (int, error) {
	if sc == nil {
		return 0, ErrNoDB
	}
	q := `INSERT INTO users (username, serial, hash)
	      VALUES (?, ?, ?)
	      ON DUPLICATE KEY UPDATE deleted=0, username=VALUES(username), serial=VALUES(serial), hash=VALUES(hash)`
	res, err := sc.Write.DB().ExecContext(ctx, q, username, serial, hash)
	if err != nil {
		return 0, fmt.Errorf("db/user: create: %w", err)
	}
	id, err := res.LastInsertId()
	if err != nil {
		return 0, fmt.Errorf("db/user: last insert id: %w", err)
	}
	return int(id), nil
}

// UpdateUserHash updates the password hash for a user.
func UpdateUserHash(ctx context.Context, sc *StorageController, id int, hash string) error {
	if sc == nil {
		return ErrNoDB
	}
	q := `UPDATE users SET hash=? WHERE id=?`
	if _, err := sc.Write.DB().ExecContext(ctx, q, hash, id); err != nil {
		return fmt.Errorf("db/user: update hash: %w", err)
	}
	return nil
}

// SetResetNonce sets (or clears when nonce=="") the reset_nonce for a user.
func SetResetNonce(ctx context.Context, sc *StorageController, id int, nonce string) error {
	if sc == nil {
		return ErrNoDB
	}
	var q string
	var args []interface{}
	if nonce == "" {
		q = `UPDATE users SET reset_nonce=NULL WHERE id=?`
		args = []interface{}{id}
	} else {
		q = `UPDATE users SET reset_nonce=? WHERE id=?`
		args = []interface{}{nonce, id}
	}
	if _, err := sc.Write.DB().ExecContext(ctx, q, args...); err != nil {
		return fmt.Errorf("db/user: set nonce: %w", err)
	}
	return nil
}

// DeleteUser soft-deletes a user (sets deleted=1).
func DeleteUser(ctx context.Context, sc *StorageController, id int) error {
	if sc == nil {
		return ErrNoDB
	}
	q := `UPDATE users SET deleted=1 WHERE id=?`
	if _, err := sc.Write.DB().ExecContext(ctx, q, id); err != nil {
		return fmt.Errorf("db/user: delete: %w", err)
	}
	return nil
}
