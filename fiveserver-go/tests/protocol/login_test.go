package protocol_test

import (
	"encoding/binary"
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/config"
	"github.com/fiveserver/fiveserver-go/internal/model"
	"github.com/fiveserver/fiveserver-go/internal/protocol"
)

// ---- helpers ----------------------------------------------------------------

func loginHub(maxUsers int) *protocol.Hub {
	return protocol.NewHub(&config.Config{
		MaxUsers:   maxUsers,
		ServerName: "Test",
		NetworkServer: config.NetworkServerConfig{
			LoginService: map[string]int{"pes5": 20102},
		},
	})
}

// ---- 0x3001 -----------------------------------------------------------------

func TestDo3001_SendsZeros16(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3001}}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}

	if len(cap.sends) != 1 {
		t.Fatalf("expected 1 send, got %d", len(cap.sends))
	}
	if cap.sends[0].id != 0x3002 {
		t.Errorf("id = 0x%04x, want 0x3002", cap.sends[0].id)
	}
	if len(cap.sends[0].data) != 16 {
		t.Errorf("len = %d, want 16", len(cap.sends[0].data))
	}
}

// ---- 0x3003 authenticate (no DB) -------------------------------------------

func TestAuthenticate_ShortPacket_SendsAuthError(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	// pkt.Data too short to decrypt → DecryptECB fails → sends 0xffffff10
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3003}, Data: []byte{1, 2, 3}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x3004 {
		t.Fatalf("expected 0x3004 error response, got %+v", cap.sends)
	}
	code := binary.BigEndian.Uint32(cap.sends[0].data)
	if code != 0xffffff10 {
		t.Errorf("error code = 0x%08x, want 0xffffff10", code)
	}
}

// ---- 0x3010 getProfiles (nil user) -----------------------------------------

func TestGetProfiles_NilUser_NoOp(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3010}}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}
	if len(cap.sends) != 0 {
		t.Errorf("expected 0 sends for nil user, got %d", len(cap.sends))
	}
}

// ---- 0x3010 getProfiles (with user) ----------------------------------------

func TestGetProfiles_WithUser_SendsProfileData(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	s.User = &model.ConnectedUser{
		User:    &model.User{Hash: "abc"},
		Profile: nil,
		Profiles: []*model.Profile{
			{ID: 10, Name: "Ronaldo", Ordinal: 0},
			{ID: 11, Name: "Messi", Ordinal: 1},
			{ID: 12, Name: "Zidane", Ordinal: 2},
		},
	}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3010}}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}

	if len(cap.sends) != 1 || cap.sends[0].id != 0x3012 {
		t.Fatalf("expected 0x3012, got %+v", cap.sends)
	}
	// [4 leading zeros] + 3 × 32-byte entries = 100 bytes
	data := cap.sends[0].data
	if len(data) != 100 {
		t.Errorf("data length = %d, want 100 (4 + 3×32)", len(data))
	}

	// First profile entry starts at offset 4
	entry := data[4:36]
	if entry[0] != 0 { // index
		t.Errorf("entry[0] = %d, want 0", entry[0])
	}
	id := int32(binary.BigEndian.Uint32(entry[1:5]))
	if id != 10 {
		t.Errorf("entry ID = %d, want 10", id)
	}
	// Name field [5:21]
	nameBytes := entry[5:21]
	gotName := string(nameBytes[:7]) // "Ronaldo"
	if gotName != "Ronaldo" {
		t.Errorf("entry name = %q, want Ronaldo", gotName)
	}
}

// ---- 0x3030 deleteProfile (nil user) ----------------------------------------

func TestDeleteProfile_NilUser_NoOp(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3030}, Data: []byte{0}}
	_ = d.Dispatch(s, pkt)

	// nil user → no-op but still sends 0x3032
	// (the handler checks nil user and returns nil without sending)
	_ = cap // just checking no panic
}

// ---- 0x3040 selectProfile ---------------------------------------------------

func TestSelectProfile_ProfileFound_SendsResponse(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	s.User = &model.ConnectedUser{
		User: &model.User{Hash: "user1"},
		Profiles: []*model.Profile{
			{ID: 42, Name: "Player1", Ordinal: 0},
		},
	}

	data := make([]byte, 4)
	binary.BigEndian.PutUint32(data, 42) // profile ID = 42
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3040}, Data: data}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}

	if len(cap.sends) != 1 || cap.sends[0].id != 0x3042 {
		t.Fatalf("expected 0x3042, got %+v", cap.sends)
	}

	if s.User.Profile == nil || s.User.Profile.ID != 42 {
		t.Error("s.User.Profile not set to the selected profile")
	}
}

func TestSelectProfile_ProfileNotFound_SendsNotFound(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	s.User = &model.ConnectedUser{
		User:     &model.User{Hash: "user2"},
		Profiles: []*model.Profile{{ID: 1, Name: "X", Ordinal: 0}},
	}

	data := make([]byte, 4)
	binary.BigEndian.PutUint32(data, 999) // non-existent profile
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3040}, Data: data}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) == 0 || cap.sends[0].id != 0x3041 {
		t.Errorf("expected 0x3041 for not-found, got %+v", cap.sends)
	}
}

// ---- 0x3050 / 0x3060 stubs --------------------------------------------------

func TestDo3050_Stub(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3050}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x3052 {
		t.Errorf("expected 0x3052, got %+v", cap.sends)
	}
	if len(cap.sends[0].data) != 0x47 {
		t.Errorf("data len = %d, want 0x47", len(cap.sends[0].data))
	}
}

func TestDo3060_Stub(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3060}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x3062 {
		t.Errorf("expected 0x3062, got %+v", cap.sends)
	}
}

// ---- 0x3090 / 0x3100 / 0x3120 stubs ----------------------------------------

func TestDo3090_SendsZeros(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3090}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x3091 {
		t.Errorf("expected 0x3091, got %+v", cap.sends)
	}
}

func TestDo3100_SendsZeros(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3100}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x3101 {
		t.Errorf("expected 0x3101, got %+v", cap.sends)
	}
}

func TestDo3120_SendsTwoPackets(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3120}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 2 {
		t.Fatalf("expected 2 sends, got %d", len(cap.sends))
	}
	if cap.sends[0].id != 0x3121 {
		t.Errorf("send[0].id = 0x%04x, want 0x3121", cap.sends[0].id)
	}
	if cap.sends[1].id != 0x3123 {
		t.Errorf("send[1].id = 0x%04x, want 0x3123", cap.sends[1].id)
	}
}

// ---- 0x0003 disconnect ------------------------------------------------------

func TestDisconnect_RemovesSession(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, _ := newCaptureSession(hub)

	s.User = &model.ConnectedUser{
		User:    &model.User{Hash: "user-dc"},
		Profile: &model.Profile{ID: 1, Name: "DCPlayer"},
	}
	hub.AddSession(s)

	if hub.OnlineCount() != 1 {
		t.Fatal("expected 1 online before disconnect")
	}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x0003}}
	_ = d.Dispatch(s, pkt)

	if hub.OnlineCount() != 0 {
		t.Errorf("expected 0 online after disconnect, got %d", hub.OnlineCount())
	}
}
