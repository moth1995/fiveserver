package protocol_test

import (
	"encoding/binary"
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/config"
	"github.com/fiveserver/fiveserver-go/internal/model"
	"github.com/fiveserver/fiveserver-go/internal/protocol"
)

// ---- 0x4100 selectProfileForMenu (no DB) -----------------------------------

func TestDo4100_NilUser_NoOp(t *testing.T) {
	hub := menuHub(100, config.Lobby{Name: "EU"})
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4100}, Data: []byte{0}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 0 {
		t.Errorf("nil user should produce no sends, got %+v", cap.sends)
	}
}

func TestDo4100_IndexOutOfRange_NoOp(t *testing.T) {
	hub := menuHub(100, config.Lobby{Name: "EU"})
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	s.User = &model.ConnectedUser{
		User:     &model.User{Hash: "u"},
		Profiles: []*model.Profile{{ID: 1, Name: "P", Ordinal: 0}},
	}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4100}, Data: []byte{5}} // out of range
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 0 {
		t.Errorf("out-of-range index should produce no sends, got %+v", cap.sends)
	}
}

func TestDo4100_ValidIndex_Sends4101Then4103(t *testing.T) {
	hub := menuHub(100, config.Lobby{Name: "EU"})
	d := protocol.NewMenuDispatcher(hub, nil, "pes5") // nil sc → DB returns error → 0x4103 zeros
	s, cap := newCaptureSession(hub)

	s.User = &model.ConnectedUser{
		User: &model.User{Hash: "u"},
		Profiles: []*model.Profile{
			{ID: 10, Name: "Player", Ordinal: 0},
		},
	}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4100}, Data: []byte{0}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) < 2 {
		t.Fatalf("expected >= 2 sends (0x4101, 0x4103), got %d", len(cap.sends))
	}
	if cap.sends[0].id != 0x4101 {
		t.Errorf("send[0].id = 0x%04x, want 0x4101", cap.sends[0].id)
	}
	if cap.sends[1].id != 0x4103 {
		t.Errorf("send[1].id = 0x%04x, want 0x4103", cap.sends[1].id)
	}
	// 0x4101 layout: [4 zeros][4 id][33 flags] = 41 bytes
	if len(cap.sends[0].data) != 41 {
		t.Errorf("0x4101 data len = %d, want 41", len(cap.sends[0].data))
	}
	// profile ID at [4:8]
	id := int32(binary.BigEndian.Uint32(cap.sends[0].data[4:8]))
	if id != 10 {
		t.Errorf("profile ID in 0x4101 = %d, want 10", id)
	}
	// s.User.Profile should be set
	if s.User.Profile == nil || s.User.Profile.ID != 10 {
		t.Error("s.User.Profile should be set after 0x4100")
	}
}

// ---- 0x4102 getProfile (no DB) ---------------------------------------------

func TestGetProfile4102_ShortPacket_SendsEmpty4103(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4102}, Data: []byte{0, 0}} // too short
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x4103 {
		t.Fatalf("expected 0x4103, got %+v", cap.sends)
	}
}

func TestGetProfile4102_NilDB_SendsEmpty4103(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5") // nil sc → error → empty
	s, cap := newCaptureSession(hub)

	data := make([]byte, 4)
	binary.BigEndian.PutUint32(data, 42)
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4102}, Data: data}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x4103 {
		t.Fatalf("expected 0x4103, got %+v", cap.sends)
	}
}

// ---- 0x4110 setFavTeam (nil DB = still updates in-memory) ------------------

func TestSetFavTeam_UpdatesInMemory(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	s.User = &model.ConnectedUser{
		User:    &model.User{Hash: "u"},
		Profile: &model.Profile{ID: 1, Name: "P"},
	}

	data := make([]byte, 2)
	binary.BigEndian.PutUint16(data, 37) // team 37
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4110}, Data: data}
	_ = d.Dispatch(s, pkt)

	if s.User.Profile.FavTeam != 37 {
		t.Errorf("FavTeam = %d, want 37", s.User.Profile.FavTeam)
	}
	if len(cap.sends) != 1 || cap.sends[0].id != 0x4112 {
		t.Errorf("expected 0x4112, got %+v", cap.sends)
	}
}

func TestSetFavTeam_NilUser_NoOp(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4110}, Data: make([]byte, 2)}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 0 {
		t.Errorf("nil user should produce no sends, got %+v", cap.sends)
	}
}

// ---- 0x4114 setFavPlayer ---------------------------------------------------

func TestSetFavPlayer_UpdatesInMemory(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	s.User = &model.ConnectedUser{
		User:    &model.User{Hash: "u"},
		Profile: &model.Profile{ID: 1, Name: "P"},
	}

	data := make([]byte, 4)
	binary.BigEndian.PutUint32(data, 999)
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4114}, Data: data}
	_ = d.Dispatch(s, pkt)

	if s.User.Profile.FavPlayer != 999 {
		t.Errorf("FavPlayer = %d, want 999", s.User.Profile.FavPlayer)
	}
	if len(cap.sends) != 1 || cap.sends[0].id != 0x4116 {
		t.Errorf("expected 0x4116, got %+v", cap.sends)
	}
}

func TestSetFavPlayer_NilUser_NoOp(t *testing.T) {
	hub := menuHub(100)
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4114}, Data: make([]byte, 4)}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 0 {
		t.Errorf("nil user should produce no sends, got %+v", cap.sends)
	}
}

// ---- 0x4210 getUserList (with users) ----------------------------------------

func TestGetUserList_WithUsers_SendsEntries(t *testing.T) {
	hub := menuHub(100, config.Lobby{Name: "EU"})
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")

	s1, cap1 := sessionWithProfile(hub, 1, "Alpha")
	s2, _ := sessionWithProfile(hub, 2, "Beta")

	hub.AddSession(s1)
	hub.AddSession(s2)
	lobby, _ := hub.GetLobby(0)
	s1.User.LobbyIndex = 0
	s2.User.LobbyIndex = 0
	lobby.Enter(s1.User)
	lobby.Enter(s2.User)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4210}}
	_ = d.Dispatch(s1, pkt)

	// Should be: 0x4211, one or more 0x4212, 0x4213
	if len(cap1.sends) < 3 {
		t.Fatalf("expected >= 3 sends (4211 + entries + 4213), got %d", len(cap1.sends))
	}
	if cap1.sends[0].id != 0x4211 {
		t.Errorf("first send should be 0x4211, got 0x%04x", cap1.sends[0].id)
	}
	if cap1.sends[len(cap1.sends)-1].id != 0x4213 {
		t.Errorf("last send should be 0x4213, got 0x%04x", cap1.sends[len(cap1.sends)-1].id)
	}
	var entries int
	for _, send := range cap1.sends {
		if send.id == 0x4212 {
			entries++
		}
	}
	if entries != 2 {
		t.Errorf("expected 2 player entries (0x4212), got %d", entries)
	}
}

// ---- 0x4300 getRoomList (with rooms) ----------------------------------------

func TestGetRoomList_WithRooms_SendsEntries(t *testing.T) {
	hub := menuHub(100, config.Lobby{Name: "EU"})
	d := protocol.NewMenuDispatcher(hub, nil, "pes5")

	s, cap := sessionWithProfile(hub, 1, "P1")
	hub.AddSession(s)
	lobby, _ := hub.GetLobby(0)
	s.User.LobbyIndex = 0
	lobby.Enter(s.User)

	room := model.NewRoom(lobby)
	room.Name = "Room1"
	room.Enter(s.User)
	lobby.AddRoom(room)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x4300}}
	_ = d.Dispatch(s, pkt)

	// 0x4301 + 0x4302 (room entry) + 0x4303
	if len(cap.sends) < 3 {
		t.Fatalf("expected >= 3 sends, got %d", len(cap.sends))
	}
	if cap.sends[0].id != 0x4301 {
		t.Errorf("send[0] = 0x%04x, want 0x4301", cap.sends[0].id)
	}
	var has4302 bool
	for _, send := range cap.sends {
		if send.id == 0x4302 {
			has4302 = true
		}
	}
	if !has4302 {
		t.Error("expected at least one 0x4302 room entry")
	}
	if cap.sends[len(cap.sends)-1].id != 0x4303 {
		t.Errorf("last send = 0x%04x, want 0x4303", cap.sends[len(cap.sends)-1].id)
	}
}
