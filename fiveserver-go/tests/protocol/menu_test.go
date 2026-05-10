package protocol_test

import (
	"encoding/binary"
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/config"
	"github.com/fiveserver/fiveserver-go/internal/model"
	"github.com/fiveserver/fiveserver-go/internal/protocol"
)

// ---- helpers ----------------------------------------------------------------

func menuHub(maxUsers int, lobbies ...config.Lobby) *protocol.Hub {
	return protocol.NewHub(&config.Config{
		MaxUsers:   maxUsers,
		ServerName: "Test",
		Lobbies:    lobbies,
		NetworkServer: config.NetworkServerConfig{
			LoginService: map[string]int{"pes5": 20102},
		},
	})
}

func sessionWithProfile(hub *protocol.Hub, profID int, profName string) (*protocol.Session, *captureConn) {
	s, cap := newCaptureSession(hub)
	s.User = &model.ConnectedUser{
		User:    &model.User{Hash: profName},
		Profile: &model.Profile{ID: profID, Name: profName},
		Profiles: []*model.Profile{
			{ID: profID, Name: profName, Ordinal: 0},
		},
		LobbyIndex: -1,
	}
	return s, cap
}

// ---- FilterChat tests -------------------------------------------------------

func TestFilterChat_NoBannedWords_ReturnsOriginal(t *testing.T) {
	got := protocol.FilterChat("hello world", []string{"badword"}, "WARNING")
	if got != "hello world" {
		t.Errorf("got %q, want %q", got, "hello world")
	}
}

func TestFilterChat_MatchedWord_ReturnsWarning(t *testing.T) {
	got := protocol.FilterChat("you are badword dude", []string{"badword"}, "BANNED")
	if got != "[BANNED]" {
		t.Errorf("got %q, want [BANNED]", got)
	}
}

func TestFilterChat_CaseInsensitive(t *testing.T) {
	got := protocol.FilterChat("BADWORD in uppercase", []string{"badword"}, "W")
	if got != "[W]" {
		t.Errorf("expected warning for case-insensitive match, got %q", got)
	}
}

func TestFilterChat_EmptyBannedList_ReturnsOriginal(t *testing.T) {
	got := protocol.FilterChat("anything goes", nil, "W")
	if got != "anything goes" {
		t.Errorf("got %q, want original", got)
	}
}

func TestFilterChat_MultipleWords_FirstMatch(t *testing.T) {
	got := protocol.FilterChat("spam message", []string{"ok", "spam", "bad"}, "WARN")
	if got != "[WARN]" {
		t.Errorf("expected warning, got %q", got)
	}
}

// ---- 0x4200 getLobbies -------------------------------------------------------

func TestGetLobbies_NoLobbies_SendsEmptyList(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)
	s.User = &model.ConnectedUser{User: &model.User{Hash: "u"}, Profile: &model.Profile{ID: 1, Name: "p"}}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4200}}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}
	if len(cap.sends) != 1 || cap.sends[0].id != 0x4201 {
		t.Fatalf("expected 0x4201, got %+v", cap.sends)
	}
	// First 2 bytes = count = 0
	data := cap.sends[0].data
	if len(data) < 2 {
		t.Fatal("response too short")
	}
	count := binary.BigEndian.Uint16(data[0:2])
	if count != 0 {
		t.Errorf("count = %d, want 0", count)
	}
}

func TestGetLobbies_WithLobbies_CorrectCount(t *testing.T) {
	hub := menuHub(100,
		config.Lobby{Name: "Europe", TypeCode: 0x5f},
		config.Lobby{Name: "America", TypeCode: 0x5f},
	)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)
	s.User = &model.ConnectedUser{User: &model.User{Hash: "u"}, Profile: &model.Profile{ID: 1, Name: "p"}}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4200}}
	_ = d.Dispatch(s, pkt)

	data := cap.sends[0].data
	count := binary.BigEndian.Uint16(data[0:2])
	if count != 2 {
		t.Errorf("count = %d, want 2", count)
	}
	// Each lobby entry = 35 bytes (1 typeCode + 32 name + 2 playerCount)
	if len(data) != 2+2*35 {
		t.Errorf("data length = %d, want %d", len(data), 2+2*35)
	}
}

// ---- 0x4202 selectLobby -----------------------------------------------------

func TestSelectLobby_ValidLobby_SendsZerosAndEntersLobby(t *testing.T) {
	hub := menuHub(100, config.Lobby{Name: "EU"})
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := sessionWithProfile(hub, 1, "Player1")

	// pkt.Data: [1 lobbyID][16 ip1][2 port1][16 ip2][2 port2]
	pktData := make([]byte, 37)
	pktData[0] = 0 // lobby index 0
	copy(pktData[1:17], []byte("192.168.1.1\x00\x00\x00\x00\x00"))
	binary.BigEndian.PutUint16(pktData[17:19], 3658)
	copy(pktData[19:35], []byte("1.2.3.4\x00\x00\x00\x00\x00\x00\x00\x00\x00"))
	binary.BigEndian.PutUint16(pktData[35:37], 3658)

	// Simulate post-0x3040: register session in hub so sendToUser can find it.
	hub.AddSession(s)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4202}, Data: pktData}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}

	// Should send 0x4203 (zeros) + 0x4220 (player info broadcast)
	if len(cap.sends) < 2 {
		t.Fatalf("expected >= 2 sends, got %d", len(cap.sends))
	}
	if cap.sends[0].id != 0x4203 {
		t.Errorf("send[0].id = 0x%04x, want 0x4203", cap.sends[0].id)
	}
	if cap.sends[1].id != 0x4220 {
		t.Errorf("send[1].id = 0x%04x, want 0x4220", cap.sends[1].id)
	}

	// LobbyIndex should be updated
	if s.User.LobbyIndex != 0 {
		t.Errorf("LobbyIndex = %d, want 0", s.User.LobbyIndex)
	}
	// NetworkState should be set
	if s.User.State == nil {
		t.Fatal("State should not be nil after selectLobby")
	}
	if s.User.State.UDPPort1 != 3658 {
		t.Errorf("UDPPort1 = %d, want 3658", s.User.State.UDPPort1)
	}
}

func TestSelectLobby_WithOptionalField_PreservesNetworkState(t *testing.T) {
	hub := menuHub(100, config.Lobby{Name: "EU"})
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, _ := sessionWithProfile(hub, 1, "Player1")
	hub.AddSession(s)

	pktData := make([]byte, 39)
	pktData[0] = 0
	copy(pktData[1:17], []byte("abcdefghijklmnop"))
	binary.BigEndian.PutUint16(pktData[17:19], 1111)
	copy(pktData[19:35], []byte("qrstuvwxyzABCDEF"))
	binary.BigEndian.PutUint16(pktData[35:37], 2222)
	binary.BigEndian.PutUint16(pktData[37:39], 3333)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4202}, Data: pktData}
	_ = d.Dispatch(s, pkt)

	if s.User.State == nil {
		t.Fatal("State should not be nil after selectLobby")
	}
	if s.User.State.UDPPort2 != 2222 {
		t.Errorf("UDPPort2 = %d, want 2222", s.User.State.UDPPort2)
	}
	if s.User.State.SomeField != 3333 {
		t.Errorf("SomeField = %d, want 3333", s.User.State.SomeField)
	}
	if string(s.User.State.IP1) != "abcdefghijklmnop" {
		t.Errorf("IP1 = %q, want %q", string(s.User.State.IP1), "abcdefghijklmnop")
	}
	if string(s.User.State.IP2) != "qrstuvwxyzABCDEF" {
		t.Errorf("IP2 = %q, want %q", string(s.User.State.IP2), "qrstuvwxyzABCDEF")
	}
}

func TestSelectLobby_InvalidLobbyIndex_NoOp(t *testing.T) {
	hub := menuHub(100) // no lobbies
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := sessionWithProfile(hub, 1, "Player1")

	pktData := make([]byte, 37)
	pktData[0] = 5 // lobby index out of range
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4202}, Data: pktData}
	_ = d.Dispatch(s, pkt)

	// Still sends 0x4203
	if len(cap.sends) < 1 || cap.sends[0].id != 0x4203 {
		t.Errorf("expected 0x4203, got %+v", cap.sends)
	}
}

// ---- 0x4210 getUserList -----------------------------------------------------

func TestGetUserList_NoLobby_SendsEmptySequence(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := sessionWithProfile(hub, 1, "P1")
	s.User.LobbyIndex = -1

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4210}}
	_ = d.Dispatch(s, pkt)

	// Expects 0x4211 + 0x4213 (no 0x4212 entries)
	if len(cap.sends) != 2 {
		t.Fatalf("expected 2 sends, got %d", len(cap.sends))
	}
	if cap.sends[0].id != 0x4211 {
		t.Errorf("send[0].id = 0x%04x, want 0x4211", cap.sends[0].id)
	}
	if cap.sends[1].id != 0x4213 {
		t.Errorf("send[1].id = 0x%04x, want 0x4213", cap.sends[1].id)
	}
}

func TestGetUserList_EncodesRoomAndChatFlags(t *testing.T) {
	hub := menuHub(100, config.Lobby{Name: "EU"})
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")

	s1, cap1 := sessionWithProfile(hub, 1, "Alpha")
	s2, _ := sessionWithProfile(hub, 2, "Beta")
	hub.AddSession(s1)
	hub.AddSession(s2)

	lobby, _ := hub.GetLobby(0)
	room := model.NewRoom(lobby)
	lobby.AddRoom(room)

	s1.User.LobbyIndex = 0
	s2.User.LobbyIndex = 0
	s2.User.State = &model.NetworkState{InRoom: true, NoLobbyChat: 5, Room: room}
	lobby.Enter(s1.User)
	lobby.Enter(s2.User)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4210}}
	_ = d.Dispatch(s1, pkt)

	var entry []byte
	for _, send := range cap1.sends {
		if send.id == 0x4212 && binary.BigEndian.Uint32(send.data[0:4]) == 2 {
			entry = send.data
			break
		}
	}
	if entry == nil {
		t.Fatal("expected a 0x4212 entry for profile 2")
	}
	if len(entry) != 31 {
		t.Fatalf("entry len = %d, want 31", len(entry))
	}
	if entry[20] != 1 {
		t.Errorf("inRoom flag = %d, want 1", entry[20])
	}
	if roomID := int32(binary.BigEndian.Uint32(entry[21:25])); roomID != int32(room.ID) {
		t.Errorf("roomID = %d, want %d", roomID, room.ID)
	}
	if noLobbyChat := int32(binary.BigEndian.Uint32(entry[25:29])); noLobbyChat != 5 {
		t.Errorf("noLobbyChat = %d, want 5", noLobbyChat)
	}
}

// ---- 0x4300 getRoomList -----------------------------------------------------

func TestGetRoomList_NoLobby_SendsEmptySequence(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := sessionWithProfile(hub, 1, "P1")
	s.User.LobbyIndex = 0 // out of range

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4300}}
	_ = d.Dispatch(s, pkt)

	// 0x4301 + 0x4303
	if len(cap.sends) != 2 {
		t.Fatalf("expected 2 sends (empty room list), got %d", len(cap.sends))
	}
	if cap.sends[0].id != 0x4301 {
		t.Errorf("send[0].id = 0x%04x, want 0x4301", cap.sends[0].id)
	}
	if cap.sends[1].id != 0x4303 {
		t.Errorf("send[1].id = 0x%04x, want 0x4303", cap.sends[1].id)
	}
}

func TestGetRoomList_EncodesPasswordAndPlayerIDs(t *testing.T) {
	hub := menuHub(100, config.Lobby{Name: "EU"})
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")

	s1, cap1 := sessionWithProfile(hub, 11, "P1")
	s2, _ := sessionWithProfile(hub, 22, "P2")
	hub.AddSession(s1)
	hub.AddSession(s2)

	lobby, _ := hub.GetLobby(0)
	s1.User.LobbyIndex = 0
	s2.User.LobbyIndex = 0
	lobby.Enter(s1.User)
	lobby.Enter(s2.User)

	room := model.NewRoom(lobby)
	room.Name = "Room1"
	room.UsePassword = true
	room.MatchTime = 15
	room.Enter(s1.User)
	room.Enter(s2.User)
	lobby.AddRoom(room)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4300}}
	_ = d.Dispatch(s1, pkt)

	var entry []byte
	for _, send := range cap1.sends {
		if send.id == 0x4302 {
			entry = send.data
			break
		}
	}
	if entry == nil {
		t.Fatal("expected a 0x4302 room entry")
	}
	if roomID := int32(binary.BigEndian.Uint32(entry[0:4])); roomID != int32(room.ID) {
		t.Errorf("roomID = %d, want %d", roomID, room.ID)
	}
	if entry[5] != 1 {
		t.Errorf("password flag = %d, want 1", entry[5])
	}
	if entry[38] != 3 {
		t.Errorf("match time byte = %d, want 3", entry[38])
	}
	if playerID := int32(binary.BigEndian.Uint32(entry[39:43])); playerID != 11 {
		t.Errorf("first player ID = %d, want 11", playerID)
	}
	if playerID := int32(binary.BigEndian.Uint32(entry[43:47])); playerID != 22 {
		t.Errorf("second player ID = %d, want 22", playerID)
	}
}

// ---- 0x3080 stub ------------------------------------------------------------

func TestDo3080_SendsTwoPackets(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3080}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 2 {
		t.Fatalf("expected 2 sends, got %d", len(cap.sends))
	}
	if cap.sends[0].id != 0x3082 {
		t.Errorf("send[0].id = 0x%04x, want 0x3082", cap.sends[0].id)
	}
	if cap.sends[1].id != 0x3086 {
		t.Errorf("send[1].id = 0x%04x, want 0x3086", cap.sends[1].id)
	}
}

// ---- 0x4580 / 0x4600 / 0x4780 stubs ----------------------------------------

func TestGetFriends_SendsThreePackets(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4580}}
	_ = d.Dispatch(s, pkt)

	// Python sends 0x4581 → 0x4582 (empty) → 0x4583
	if len(cap.sends) != 3 || cap.sends[0].id != 0x4581 || cap.sends[1].id != 0x4582 || cap.sends[2].id != 0x4583 {
		t.Errorf("expected 0x4581+0x4582+0x4583, got %+v", cap.sends)
	}
}

func TestSearchPlayers_SendsTwoPackets(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4600}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 2 || cap.sends[0].id != 0x4601 || cap.sends[1].id != 0x4603 {
		t.Errorf("expected 0x4601+0x4603, got %+v", cap.sends)
	}
}

func TestGetInboxMessages_EmptyInbox_SendsStartAndEndPackets(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4780}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 2 || cap.sends[0].id != 0x4782 || cap.sends[1].id != 0x4786 {
		t.Errorf("expected 0x4782+0x4786 for empty inbox, got %+v", cap.sends)
	}
}

// ---- 0x4a00 quickMatchSearch ------------------------------------------------

func TestQuickMatchSearch_NoLobby_SendsNoResults(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := sessionWithProfile(hub, 1, "P")
	s.User.LobbyIndex = -1 // not in a lobby

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4a00}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) < 1 || cap.sends[0].id != 0x4a01 {
		t.Fatalf("expected 0x4a01, got %+v", cap.sends)
	}
	if len(cap.sends[0].data) != 4 {
		t.Errorf("data len = %d, want 4", len(cap.sends[0].data))
	}
	if binary.BigEndian.Uint32(cap.sends[0].data) != 1 {
		t.Errorf("no-results value should be 1")
	}
}

// ---- 0x0003 menu disconnect -------------------------------------------------

func TestMenuDisconnect_RemovesFromHubAndLobby(t *testing.T) {
	hub := menuHub(100, config.Lobby{Name: "EU"})
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, _ := sessionWithProfile(hub, 5, "Disconnect5")

	// Add to hub and lobby
	hub.UserOnline(s)
	hub.AddSession(s)
	lobby, _ := hub.GetLobby(0)
	s.User.LobbyIndex = 0
	lobby.Enter(s.User)

	if hub.OnlineCount() != 1 {
		t.Fatal("expected 1 online before disconnect")
	}
	if lobby.PlayerCount() != 1 {
		t.Fatal("expected 1 player in lobby before disconnect")
	}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x0003}}
	_ = d.Dispatch(s, pkt)

	if hub.OnlineCount() != 0 {
		t.Errorf("expected 0 online after disconnect, got %d", hub.OnlineCount())
	}
	if lobby.PlayerCount() != 0 {
		t.Errorf("expected 0 players in lobby after disconnect, got %d", lobby.PlayerCount())
	}
}

// ---- Hub lobby support ------------------------------------------------------

func TestHub_GetLobby_ValidIndex(t *testing.T) {
	hub := menuHub(100,
		config.Lobby{Name: "Lobby0"},
		config.Lobby{Name: "Lobby1"},
	)
	l, ok := hub.GetLobby(1)
	if !ok {
		t.Fatal("GetLobby(1) should succeed")
	}
	if l.Name != "Lobby1" {
		t.Errorf("Name = %q, want Lobby1", l.Name)
	}
}

func TestHub_GetLobby_OutOfRange(t *testing.T) {
	hub := menuHub(100, config.Lobby{Name: "X"})
	_, ok := hub.GetLobby(5)
	if ok {
		t.Error("GetLobby(5) should return false for out-of-range index")
	}
}

func TestHub_Lobbies_Length(t *testing.T) {
	hub := menuHub(100,
		config.Lobby{Name: "A"},
		config.Lobby{Name: "B"},
		config.Lobby{Name: "C"},
	)
	if len(hub.Lobbies()) != 3 {
		t.Errorf("Lobbies() len = %d, want 3", len(hub.Lobbies()))
	}
}

// ---- formatPlayerInfo size --------------------------------------------------

func TestFormatPlayerInfo_SizeIs31Bytes(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := sessionWithProfile(hub, 10, "TestPlayer")
	s.User.LobbyIndex = -1

	// Use getUserList to trigger formatPlayerInfo — add a lobby with this user
	hub2 := menuHub(100, config.Lobby{Name: "L"})
	d2 := protocol.NewMenuDispatcher(hub2, nil, "pes5")
	s2, cap2 := sessionWithProfile(hub2, 10, "TestPlayer")
	lobby, _ := hub2.GetLobby(0)
	s2.User.LobbyIndex = 0
	lobby.Enter(s2.User)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4210}}
	_ = d2.Dispatch(s2, pkt)

	// cap2.sends: [0x4211][0x4212 for each player][0x4213]
	var entry []byte
	for _, send := range cap2.sends {
		if send.id == 0x4212 {
			entry = send.data
			break
		}
	}
	if entry == nil {
		t.Fatal("no 0x4212 player entry found")
	}
	if len(entry) != 31 {
		t.Errorf("formatPlayerInfo size = %d, want 31", len(entry))
	}

	_ = d
	_ = cap
	_ = s
}

// ---- 0x3080 getFriendsAndBlocked --------------------------------------------

func TestDo3080_NoFriends_SendsBeginAndEnd(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)
	s.User = &model.ConnectedUser{
		User:    &model.User{Hash: "u1"},
		Profile: &model.Profile{ID: 1, Name: "Player1"},
	}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3080}}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}

	// With nil sc, DB call returns ErrNoDB → zero entries → only 0x3082 + 0x3086
	if len(cap.sends) != 2 {
		t.Fatalf("expected 2 sends (0x3082 + 0x3086), got %d: %+v", len(cap.sends), cap.sends)
	}
	if cap.sends[0].id != 0x3082 {
		t.Errorf("send[0].id = 0x%04x, want 0x3082", cap.sends[0].id)
	}
	if cap.sends[1].id != 0x3086 {
		t.Errorf("send[1].id = 0x%04x, want 0x3086", cap.sends[1].id)
	}
}

// ---- 0x4580 getFriendsMatchState --------------------------------------------

func TestGetFriends4580_SendsThreePackets(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4580}}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}

	if len(cap.sends) != 3 {
		t.Fatalf("expected 3 sends, got %d: %+v", len(cap.sends), cap.sends)
	}
	want := []uint16{0x4581, 0x4582, 0x4583}
	for i, w := range want {
		if cap.sends[i].id != w {
			t.Errorf("send[%d].id = 0x%04x, want 0x%04x", i, cap.sends[i].id, w)
		}
	}
}

// ---- 0x4600 searchPlayers ---------------------------------------------------

func TestSearchPlayers4600_EmptyResults_SendsBeginAndEnd(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)
	s.User = &model.ConnectedUser{
		User:    &model.User{Hash: "u1"},
		Profile: &model.Profile{ID: 1, Name: "Player1"},
	}

	// searchType=0, name="NoOne" — with nil sc DB returns error → empty results
	data := make([]byte, 17)
	data[0] = 0 // exact match
	copy(data[1:], []byte("NoOne"))
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4600}, Data: data}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}

	// Expects 0x4601 (clean) + 0x4603 (end), no 0x4602 (no results)
	if len(cap.sends) != 2 {
		t.Fatalf("expected 2 sends (0x4601 + 0x4603), got %d: %+v", len(cap.sends), cap.sends)
	}
	if cap.sends[0].id != 0x4601 {
		t.Errorf("send[0].id = 0x%04x, want 0x4601", cap.sends[0].id)
	}
	if cap.sends[1].id != 0x4603 {
		t.Errorf("send[1].id = 0x%04x, want 0x4603", cap.sends[1].id)
	}
}

func TestSearchPlayers4600_ShortData_SendsBeginAndEnd(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4600}, Data: []byte{0}}
	_ = d.Dispatch(s, pkt)

	// too short (< 17 bytes) → 0x4601 + 0x4603 only
	ids := make([]uint16, len(cap.sends))
	for i, send := range cap.sends {
		ids[i] = send.id
	}
	found4601, found4603 := false, false
	for _, id := range ids {
		if id == 0x4601 {
			found4601 = true
		}
		if id == 0x4603 {
			found4603 = true
		}
	}
	if !found4601 || !found4603 {
		t.Errorf("expected 0x4601 and 0x4603, got %v", ids)
	}
}

// ---- 0x3f01 WE9LE — verify registered in menu dispatcher --------------------

func TestMenuDispatcher_Has3f01Handler(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "we9le")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3f01}, Data: []byte{1, 2, 3}}
	_ = d.Dispatch(s, pkt)

	// Short data → decrypt fails → 0x3f02 with error code (not default echo 0x3f02 zeros)
	found := false
	for _, send := range cap.sends {
		if send.id == 0x3f02 {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("expected 0x3f02 response in menu dispatcher, got %+v", cap.sends)
	}
}

// ---- 0x3f01 entry format check (binary.BigEndian usage) ---------------------

func TestAuthenticate3f01_ResponseID_Is3f02(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "we9le")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3f01}, Data: make([]byte, 64)}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) == 0 {
		t.Fatal("expected at least 1 response")
	}
	if cap.sends[0].id != 0x3f02 {
		t.Errorf("response ID = 0x%04x, want 0x3f02", cap.sends[0].id)
	}
	// Must not respond with 0x3004 (that would be PES5/WE9)
	for _, send := range cap.sends {
		if send.id == 0x3004 {
			t.Error("got 0x3004 response for 0x3f01 — wrong auth variant")
		}
	}
}
