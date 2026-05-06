package db_test

import (
	"context"
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/db"
)

func TestSendMessage_NilSC_ReturnsErrNoDB(t *testing.T) {
	err := db.SendMessage(context.Background(), nil, 1, 2, "hello")
	if err != db.ErrNoDB {
		t.Fatalf("expected ErrNoDB, got %v", err)
	}
}

func TestSendMessage_NoServer_ReturnsError(t *testing.T) {
	sc := newSC(t)
	err := db.SendMessage(context.Background(), sc, 1, 2, "hello")
	if err == nil {
		t.Fatal("expected error when no server available")
	}
}

func TestGetMessagesForProfile_NilSC_ReturnsErrNoDB(t *testing.T) {
	_, err := db.GetMessagesForProfile(context.Background(), nil, 1)
	if err != db.ErrNoDB {
		t.Fatalf("expected ErrNoDB, got %v", err)
	}
}

func TestGetMessagesForProfile_NoServer_ReturnsError(t *testing.T) {
	sc := newSC(t)
	_, err := db.GetMessagesForProfile(context.Background(), sc, 1)
	if err == nil {
		t.Fatal("expected error when no server available")
	}
}
