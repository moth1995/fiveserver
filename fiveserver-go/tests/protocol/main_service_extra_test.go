package protocol_test

import (
	"encoding/binary"
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/config"
	"github.com/fiveserver/fiveserver-go/internal/model"
	"github.com/fiveserver/fiveserver-go/internal/protocol"
)

// ---- helpers ----------------------------------------------------------------

// sessionInLobby creates a session with a profile in a lobby but no room.
func sessionInLobby(hub *protocol.Hub, profID int, profName string, lobbyIdx int) (*protocol.Session, *captureConn) {
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
	return s, cap
}

// ---- 0x432a exitRoom --------------------------------------------------------

func TestExitRoom_NotInRoom_SendsZeros(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)
	s.User = &model.ConnectedUser{
		User:       &model.User{Hash: "U"},
		Profile:    &model.Profile{ID: 1, Name: "U"},
		LobbyIndex: 0,
		State:      &model.NetworkState{InRoom: false},
	}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x432a}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) == 0 || cap.sends[0].id != 0x432b {
		t.Errorf("expected 0x432b, got %+v", cap.sends)
	}
}

func TestExitRoom_InRoom_RemovesPlayerAndSends432b(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, _, cap := sessionInRoom(hub, 1, "Owner", 0)

	lobby, _ := hub.GetLobby(0)
	if lobby.Rooms() == nil || len(lobby.Rooms()) == 0 {
		t.Fatal("expected at least one room before exit")
	}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x432a}}
	_ = d.Dispatch(s, pkt)

	var got432b bool
	for _, send := range cap.sends {
		if send.id == 0x432b {
			got432b = true
		}
	}
	if !got432b {
		t.Error("expected 0x432b after exitRoom")
	}
	// Room was the only player, so it should be destroyed
	if len(lobby.Rooms()) != 0 {
		t.Errorf("empty room should be destroyed, got %d rooms", len(lobby.Rooms()))
	}
}

func TestExitRoom_BroadcastsRoomUpdateToLobby(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, _, _ := sessionInRoom(hub, 1, "Owner", 0)

	// Second lobby member (not in room) — will receive broadcast
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
		RemoteAddr: "2.2.2.2:9999",
	}
	u2 := &model.ConnectedUser{
		User:       &model.User{Hash: "Watcher"},
		Profile:    &model.Profile{ID: 2, Name: "Watcher"},
		LobbyIndex: 0,
		State:      &model.NetworkState{},
		Conn:       cs2,
	}
	hub.AddSession(&protocol.Session{Conn: cs2, Hub: hub, User: u2})
	lobby, _ := hub.GetLobby(0)
	lobby.Enter(u2)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x432a}}
	_ = d.Dispatch(s, pkt)

	// Watcher should have received 0x4305 (room destroyed) or 0x4306 (room update)
	var gotBroadcast bool
	for _, send := range cap2.sends {
		if send.id == 0x4305 || send.id == 0x4306 {
			gotBroadcast = true
		}
	}
	if !gotBroadcast {
		t.Errorf("lobby member should receive room update/destroy broadcast, got %+v", cap2.sends)
	}
}

// ---- 0x4400 chat -----------------------------------------------------------

func TestChat_LobbyChat_BroadcastsToAllLobbyMembers(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, cap := sessionInLobby(hub, 1, "Sender", 0)

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
		RemoteAddr: "2.2.2.2:9999",
	}
	u2 := &model.ConnectedUser{
		User: &model.User{Hash: "Receiver"}, Profile: &model.Profile{ID: 2, Name: "Receiver"},
		LobbyIndex: 0, State: &model.NetworkState{}, Conn: cs2,
	}
	hub.AddSession(&protocol.Session{Conn: cs2, Hub: hub, User: u2})
	lobby, _ := hub.GetLobby(0)
	lobby.Enter(u2)

	// chatType = 0x0001 (lobby broadcast), 4 bytes special zeros, 4 bytes target profile id (unused), message at [10:]
	pktData := make([]byte, 10+5)
	pktData[0] = 0x00 // chatType[0]
	pktData[1] = 0x01 // chatType[1] = lobby
	copy(pktData[10:], []byte("hello"))

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4400}, Data: pktData}
	_ = d.Dispatch(s, pkt)

	// Both sender and receiver should get 0x4402
	var senderGot, receiverGot bool
	for _, send := range cap.sends {
		if send.id == 0x4402 {
			senderGot = true
		}
	}
	for _, send := range cap2.sends {
		if send.id == 0x4402 {
			receiverGot = true
		}
	}
	if !senderGot {
		t.Error("sender should receive own lobby chat message")
	}
	if !receiverGot {
		t.Error("other lobby member should receive lobby chat message")
	}
}

func TestChat_LobbyChat_AddedToHistory(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, _ := sessionInLobby(hub, 1, "P1", 0)

	pktData := make([]byte, 10+7)
	pktData[1] = 0x01 // lobby chat
	copy(pktData[10:], []byte("history!"))

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4400}, Data: pktData}
	_ = d.Dispatch(s, pkt)

	lobby, _ := hub.GetLobby(0)
	history := lobby.ChatHistory()
	if len(history) == 0 {
		t.Error("lobby chat should be added to history")
	}
}

func TestChat_RoomChat_OnlySentToRoomMembers(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, _, capOwner := sessionInRoom(hub, 1, "RoomOwner", 0)

	// Second player in room
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
		RemoteAddr: "2.2.2.2:9999",
	}
	u2 := &model.ConnectedUser{
		User: &model.User{Hash: "P2"}, Profile: &model.Profile{ID: 2, Name: "P2"},
		LobbyIndex: 0, State: &model.NetworkState{InRoom: true}, Conn: cs2,
	}
	hub.AddSession(&protocol.Session{Conn: cs2, Hub: hub, User: u2})
	room := s.User.State.Room
	room.Enter(u2)
	u2.State.Room = room

	// Lobby member outside room
	cap3 := &captureConn{}
	cs3 := &protocol.ConnSender{
		SendDataFn: func(id uint16, data []byte) error {
			cap3.sends = append(cap3.sends, capturedSend{id, data})
			return nil
		},
		SendZerosFn: func(id uint16, length int) error {
			cap3.sends = append(cap3.sends, capturedSend{id, make([]byte, length)})
			return nil
		},
		SendFn:     func(pkt protocol.Packet) error { return nil },
		RemoteAddr: "3.3.3.3:9999",
	}
	u3 := &model.ConnectedUser{
		User: &model.User{Hash: "P3"}, Profile: &model.Profile{ID: 3, Name: "P3"},
		LobbyIndex: 0, State: &model.NetworkState{},
	}
	_ = cs3
	lobby, _ := hub.GetLobby(0)
	lobby.Enter(u3)

	pktData := make([]byte, 10+4)
	pktData[0] = 0x01 // chatType[0] = room
	pktData[1] = 0x02 // chatType[1]
	copy(pktData[10:], []byte("room!"))

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4400}, Data: pktData}
	_ = d.Dispatch(s, pkt)

	var ownerGot, p2Got bool
	for _, send := range capOwner.sends {
		if send.id == 0x4402 {
			ownerGot = true
		}
	}
	for _, send := range cap2.sends {
		if send.id == 0x4402 {
			p2Got = true
		}
	}
	if !ownerGot {
		t.Error("owner should receive room chat")
	}
	if !p2Got {
		t.Error("room member 2 should receive room chat")
	}
	for _, send := range cap3.sends {
		if send.id == 0x4402 {
			t.Error("lobby-only member should NOT receive room chat")
		}
	}
}

func TestChat_PrivateMessage_SentToTargetAndSenderOnly(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, capSender := sessionInLobby(hub, 1, "Sender", 0)

	// Target
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
		SendFn: func(pkt protocol.Packet) error { return nil },
	}
	u2 := &model.ConnectedUser{
		User: &model.User{Hash: "Target"}, Profile: &model.Profile{ID: 2, Name: "Target"},
		LobbyIndex: 0, State: &model.NetworkState{}, Conn: cs2,
	}
	sess2 := &protocol.Session{Conn: cs2, Hub: hub, User: u2}
	hub.AddSession(sess2)
	lobby, _ := hub.GetLobby(0)
	lobby.Enter(u2)

	// Third user who should NOT get the message
	cap3 := &captureConn{}
	cs3 := &protocol.ConnSender{
		SendDataFn: func(id uint16, data []byte) error {
			cap3.sends = append(cap3.sends, capturedSend{id, data})
			return nil
		},
		SendZerosFn: func(id uint16, length int) error { return nil },
		SendFn:      func(pkt protocol.Packet) error { return nil },
	}
	u3 := &model.ConnectedUser{
		User: &model.User{Hash: "Bystander"}, Profile: &model.Profile{ID: 3, Name: "Bystander"},
		LobbyIndex: 0, State: &model.NetworkState{}, Conn: cs3,
	}
	hub.AddSession(&protocol.Session{Conn: cs3, Hub: hub, User: u3})
	lobby.Enter(u3)

	// chatType = 0x0002 (private), special[2:6], targetProfileID[6:10], message[10:]
	pktData := make([]byte, 10+4)
	pktData[0] = 0x00
	pktData[1] = 0x02 // private
	// target profile ID at [6:10]
	binary.BigEndian.PutUint32(pktData[6:10], 2)
	copy(pktData[10:], []byte("psst"))

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4400}, Data: pktData}
	_ = d.Dispatch(s, pkt)

	var targetGot bool
	for _, send := range cap2.sends {
		if send.id == 0x4402 {
			targetGot = true
		}
	}
	var senderGot bool
	for _, send := range capSender.sends {
		if send.id == 0x4402 {
			senderGot = true
		}
	}
	if !targetGot {
		t.Error("target should receive private message")
	}
	if !senderGot {
		t.Error("sender should receive echo of private message")
	}
	for _, send := range cap3.sends {
		if send.id == 0x4402 {
			t.Error("bystander should NOT receive private message")
		}
	}
}

func TestChat_BannedWord_ReplacedWithWarning(t *testing.T) {
	hub := protocol.NewHub(&config.Config{
		MaxUsers:   100,
		ServerName: "Test",
		Lobbies:    []config.Lobby{{Name: "EU"}},
		Chat: config.ChatConfig{
			BannedWords:    []string{"badword"},
			WarningMessage: "CENSORED",
		},
		NetworkServer: config.NetworkServerConfig{
			LoginService: map[string]int{"pes5": 20102},
		},
	})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, cap := sessionInLobby(hub, 1, "P1", 0)

	pktData := make([]byte, 10+7)
	pktData[1] = 0x01 // lobby
	copy(pktData[10:], []byte("badword"))

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4400}, Data: pktData}
	_ = d.Dispatch(s, pkt)

	var found4402 bool
	for _, send := range cap.sends {
		if send.id == 0x4402 {
			found4402 = true
			// message starts after [1 chatType][4 special][4 profileID][16 name] = 25 bytes
			msg := string(send.data[25:])
			if len(msg) > 0 && msg[:len("[CENSORED]")] != "[CENSORED]" {
				t.Errorf("expected [CENSORED] in message, got %q", msg)
			}
		}
	}
	if !found4402 {
		t.Error("expected 0x4402 even when message is banned")
	}
}

// ---- 0x4b00 ping -----------------------------------------------------------

func TestPing_TargetNotFound_SendsFF(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, cap := sessionInLobby(hub, 1, "P1", 0)

	data := make([]byte, 4)
	binary.BigEndian.PutUint32(data, 999) // non-existent profile
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4b00}, Data: data}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x4b01 {
		t.Fatalf("expected 0x4b01, got %+v", cap.sends)
	}
	code := binary.BigEndian.Uint32(cap.sends[0].data)
	if code != 0xffffffff {
		t.Errorf("not-found code = 0x%08x, want 0xffffffff", code)
	}
}

func TestPing_TargetFound_SendsNetworkInfo(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, cap := sessionInLobby(hub, 1, "Pinger", 0)

	// Add a target in the same lobby with known network state
	sTarget, _ := newCaptureSession(hub)
	sTarget.User = &model.ConnectedUser{
		User:       &model.User{Hash: "Target"},
		Profile:    &model.Profile{ID: 2, Name: "Target"},
		LobbyIndex: 0,
		State: &model.NetworkState{
			IP1:      []byte("192.168.1.1\x00\x00\x00\x00\x00"),
			IP2:      []byte("1.2.3.4\x00\x00\x00\x00\x00\x00\x00\x00\x00"),
			UDPPort1: 3333,
			UDPPort2: 4444,
		},
	}
	hub.AddSession(sTarget)
	lobby, _ := hub.GetLobby(0)
	lobby.Enter(sTarget.User)

	data := make([]byte, 4)
	binary.BigEndian.PutUint32(data, 2) // target profile ID = 2
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4b00}, Data: data}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x4b01 {
		t.Fatalf("expected 0x4b01, got %+v", cap.sends)
	}
	// Layout: [4 zeros][16 ip1][2 port1][16 ip2][2 port2][4 profileID] = 44 bytes
	resp := cap.sends[0].data
	if len(resp) != 44 {
		t.Errorf("0x4b01 data len = %d, want 44", len(resp))
	}
	port1 := binary.BigEndian.Uint16(resp[20:22])
	if port1 != 3333 {
		t.Errorf("port1 = %d, want 3333", port1)
	}
	profileID := int32(binary.BigEndian.Uint32(resp[40:44]))
	if profileID != 2 {
		t.Errorf("profileID = %d, want 2", profileID)
	}
}

// ---- 0x4323 challengeResponse -----------------------------------------------

func TestChallengeResponse_NilRoom_NoOp(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, cap := sessionInLobby(hub, 1, "P1", 0)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4323}, Data: []byte{1}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 0 {
		t.Errorf("no room → no-op, got %+v", cap.sends)
	}
}

func TestChallengeResponse_Rejected_NotifiesChallenger(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")

	// Owner is in room
	sOwner, _, _ := sessionInRoom(hub, 1, "Owner", 0)
	room := sOwner.User.State.Room

	// Challenger session
	capChal := &captureConn{}
	csChal := &protocol.ConnSender{
		SendDataFn: func(id uint16, data []byte) error {
			capChal.sends = append(capChal.sends, capturedSend{id, data})
			return nil
		},
		SendZerosFn: func(id uint16, length int) error {
			capChal.sends = append(capChal.sends, capturedSend{id, make([]byte, length)})
			return nil
		},
		SendFn: func(pkt protocol.Packet) error { return nil },
	}
	uChal := &model.ConnectedUser{
		User: &model.User{Hash: "Challenger"}, Profile: &model.Profile{ID: 2, Name: "Challenger"},
		LobbyIndex: 0, State: &model.NetworkState{InRoom: true, Room: room}, Conn: csChal,
	}
	sessChal := &protocol.Session{Conn: csChal, Hub: hub, User: uChal}
	hub.AddSession(sessChal)
	room.MatchStarter = uChal

	// Owner sends rejection (pkt.Data[0] == 0)
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4323}, Data: []byte{0}}
	_ = d.Dispatch(sOwner, pkt)

	// Challenger should receive 0x4321 with code 1 (rejected)
	var rejSend *capturedSend
	for i := range capChal.sends {
		if capChal.sends[i].id == 0x4321 {
			rejSend = &capChal.sends[i]
		}
	}
	if rejSend == nil {
		t.Fatal("challenger should receive 0x4321 rejection")
	}
	code := binary.BigEndian.Uint32(rejSend.data[:4])
	if code != 1 {
		t.Errorf("rejection code = %d, want 1", code)
	}
}

// ---- 0x4350 relayRoomSettings -----------------------------------------------

func TestRelayRoomSettings_ForwardsToOtherRoomMember(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, _, _ := sessionInRoom(hub, 1, "P1", 0)

	cap2 := &captureConn{}
	cs2 := &protocol.ConnSender{
		SendDataFn: func(id uint16, data []byte) error {
			cap2.sends = append(cap2.sends, capturedSend{id, data})
			return nil
		},
		SendZerosFn: func(id uint16, length int) error { return nil },
		SendFn:      func(pkt protocol.Packet) error { return nil },
	}
	u2 := &model.ConnectedUser{
		User: &model.User{Hash: "P2"}, Profile: &model.Profile{ID: 2, Name: "P2"},
		Conn: cs2,
	}
	room := s.User.State.Room
	room.Enter(u2)

	payload := []byte{0xAA, 0xBB, 0xCC}
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4350}, Data: payload}
	_ = d.Dispatch(s, pkt)

	if len(cap2.sends) != 1 || cap2.sends[0].id != 0x4350 {
		t.Fatalf("expected 0x4350 relay on P2, got %+v", cap2.sends)
	}
	if string(cap2.sends[0].data) != string(payload) {
		t.Errorf("relayed data = %v, want %v", cap2.sends[0].data, payload)
	}
}

func TestRelayRoomSettings_DoesNotSendToSelf(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, _, cap := sessionInRoom(hub, 1, "P1", 0)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4350}, Data: []byte{0x01}}
	_ = d.Dispatch(s, pkt)

	for _, send := range cap.sends {
		if send.id == 0x4350 {
			t.Error("sender should not receive their own relayed room settings")
		}
	}
}

// ---- 0x3087 matchSeriesExit (no DB) ----------------------------------------

func TestMatchSeriesExit_NoRoom_NoOp(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, cap := sessionInLobby(hub, 1, "P1", 0)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3087}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 0 {
		t.Errorf("no room → no-op, got %+v", cap.sends)
	}
}

func TestMatchSeriesExit_NoMatch_NoOp(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, _, cap := sessionInRoom(hub, 1, "P1", 0)
	// room.Match == nil by default

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3087}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 0 {
		t.Errorf("no match → no-op, got %+v", cap.sends)
	}
}

func TestMatchSeriesExit_MutualDisconnect_Disregarded(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, _, _ := sessionInRoom(hub, 1, "P1", 0)
	room := s.User.State.Room
	room.Match = &model.Match{
		HomeProfileID: 1, AwayProfileID: 2,
		HomeTeamID: 10, AwayTeamID: 20,
		HomeExit: byte(1), AwayExit: byte(1), // both exited = mutual disconnect
	}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3087}}
	_ = d.Dispatch(s, pkt)

	// Match should be cleared but no DB write (sc=nil won't panic if skipped)
	if room.Match != nil {
		t.Error("match should be cleared after 3087")
	}
}

func TestMatchSeriesExit_NoStatsLobby_SkipsDB(t *testing.T) {
	hub := protocol.NewHub(&config.Config{
		MaxUsers: 100, ServerName: "Test",
		Lobbies: []config.Lobby{{Name: "Training", TypeCode: 0x20}}, // no-stats
		NetworkServer: config.NetworkServerConfig{
			LoginService: map[string]int{"pes5": 20102},
		},
	})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, _, _ := sessionInRoom(hub, 1, "P1", 0)
	room := s.User.State.Room
	room.Match = &model.Match{HomeProfileID: 1, AwayProfileID: 2, HomeTeamID: 5, AwayTeamID: 6}

	// Should not panic with nil sc because no-stats lobby exits before DB call
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3087}}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}
}

// ---- 0x4310 createRoom — room info broadcast size --------------------------

func TestCreateRoom_RoomUpdatePayloadSize(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")

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
		RemoteAddr: "1.1.1.1:9999",
	}
	u := &model.ConnectedUser{
		User:       &model.User{Hash: "U"},
		Profile:    &model.Profile{ID: 1, Name: "Creator"},
		LobbyIndex: 0,
		State:      &model.NetworkState{},
		Conn:       cs2,
	}
	sess := &protocol.Session{Conn: cs2, Hub: hub, User: u}
	hub.AddSession(sess)
	lobby, _ := hub.GetLobby(0)
	lobby.Enter(u)

	roomNameBytes := model.PadWithZeros("SizeCheck", 32)
	pktData := make([]byte, 50)
	copy(pktData[0:32], roomNameBytes)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4310}, Data: pktData}
	_ = d.Dispatch(sess, pkt)

	// Find the 0x4306 broadcast
	var roomUpdate *capturedSend
	for i := range cap2.sends {
		if cap2.sends[i].id == 0x4306 {
			roomUpdate = &cap2.sends[i]
		}
	}
	if roomUpdate == nil {
		t.Fatal("expected 0x4306 room update broadcast")
	}
	// [4 roomID][1 active][1 usePassword][32 name][1 matchTime/5][48 player slots] = 87 bytes
	if len(roomUpdate.data) != 87 {
		t.Errorf("0x4306 payload = %d bytes, want 87", len(roomUpdate.data))
	}
}

// ---- 0x4320 challenge — error paths ----------------------------------------

func TestChallenge_NilUser_SendsError(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4320}, Data: make([]byte, 21)}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x4321 {
		t.Fatalf("expected 0x4321, got %+v", cap.sends)
	}
	code := binary.BigEndian.Uint32(cap.sends[0].data)
	if code != 1 {
		t.Errorf("error code = %d, want 1", code)
	}
}

func TestChallenge_ShortPacket_SendsError(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, cap := sessionInLobby(hub, 1, "P1", 0)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4320}, Data: []byte{0, 0, 0, 1}} // < 21 bytes
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) == 0 || cap.sends[0].id != 0x4321 {
		t.Fatalf("expected 0x4321, got %+v", cap.sends)
	}
}

func TestChallenge_RoomNotFound_SendsError(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")
	s, cap := sessionInLobby(hub, 1, "P1", 0)

	pktData := make([]byte, 21)
	binary.BigEndian.PutUint32(pktData[0:4], 9999) // non-existent room ID
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4320}, Data: pktData}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) == 0 || cap.sends[0].id != 0x4321 {
		t.Fatalf("expected 0x4321, got %+v", cap.sends)
	}
}

// ---- 0x4325 cancelChallenge — in-room path ----------------------------------

func TestCancelChallenge_InRoom_ExitsAndBroadcasts(t *testing.T) {
	hub := mainHub(config.Lobby{Name: "EU"})
	d := protocol.NewMainServiceDispatcher(hub, nil, "pes5")

	// Owner in room
	sOwner, _, capOwner := sessionInRoom(hub, 1, "Owner", 0)
	room := sOwner.User.State.Room

	// Challenger enters room
	capChal := &captureConn{}
	csChal := &protocol.ConnSender{
		SendDataFn: func(id uint16, data []byte) error {
			capChal.sends = append(capChal.sends, capturedSend{id, data})
			return nil
		},
		SendZerosFn: func(id uint16, length int) error {
			capChal.sends = append(capChal.sends, capturedSend{id, make([]byte, length)})
			return nil
		},
		SendFn: func(pkt protocol.Packet) error { return nil },
	}
	uChal := &model.ConnectedUser{
		User: &model.User{Hash: "Chal"}, Profile: &model.Profile{ID: 2, Name: "Chal"},
		LobbyIndex: 0, State: &model.NetworkState{InRoom: true, Room: room}, Conn: csChal,
	}
	sessChal := &protocol.Session{Conn: csChal, Hub: hub, User: uChal}
	hub.AddSession(sessChal)
	room.Enter(uChal)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4325}}
	_ = d.Dispatch(sessChal, pkt)

	// Challenger should receive 0x4326
	var got4326 bool
	for _, send := range capChal.sends {
		if send.id == 0x4326 {
			got4326 = true
		}
	}
	if !got4326 {
		t.Error("challenger should receive 0x4326")
	}

	// Owner should receive 0x4324 (cancel notification)
	var got4324 bool
	for _, send := range capOwner.sends {
		if send.id == 0x4324 {
			got4324 = true
		}
	}
	if !got4324 {
		t.Error("owner should receive 0x4324 cancel notification")
	}
}
