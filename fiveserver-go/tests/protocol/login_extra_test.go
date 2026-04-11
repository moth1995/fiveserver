package protocol_test

import (
	"bytes"
	"compress/zlib"
	"encoding/binary"
	"io"
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/model"
	"github.com/fiveserver/fiveserver-go/internal/protocol"
)

// ---- 0x3030 deleteProfile ---------------------------------------------------

func TestDeleteProfile_NilUser_NoSend(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3030}, Data: []byte{0}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 0 {
		t.Errorf("nil user should produce no sends, got %+v", cap.sends)
	}
}

func TestDeleteProfile_ValidIndex_SendsZeros(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	s.User = &model.ConnectedUser{
		User: &model.User{Hash: "u"},
		Profiles: []*model.Profile{
			{ID: 7, Name: "ToDelete", Ordinal: 0},
		},
	}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3030}, Data: []byte{0}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x3032 {
		t.Errorf("expected 0x3032, got %+v", cap.sends)
	}
	// Profile slot reset to empty
	if s.User.Profiles[0] == nil {
		t.Error("slot should not be nil; should be reset to empty Profile")
	}
	if s.User.Profiles[0].ID != 0 {
		t.Errorf("slot ID = %d, want 0 (reset)", s.User.Profiles[0].ID)
	}
}

// ---- 0x3087 stub -----------------------------------------------------------

func TestDo3087_Stub_NoOp(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3087}}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}
	if len(cap.sends) != 0 {
		t.Errorf("stub should send nothing, got %+v", cap.sends)
	}
}

// ---- 0x3088 stub -----------------------------------------------------------

func TestDo3088_Settings1_Stored(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, _ := newCaptureSession(hub)

	s.User = &model.ConnectedUser{
		User:    &model.User{Hash: "u"},
		Profile: &model.Profile{ID: 1, Name: "P"},
	}

	// pkt.Data[2] == 3 → stored as Settings1
	origData := []byte{0x01, 0x02, 0x03, 0xAA, 0xBB}
	pkt := protocol.Packet{
		Header: protocol.Header{ID: 0x3088},
		Data:   origData,
	}
	_ = d.Dispatch(s, pkt)

	if len(s.User.Profile.Settings.Settings1) == 0 {
		t.Error("Settings1 should have been stored")
	}
	// Settings are stored zlib-compressed; decompress and verify round-trip.
	r, err := zlib.NewReader(bytes.NewReader(s.User.Profile.Settings.Settings1))
	if err != nil {
		t.Fatalf("stored Settings1 is not valid zlib: %v", err)
	}
	dec, err := io.ReadAll(r)
	r.Close()
	if err != nil {
		t.Fatalf("decompress Settings1: %v", err)
	}
	if !bytes.Equal(dec, origData) {
		t.Errorf("decompressed Settings1 = %#v, want %#v", dec, origData)
	}
}

func TestDo3088_Settings2_Stored(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, _ := newCaptureSession(hub)

	s.User = &model.ConnectedUser{
		User:    &model.User{Hash: "u"},
		Profile: &model.Profile{ID: 1, Name: "P"},
	}

	// pkt.Data[2] != 3 → stored as Settings2
	pkt := protocol.Packet{
		Header: protocol.Header{ID: 0x3088},
		Data:   []byte{0x01, 0x02, 0x01, 0xCC},
	}
	_ = d.Dispatch(s, pkt)

	if len(s.User.Profile.Settings.Settings2) == 0 {
		t.Error("Settings2 should have been stored")
	}
}

func TestDo3088_ShortData_NoOp(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, _ := newCaptureSession(hub)

	s.User = &model.ConnectedUser{
		User:    &model.User{Hash: "u"},
		Profile: &model.Profile{ID: 1, Name: "P"},
	}

	pkt := protocol.Packet{
		Header: protocol.Header{ID: 0x3088},
		Data:   []byte{0x01, 0x02}, // too short (< 3)
	}
	_ = d.Dispatch(s, pkt)

	if s.User.Profile.Settings.Settings1 != nil || s.User.Profile.Settings.Settings2 != nil {
		t.Error("short data should not store anything")
	}
}

// ---- 0x308a askForSettings (no DB / StoreSettings=false) -------------------

func TestAskForSettings_StoreSettingsFalse_SendsError(t *testing.T) {
	hub := loginHub(100) // StoreSettings defaults to false
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	s.User = &model.ConnectedUser{
		User:    &model.User{Hash: "u"},
		Profile: &model.Profile{ID: 1, Name: "P"},
	}

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x308a}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x3087 {
		t.Fatalf("expected 0x3087, got %+v", cap.sends)
	}
	code := binary.BigEndian.Uint32(cap.sends[0].data)
	if code != 0xfffffedd {
		t.Errorf("error code = 0x%08x, want 0xfffffedd", code)
	}
}

// ---- 0x3089 stub ----------------------------------------------------------

func TestDo3089_Sends308b(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3089}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x308b {
		t.Errorf("expected 0x308b, got %+v", cap.sends)
	}
	if len(cap.sends[0].data) != 4 {
		t.Errorf("data len = %d, want 4", len(cap.sends[0].data))
	}
}

// ---- 0x3070 getMatchResults (no DB, returns empty) ------------------------

func TestGetMatchResults_ShortPacket_ReturnsEmpty(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3070}, Data: []byte{0, 0}} // too short
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x3072 {
		t.Fatalf("expected 0x3072, got %+v", cap.sends)
	}
	if len(cap.sends[0].data) != 4 {
		t.Errorf("empty result should be 4 bytes, got %d", len(cap.sends[0].data))
	}
}

func TestGetMatchResults_ValidIDNoDb_ReturnsEmpty(t *testing.T) {
	hub := loginHub(100)
	d := protocol.NewLoginDispatcher(hub, nil, "pes5") // nil sc → DB calls return error
	s, cap := newCaptureSession(hub)
	s.User = &model.ConnectedUser{
		User:     &model.User{Hash: "u"},
		Profiles: []*model.Profile{{ID: 42, Name: "P", Ordinal: 0}},
	}

	data := make([]byte, 4)
	binary.BigEndian.PutUint32(data, 42)
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3070}, Data: data}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 1 || cap.sends[0].id != 0x3072 {
		t.Fatalf("expected 0x3072, got %+v", cap.sends)
	}
}
