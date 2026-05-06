package protocol_test

import (
	"encoding/binary"
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/config"
	"github.com/fiveserver/fiveserver-go/internal/model"
	"github.com/fiveserver/fiveserver-go/internal/protocol"
)

// ---- helpers ----------------------------------------------------------------

func mainHub(lobbies ...config.Lobby) *protocol.Hub {
	return protocol.NewHub(&config.Config{
		MaxUsers:   100,
		ServerName: "Test",
		Lobbies:    lobbies,
		NetworkServer: config.NetworkServerConfig{
			LoginService: map[string]int{"pes5": 20102},
		},
	})
}

func sessionInRoom(hub *protocol.Hub, profID int, profName string, lobbyIdx int) (*protocol.Session, *model.Room, *captureConn) {
	s, cap := newCaptureSession(hub)
	s.User = &model.ConnectedUser{
		User:       &model.User{Hash: profName},
		Profile:    &model.Profile{ID: profID, Name: profName},
		Profiles:   []*model.Profile{{ID: profID, Name: profName, Ordinal: 0}},
		LobbyIndex: lobbyIdx,
		State:      &model.NetworkState{},
	}
	hub.AddSession(s)

	lobby, _ := hub.GetLobby(lobbyIdx)
	lobby.Enter(s.User)
	room := model.NewRoom(lobby)
	room.Name = "TestRoom"
	room.Enter(s.User)
	lobby.AddRoom(room)
	return s, room, cap
}

// ---- MatchState in Hub ------------------------------------------------------

func TestHub_SetAndGetPendingMatch(t *testing.T) {
	hub := mainHub()
	home := &protocol.Session{}
	away := &protocol.Session{}
	ms := &protocol.MatchState{Home: home, Away: away}

	hub.SetPendingMatch(ms)

	got, ok := hub.PendingMatch(home)
	if !ok || got != ms {
		t.Errorf("PendingMatch(home) = %v, %v; want ms, true", got, ok)
	}

	got, ok = hub.PendingMatch(away)
	if !ok || got != ms {
		t.Errorf("PendingMatch(away) = %v, %v; want ms, true", got, ok)
	}
}

func TestHub_ClearPendingMatch_ByHome(t *testing.T) {
	hub := mainHub()
	home := &protocol.Session{}
	away := &protocol.Session{}
	hub.SetPendingMatch(&protocol.MatchState{Home: home, Away: away})

	hub.ClearPendingMatch(home)

	_, ok := hub.PendingMatch(home)
	if ok {
		t.Error("PendingMatch should return false after ClearPendingMatch")
	}
}

func TestHub_ClearPendingMatch_ByAway(t *testing.T) {
	hub := mainHub()
	home := &protocol.Session{}
	away := &protocol.Session{}
	hub.SetPendingMatch(&protocol.MatchState{Home: home, Away: away})

	hub.ClearPendingMatch(away)

	_, ok := hub.PendingMatch(away)
	if ok {
		t.Error("PendingMatch should return false after clearing by away session")
	}
}

func TestHub_PendingMatch_NotFound(t *testing.T) {
	hub := mainHub()
	s := &protocol.Session{}
	_, ok := hub.PendingMatch(s)
	if ok {
		t.Error("expected false for unknown session")
	}
}

// ---- 0x4310 createRoom -------------------------------------------------------

func TestCreateRoom_NewRoom_SendsZeros(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, _, cap := sessionInRoom(hub, 1, "Owner", 0)

	// Need to be NOT in a room already for room creation
	// Reset the session to lobby-only state
	s2, cap2 := newCaptureSession(hub)
	s2.User = &model.ConnectedUser{
		User:       &model.User{Hash: "Owner2"},
		Profile:    &model.Profile{ID: 2, Name: "Owner2"},
		LobbyIndex: 0,
		State:      &model.NetworkState{},
	}
	hub.AddSession(s2)
	lobby, _ := hub.GetLobby(0)
	lobby.Enter(s2.User)

	roomNameBytes := model.PadWithZeros("NewRoom", 32)
	pktData := make([]byte, 50)
	copy(pktData[0:32], roomNameBytes)
	pktData[32] = 0 // no match time set
	pktData[33] = 0 // no password

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4310}, Data: pktData}
	if err := d.Dispatch(s2, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}

	// Last send on s2's capture should be 0x4311
	var last *capturedSend
	for i := range cap2.sends {
		last = &cap2.sends[i]
	}
	if last == nil || last.id != 0x4311 {
		t.Errorf("expected 0x4311 as final send, got %+v", cap2.sends)
	}
	_ = s
	_ = cap
}

func TestCreateRoom_DuplicateName_SendsError(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")

	// First user creates a room
	s1, cap1 := newCaptureSession(hub)
	s1.User = &model.ConnectedUser{
		User:       &model.User{Hash: "U1"},
		Profile:    &model.Profile{ID: 1, Name: "U1"},
		LobbyIndex: 0,
		State:      &model.NetworkState{},
	}
	hub.AddSession(s1)
	lobby, _ := hub.GetLobby(0)
	lobby.Enter(s1.User)

	roomData := model.PadWithZeros("DupRoom", 32)
	pktData := make([]byte, 50)
	copy(pktData[0:32], roomData)
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4310}, Data: pktData}
	_ = d.Dispatch(s1, pkt)
	_ = cap1

	// Second user tries same room name
	s2, cap2 := newCaptureSession(hub)
	s2.User = &model.ConnectedUser{
		User:       &model.User{Hash: "U2"},
		Profile:    &model.Profile{ID: 2, Name: "U2"},
		LobbyIndex: 0,
		State:      &model.NetworkState{},
	}
	hub.AddSession(s2)
	lobby.Enter(s2.User)

	pkt2 := protocol.Packet{Header: protocol.Header{ID: 0x4310}, Data: pktData}
	_ = d.Dispatch(s2, pkt2)

	// Should receive 0x4311 with error code 0xffffff10
	var errSend *capturedSend
	for i := range cap2.sends {
		if cap2.sends[i].id == 0x4311 {
			errSend = &cap2.sends[i]
		}
	}
	if errSend == nil {
		t.Fatal("expected 0x4311 error response")
	}
	if len(errSend.data) < 4 {
		t.Fatal("error data too short")
	}
	code := binary.BigEndian.Uint32(errSend.data[0:4])
	if code != 0xffffff10 {
		t.Errorf("error code = 0x%08x, want 0xffffff10", code)
	}
}

// ---- 0x4364 setMatchTime ----------------------------------------------------

func TestSetMatchTime_UpdatesRoomAndSends4365(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, room, cap := sessionInRoom(hub, 1, "P1", 0)

	pkt := protocol.Packet{
		Header: protocol.Header{ID: 0x4364},
		Data:   []byte{3}, // 3 × 5 = 15 minutes
	}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}

	if room.MatchTime != 15 {
		t.Errorf("MatchTime = %d, want 15", room.MatchTime)
	}

	var got *capturedSend
	for i := range cap.sends {
		if cap.sends[i].id == 0x4365 {
			got = &cap.sends[i]
		}
	}
	if got == nil {
		t.Error("expected 0x4365 response")
	}
}

// ---- 0x4366 selectTeam ------------------------------------------------------

func TestSelectTeam_Owner_SetsHomeTeam(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, room, cap := sessionInRoom(hub, 1, "Owner", 0)

	pktData := make([]byte, 2)
	binary.BigEndian.PutUint16(pktData, 42) // team 42
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4366}, Data: pktData}
	_ = d.Dispatch(s, pkt)

	if room.Match == nil {
		t.Fatal("Match should be created")
	}
	if room.Match.HomeTeamID != 42 {
		t.Errorf("HomeTeamID = %d, want 42", room.Match.HomeTeamID)
	}

	var response *capturedSend
	for i := range cap.sends {
		if cap.sends[i].id == 0x4367 {
			response = &cap.sends[i]
		}
	}
	if response == nil {
		t.Error("expected 0x4367 response")
	}
}

// ---- 0x4368 goalScored -------------------------------------------------------

func TestGoalScored_HomeGoal_IncrementsScoreHome(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, room, _ := sessionInRoom(hub, 1, "P1", 0)
	room.Match = &model.Match{HomeProfileID: 1, AwayProfileID: 2}

	pkt := protocol.Packet{
		Header: protocol.Header{ID: 0x4368},
		Data:   []byte{0}, // 0 = home goal
	}
	_ = d.Dispatch(s, pkt)

	if room.Match.ScoreHome != 1 {
		t.Errorf("ScoreHome = %d, want 1", room.Match.ScoreHome)
	}
	if room.Match.ScoreAway != 0 {
		t.Errorf("ScoreAway = %d, want 0", room.Match.ScoreAway)
	}
}

func TestGoalScored_AwayGoal_IncrementsScoreAway(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, room, _ := sessionInRoom(hub, 1, "P1", 0)
	room.Match = &model.Match{HomeProfileID: 1, AwayProfileID: 2}

	pkt := protocol.Packet{
		Header: protocol.Header{ID: 0x4368},
		Data:   []byte{1}, // 1 = away goal
	}
	_ = d.Dispatch(s, pkt)

	if room.Match.ScoreAway != 1 {
		t.Errorf("ScoreAway = %d, want 1", room.Match.ScoreAway)
	}
}

// ---- 0x4370 matchExit -------------------------------------------------------

func TestMatchExit_SetsHomeExit(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, room, cap := sessionInRoom(hub, 1, "P1", 0)
	room.Match = &model.Match{}

	pkt := protocol.Packet{
		Header: protocol.Header{ID: 0x4370},
		Data:   []byte{0, 1}, // side=0 (home), exitType=1
	}
	_ = d.Dispatch(s, pkt)

	exitVal, ok := room.Match.HomeExit.(byte)
	if !ok || exitVal != 1 {
		t.Errorf("HomeExit = %v, want byte(1)", room.Match.HomeExit)
	}

	var found bool
	for _, send := range cap.sends {
		if send.id == 0x4371 {
			found = true
		}
	}
	if !found {
		t.Error("expected 0x4371 response")
	}
}

// ---- 0x4325 cancelChallenge -------------------------------------------------

func TestCancelChallenge_NotInRoom_SendsZeros(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)
	s.User = &model.ConnectedUser{
		User:       &model.User{Hash: "U"},
		Profile:    &model.Profile{ID: 1, Name: "U"},
		LobbyIndex: 0,
		State:      &model.NetworkState{InRoom: false},
	}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4325}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) == 0 || cap.sends[0].id != 0x4326 {
		t.Errorf("expected 0x4326, got %+v", cap.sends)
	}
}

// ---- 0x4360 toggleReady -----------------------------------------------------

func TestToggleReady_RelaysToOthers(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, room, cap := sessionInRoom(hub, 1, "P1", 0)

	// Add a second player to the room using ConnSender
	cap2 := &captureConn{}
	cs2 := &protocol.ConnSender{
		SendDataFn: func(id uint16, data []byte) error {
			cap2.sends = append(cap2.sends, capturedSend{id, data})
			return nil
		},
		SendZerosFn: func(id uint16, length int) error {
			cap2.sends = append(cap2.sends, capturedSend{id, make([]byte, length)})
			return nil
		},
		SendFn:     func(pkt protocol.Packet) error { return nil },
		RemoteAddr: "2.3.4.5:9999",
	}
	u2 := &model.ConnectedUser{
		User:       &model.User{Hash: "P2"},
		Profile:    &model.Profile{ID: 2, Name: "P2"},
		LobbyIndex: 0,
		State:      &model.NetworkState{},
		Conn:       cs2, // direct Conn for relay path
	}
	room.Enter(u2)

	pkt := protocol.Packet{
		Header: protocol.Header{ID: 0x4360},
		Data:   []byte{1}, // ready
	}
	_ = d.Dispatch(s, pkt)

	// s should receive 0x4361
	var got4361 bool
	for _, send := range cap.sends {
		if send.id == 0x4361 {
			got4361 = true
		}
	}
	if !got4361 {
		t.Error("expected 0x4361 on sender")
	}

	// u2 should receive 0x4362 relay
	var got4362 bool
	for _, send := range cap2.sends {
		if send.id == 0x4362 {
			got4362 = true
		}
	}
	if !got4362 {
		t.Error("expected 0x4362 relay on other player")
	}
}

// ---- main disconnect --------------------------------------------------------

func TestMainDisconnect_RemovesFromHubAndLobby(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, _, _ := sessionInRoom(hub, 5, "DCUser", 0)

	lobby, _ := hub.GetLobby(0)
	if lobby.PlayerCount() == 0 {
		t.Fatal("expected player in lobby before disconnect")
	}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x0003}}
	_ = d.Dispatch(s, pkt)

	if hub.OnlineCount() != 0 {
		t.Errorf("expected 0 online after disconnect, got %d", hub.OnlineCount())
	}
}
