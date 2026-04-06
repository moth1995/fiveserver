package model_test

import (
	"bytes"
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/model"
)

// ---- util -------------------------------------------------------------------

func TestPadWithZeros_Short(t *testing.T) {
	got := model.PadWithZeros("hello", 10)
	want := append([]byte("hello"), make([]byte, 5)...)
	if !bytes.Equal(got, want) {
		t.Fatalf("got %x want %x", got, want)
	}
}

func TestPadWithZeros_Exact(t *testing.T) {
	got := model.PadWithZeros("hello", 5)
	if !bytes.Equal(got, []byte("hello")) {
		t.Fatalf("got %x want %x", got, []byte("hello"))
	}
}

func TestPadWithZeros_Truncate(t *testing.T) {
	got := model.PadWithZeros("hello world", 5)
	if !bytes.Equal(got, []byte("hello")) {
		t.Fatalf("got %x want %x", got, []byte("hello"))
	}
}

func TestPadWithZeros_Empty(t *testing.T) {
	got := model.PadWithZeros("", 4)
	if len(got) != 4 {
		t.Fatalf("expected 4 bytes, got %d", len(got))
	}
	for _, b := range got {
		if b != 0 {
			t.Fatalf("expected all zeros, got %x", got)
		}
	}
}

func TestStripZeros_MidNull(t *testing.T) {
	input := []byte{'a', 'b', 0, 'c', 'd'}
	got := model.StripZeros(input)
	if !bytes.Equal(got, []byte("ab")) {
		t.Fatalf("got %q want %q", got, "ab")
	}
}

func TestStripZeros_NoNull(t *testing.T) {
	input := []byte("hello")
	got := model.StripZeros(input)
	if !bytes.Equal(got, input) {
		t.Fatalf("got %q want %q", got, input)
	}
}

func TestStripZeros_AllNull(t *testing.T) {
	input := []byte{0, 0, 0}
	got := model.StripZeros(input)
	if len(got) != 0 {
		t.Fatalf("expected empty, got %x", got)
	}
}

func TestPadStrip_RoundTrip(t *testing.T) {
	// PadWithZeros then StripZeros should recover original string bytes
	original := "Russia"
	padded := model.PadWithZeros(original, 32)
	stripped := model.StripZeros(padded)
	if string(stripped) != original {
		t.Fatalf("round-trip failed: got %q want %q", stripped, original)
	}
}

// ---- Lobby ------------------------------------------------------------------

func TestLobby_EnterExit(t *testing.T) {
	l := model.NewLobby("Russia", 100)
	u := &model.ConnectedUser{
		User:    &model.User{Hash: "abc123"},
		Profile: &model.Profile{ID: 1, Name: "player1"},
	}
	l.Enter(u)
	if l.PlayerCount() != 1 {
		t.Fatalf("expected 1 player, got %d", l.PlayerCount())
	}
	l.Exit(u)
	if l.PlayerCount() != 0 {
		t.Fatalf("expected 0 players after exit, got %d", l.PlayerCount())
	}
}

func TestLobby_Bytes_Layout(t *testing.T) {
	l := model.NewLobby("Russia", 100)
	l.TypeCode = 0x5f
	b := l.Bytes()
	if len(b) != 35 {
		t.Fatalf("expected 35 bytes, got %d", len(b))
	}
	if b[0] != 0x5f {
		t.Errorf("byte[0] typeCode = 0x%x, want 0x5f", b[0])
	}
	// name occupies bytes 1–32, null-padded
	name := model.StripZeros(b[1:33])
	if string(name) != "Russia" {
		t.Errorf("name = %q, want Russia", name)
	}
	// player count (big-endian u16) = 0
	if b[33] != 0 || b[34] != 0 {
		t.Errorf("player count bytes = %x %x, want 0 0", b[33], b[34])
	}
}

func TestLobby_ChatHistory_Capped(t *testing.T) {
	l := model.NewLobby("test", 10)
	for i := 0; i < model.MaxChatMessages+10; i++ {
		l.AddChatMessage(model.NewChatMessage(model.SystemProfile, "msg"))
	}
	if len(l.ChatHistory()) != model.MaxChatMessages {
		t.Fatalf("expected %d messages, got %d", model.MaxChatMessages, len(l.ChatHistory()))
	}
}

// ---- TeamSelection ----------------------------------------------------------

func TestTeamSelection_GetHomeOrAway(t *testing.T) {
	home := &model.Profile{ID: 1, Name: "home"}
	away := &model.Profile{ID: 2, Name: "away"}
	ts := &model.TeamSelection{HomeCaptain: home, AwayCaptain: away}

	uHome := &model.ConnectedUser{User: &model.User{}, Profile: home}
	uAway := &model.ConnectedUser{User: &model.User{}, Profile: away}
	uOther := &model.ConnectedUser{User: &model.User{}, Profile: &model.Profile{ID: 99}}

	if ts.GetHomeOrAway(uHome) != 0x00 {
		t.Error("home captain should return 0x00")
	}
	if ts.GetHomeOrAway(uAway) != 0x01 {
		t.Error("away captain should return 0x01")
	}
	if ts.GetHomeOrAway(uOther) != 0xFF {
		t.Error("unknown player should return 0xFF")
	}
}
