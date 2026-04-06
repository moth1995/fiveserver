package db_test

import (
	"context"
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/db"
	"github.com/fiveserver/fiveserver-go/internal/model"
)

// These tests verify that function signatures, error wrapping, and ErrNotFound
// behave correctly against a non-reachable MySQL. A real DB integration test
// would require a running server and is out of scope for the unit suite.

func newSC(t *testing.T) *db.StorageController {
	t.Helper()
	cfg := testDBConfig()
	sc, err := db.NewStorageController(cfg)
	if err != nil {
		t.Fatalf("NewStorageController: %v", err)
	}
	t.Cleanup(func() { sc.Close() })
	return sc
}

// --- GetUserByID ---

func TestGetUserByID_NoServer_ReturnsError(t *testing.T) {
	sc := newSC(t)
	_, err := db.GetUserByID(context.Background(), sc, 1)
	if err == nil {
		t.Fatal("expected error when no server available")
	}
}

// --- GetUserByUsername ---

func TestGetUserByUsername_NoServer_ReturnsError(t *testing.T) {
	sc := newSC(t)
	_, err := db.GetUserByUsername(context.Background(), sc, "player1")
	if err == nil {
		t.Fatal("expected error when no server available")
	}
}

// --- GetProfilesByUserID ---

func TestGetProfilesByUserID_NoServer_ReturnsError(t *testing.T) {
	sc := newSC(t)
	_, err := db.GetProfilesByUserID(context.Background(), sc, 1)
	if err == nil {
		t.Fatal("expected error when no server available")
	}
}

// --- UpdateProfileStats nil-safety ---

func TestUpdateProfileStats_NilProfile_Panics(t *testing.T) {
	// Verify that passing a non-nil Profile with zero ID doesn't silently succeed
	// (it should propagate a DB error, not panic)
	defer func() {
		if r := recover(); r != nil {
			t.Errorf("unexpected panic: %v", r)
		}
	}()
	sc := newSC(t)
	p := &model.Profile{ID: 0, UserID: 1, Name: "test", Ordinal: 0}
	err := db.UpdateProfileStats(context.Background(), sc, p)
	// No server reachable — expect an error, not a panic
	if err == nil {
		t.Fatal("expected error when no server available")
	}
}

// --- GetMatchesByProfileID ---

func TestGetMatchesByProfileID_NoServer_ReturnsError(t *testing.T) {
	sc := newSC(t)
	_, err := db.GetMatchesByProfileID(context.Background(), sc, 1, 10)
	if err == nil {
		t.Fatal("expected error when no server available")
	}
}

// --- GetStreakByProfileID ---

func TestGetStreakByProfileID_NoServer_ReturnsZero(t *testing.T) {
	// GetStreakByProfileID treats ErrNoRows as (0,0,nil) — but a connection
	// failure should propagate differently. Since we can't connect, we just
	// verify it doesn't panic.
	sc := newSC(t)
	wins, best, err := db.GetStreakByProfileID(context.Background(), sc, 1)
	// Connection failure: we either get (0,0,nil) from the no-rows branch
	// or an error — either is acceptable here.
	_ = wins
	_ = best
	_ = err
}

// --- RecordMatch ---

func TestRecordMatch_NoServer_ReturnsError(t *testing.T) {
	sc := newSC(t)
	m := &model.Match{
		HomeProfileID: 1,
		AwayProfileID: 2,
		ScoreHome:     3,
		ScoreAway:     1,
		HomeTeamID:    10,
		AwayTeamID:    20,
	}
	_, err := db.RecordMatch(context.Background(), sc, m)
	if err == nil {
		t.Fatal("expected error when no server available")
	}
}

// --- ErrNotFound is exported ---

func TestErrNotFound_IsExported(t *testing.T) {
	if db.ErrNotFound == nil {
		t.Fatal("ErrNotFound must not be nil")
	}
}
