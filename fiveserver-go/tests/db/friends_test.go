package db_test

import (
	"context"
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/db"
)

func TestAddFriend_NilSC_ReturnsErrNoDB(t *testing.T) {
	err := db.AddFriend(context.Background(), nil, 1, 2)
	if err != db.ErrNoDB {
		t.Fatalf("expected ErrNoDB, got %v", err)
	}
}

func TestRemoveFriend_NilSC_ReturnsErrNoDB(t *testing.T) {
	err := db.RemoveFriend(context.Background(), nil, 1, 2)
	if err != db.ErrNoDB {
		t.Fatalf("expected ErrNoDB, got %v", err)
	}
}

func TestBlockProfile_NilSC_ReturnsErrNoDB(t *testing.T) {
	err := db.BlockProfile(context.Background(), nil, 1, 2)
	if err != db.ErrNoDB {
		t.Fatalf("expected ErrNoDB, got %v", err)
	}
}

func TestUnblockProfile_NilSC_ReturnsErrNoDB(t *testing.T) {
	err := db.UnblockProfile(context.Background(), nil, 1, 2)
	if err != db.ErrNoDB {
		t.Fatalf("expected ErrNoDB, got %v", err)
	}
}

func TestAddFriend_NoServer_ReturnsError(t *testing.T) {
	sc := newSC(t)
	err := db.AddFriend(context.Background(), sc, 1, 2)
	if err == nil {
		t.Fatal("expected error when no server available")
	}
}

func TestRemoveFriend_NoServer_ReturnsError(t *testing.T) {
	sc := newSC(t)
	err := db.RemoveFriend(context.Background(), sc, 1, 2)
	if err == nil {
		t.Fatal("expected error when no server available")
	}
}

func TestBlockProfile_NoServer_ReturnsError(t *testing.T) {
	sc := newSC(t)
	err := db.BlockProfile(context.Background(), sc, 1, 2)
	if err == nil {
		t.Fatal("expected error when no server available")
	}
}

func TestUnblockProfile_NoServer_ReturnsError(t *testing.T) {
	sc := newSC(t)
	err := db.UnblockProfile(context.Background(), sc, 1, 2)
	if err == nil {
		t.Fatal("expected error when no server available")
	}
}
