package protocol_test

import (
	"encoding/binary"
	"testing"
	_ "unsafe"

	"github.com/fiveserver/fiveserver-go/internal/model"
	"github.com/fiveserver/fiveserver-go/internal/protocol"
)

//go:linkname sendChatHistory github.com/fiveserver/fiveserver-go/internal/protocol.sendChatHistory
func sendChatHistory(lobby *model.Lobby, s *protocol.Session)

//go:linkname formatProfileInfo github.com/fiveserver/fiveserver-go/internal/protocol.formatProfileInfo
func formatProfileInfo(p *model.Profile, s *model.Stats, showStats bool) []byte

func TestSendChatHistory_FiltersPrivateMessagesAndPreservesSpecial(t *testing.T) {
	s, cap := newCaptureSession(nil)
	me := &model.Profile{ID: 10, Name: "Me"}
	s.User = &model.ConnectedUser{
		User:    &model.User{Hash: "me"},
		Profile: me,
	}

	lobby := model.NewLobby("Test", 100)
	broadcastFrom := &model.Profile{ID: 20, Name: "Broadcaster"}
	privateFrom := &model.Profile{ID: 21, Name: "Friend"}
	otherFrom := &model.Profile{ID: 22, Name: "Other"}
	otherTo := &model.Profile{ID: 23, Name: "Else"}

	lobby.AddChatMessage(&model.ChatMessage{
		From: broadcastFrom,
		Text: "hello all",
	})
	lobby.AddChatMessage(&model.ChatMessage{
		From:    privateFrom,
		To:      me,
		Text:    "private to me",
		Special: []byte{1, 2, 3, 4, 5},
	})
	lobby.AddChatMessage(&model.ChatMessage{
		From:    me,
		To:      otherTo,
		Text:    "private from me",
		Special: "ignored",
	})
	lobby.AddChatMessage(&model.ChatMessage{
		From: otherFrom,
		To:   otherTo,
		Text: "hidden private",
	})

	sendChatHistory(lobby, s)

	if len(cap.sends) != 3 {
		t.Fatalf("len(cap.sends) = %d, want 3", len(cap.sends))
	}
	if cap.sends[0].id != 0x4402 || cap.sends[1].id != 0x4402 || cap.sends[2].id != 0x4402 {
		t.Fatalf("unexpected packet ids: %+v", cap.sends)
	}
	if got := string(cap.sends[0].data[1:5]); got != string([]byte{0, 0, 0, 0}) {
		t.Fatalf("broadcast special = %v, want zeros", cap.sends[0].data[1:5])
	}
	if fromID := int32(binary.BigEndian.Uint32(cap.sends[0].data[5:9])); fromID != 20 {
		t.Fatalf("broadcast fromID = %d, want 20", fromID)
	}
	if got := string(cap.sends[1].data[1:5]); got != string([]byte{1, 2, 3, 4}) {
		t.Fatalf("private special = %v, want [1 2 3 4]", cap.sends[1].data[1:5])
	}
	if fromID := int32(binary.BigEndian.Uint32(cap.sends[1].data[5:9])); fromID != 21 {
		t.Fatalf("private fromID = %d, want 21", fromID)
	}
	if got := string(cap.sends[2].data[1:5]); got != string([]byte{0, 0, 0, 0}) {
		t.Fatalf("fallback special = %v, want zeros", cap.sends[2].data[1:5])
	}
	if fromID := int32(binary.BigEndian.Uint32(cap.sends[2].data[5:9])); fromID != 10 {
		t.Fatalf("sender-visible fromID = %d, want 10", fromID)
	}
}

func TestFormatProfileInfo_ShowStatsTrue_EncodesAllFields(t *testing.T) {
	profile := &model.Profile{
		ID:          11,
		Name:        "Bravo",
		Points:      700,
		Disconnects: 9,
		FavTeam:     77,
		FavPlayer:   555,
		Rank:        4,
	}
	stats := &model.Stats{
		Wins:          10,
		Losses:        2,
		Draws:         3,
		StreakCurrent: 4,
		StreakBest:    8,
		GoalsScored:   22,
		GoalsAllowed:  9,
	}

	got := formatProfileInfo(profile, stats, true)

	if len(got) != 57 {
		t.Fatalf("len(got) = %d, want 57", len(got))
	}
	if id := int32(binary.BigEndian.Uint32(got[0:4])); id != 11 {
		t.Fatalf("id = %d, want 11", id)
	}
	if name := string(got[4:9]); name != "Bravo" {
		t.Fatalf("name prefix = %q, want %q", name, "Bravo")
	}
	if got[20] != 3 {
		t.Fatalf("division = %d, want 3", got[20])
	}
	if points := int32(binary.BigEndian.Uint32(got[21:25])); points != 700 {
		t.Fatalf("points = %d, want 700", points)
	}
	if games := binary.BigEndian.Uint16(got[25:27]); games != 15 {
		t.Fatalf("games = %d, want 15", games)
	}
	if wins := binary.BigEndian.Uint16(got[27:29]); wins != 10 {
		t.Fatalf("wins = %d, want 10", wins)
	}
	if losses := binary.BigEndian.Uint16(got[29:31]); losses != 2 {
		t.Fatalf("losses = %d, want 2", losses)
	}
	if draws := binary.BigEndian.Uint16(got[31:33]); draws != 3 {
		t.Fatalf("draws = %d, want 3", draws)
	}
	if streakCurrent := binary.BigEndian.Uint16(got[33:35]); streakCurrent != 4 {
		t.Fatalf("streakCurrent = %d, want 4", streakCurrent)
	}
	if streakBest := binary.BigEndian.Uint16(got[35:37]); streakBest != 8 {
		t.Fatalf("streakBest = %d, want 8", streakBest)
	}
	if disconnects := binary.BigEndian.Uint16(got[37:39]); disconnects != 9 {
		t.Fatalf("disconnects = %d, want 9", disconnects)
	}
	if goalsScored := binary.BigEndian.Uint16(got[41:43]); goalsScored != 22 {
		t.Fatalf("goalsScored = %d, want 22", goalsScored)
	}
	if goalsAllowed := binary.BigEndian.Uint16(got[45:47]); goalsAllowed != 9 {
		t.Fatalf("goalsAllowed = %d, want 9", goalsAllowed)
	}
	if favTeam := binary.BigEndian.Uint16(got[47:49]); favTeam != 77 {
		t.Fatalf("favTeam = %d, want 77", favTeam)
	}
	if favPlayer := int32(binary.BigEndian.Uint32(got[49:53])); favPlayer != 555 {
		t.Fatalf("favPlayer = %d, want 555", favPlayer)
	}
	if rank := int32(binary.BigEndian.Uint32(got[53:57])); rank != 4 {
		t.Fatalf("rank = %d, want 4", rank)
	}
}

func TestFormatProfileInfo_ShowStatsFalse_PristineProfileParity(t *testing.T) {
	profile := &model.Profile{
		ID:          11,
		Name:        "Bravo",
		Points:      700,
		Disconnects: 9,
		FavTeam:     77,
		FavPlayer:   555,
		Rank:        4,
	}
	stats := &model.Stats{
		Wins:          10,
		Losses:        2,
		Draws:         3,
		StreakCurrent: 4,
		StreakBest:    8,
		GoalsScored:   22,
		GoalsAllowed:  9,
	}

	got := formatProfileInfo(profile, stats, false)

	if len(got) != 57 {
		t.Fatalf("len(got) = %d, want 57", len(got))
	}
	if got[20] != 0 {
		t.Fatalf("division = %d, want 0 when showStats=false", got[20])
	}
	if points := int32(binary.BigEndian.Uint32(got[21:25])); points != 0 {
		t.Fatalf("points = %d, want 0 when showStats=false", points)
	}
	if games := binary.BigEndian.Uint16(got[25:27]); games != 0 {
		t.Fatalf("games = %d, want 0 when showStats=false", games)
	}
	if wins := binary.BigEndian.Uint16(got[27:29]); wins != 0 {
		t.Fatalf("wins = %d, want 0 when showStats=false", wins)
	}
	if losses := binary.BigEndian.Uint16(got[29:31]); losses != 0 {
		t.Fatalf("losses = %d, want 0 when showStats=false", losses)
	}
	if draws := binary.BigEndian.Uint16(got[31:33]); draws != 0 {
		t.Fatalf("draws = %d, want 0 when showStats=false", draws)
	}
	if streakCurrent := binary.BigEndian.Uint16(got[33:35]); streakCurrent != 0 {
		t.Fatalf("streakCurrent = %d, want 0 when showStats=false", streakCurrent)
	}
	if streakBest := binary.BigEndian.Uint16(got[35:37]); streakBest != 0 {
		t.Fatalf("streakBest = %d, want 0 when showStats=false", streakBest)
	}
	if disconnects := binary.BigEndian.Uint16(got[37:39]); disconnects != 0 {
		t.Fatalf("disconnects = %d, want 0 when showStats=false", disconnects)
	}
	if goalsScored := binary.BigEndian.Uint16(got[41:43]); goalsScored != 0 {
		t.Fatalf("goalsScored = %d, want 0 when showStats=false", goalsScored)
	}
	if goalsAllowed := binary.BigEndian.Uint16(got[45:47]); goalsAllowed != 0 {
		t.Fatalf("goalsAllowed = %d, want 0 when showStats=false", goalsAllowed)
	}
	if favTeam := binary.BigEndian.Uint16(got[47:49]); favTeam != 0 {
		t.Fatalf("favTeam = %d, want 0 when showStats=false", favTeam)
	}
	if favPlayer := int32(binary.BigEndian.Uint32(got[49:53])); favPlayer != 0 {
		t.Fatalf("favPlayer = %d, want 0 when showStats=false", favPlayer)
	}
	if rank := int32(binary.BigEndian.Uint32(got[53:57])); rank != 0 {
		t.Fatalf("rank = %d, want 0 when showStats=false", rank)
	}
}
