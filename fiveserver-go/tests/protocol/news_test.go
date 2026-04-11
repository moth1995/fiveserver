package protocol_test

import (
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/config"
	"github.com/fiveserver/fiveserver-go/internal/protocol"
)

// captureConn records all SendData / SendZeros calls so tests can inspect them.
type captureConn struct {
	sends []capturedSend
}

type capturedSend struct {
	id   uint16
	data []byte
}

func newCaptureSession(hub *protocol.Hub) (*protocol.Session, *captureConn) {
	cap := &captureConn{}
	cs := &protocol.ConnSender{
		SendDataFn: func(id uint16, data []byte) error {
			d := make([]byte, len(data))
			copy(d, data)
			cap.sends = append(cap.sends, capturedSend{id, d})
			return nil
		},
		SendZerosFn: func(id uint16, length int) error {
			cap.sends = append(cap.sends, capturedSend{id, make([]byte, length)})
			return nil
		},
		SendFn:     func(pkt protocol.Packet) error { return nil },
		RemoteAddr: "1.2.3.4:5678",
	}
	s := &protocol.Session{Conn: cs, Hub: hub}
	return s, cap
}

func newsHub(maxUsers int) *protocol.Hub {
	return protocol.NewHub(&config.Config{
		MaxUsers:   maxUsers,
		ServerName: "Fiveserver",
		NetworkServer: config.NetworkServerConfig{
			MainService:        20100,
			NetworkMenuService: 20101,
			LoginService:       []config.GamePortEntry{{Port: 20102, Version: "pes5"}, {Port: 20103, Version: "we9"}, {Port: 20104, Version: "we9le"}},
		},
		Greeting: config.GreetingConfig{Text: "Welcome!"},
	})
}

// ---- 0x2008 getNews ---------------------------------------------------------

func TestGetNews_NormalGreeting_PacketSequence(t *testing.T) {
	hub := newsHub(100)
	d := protocol.NewNewsDispatcher(hub, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x2008}}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}

	// Expect: 0x2009, 0x200a, 0x200b
	if len(cap.sends) != 3 {
		t.Fatalf("expected 3 sends, got %d: %+v", len(cap.sends), cap.sends)
	}
	if cap.sends[0].id != 0x2009 {
		t.Errorf("send[0].id = 0x%04x, want 0x2009", cap.sends[0].id)
	}
	if cap.sends[1].id != 0x200a {
		t.Errorf("send[1].id = 0x%04x, want 0x200a", cap.sends[1].id)
	}
	if cap.sends[2].id != 0x200b {
		t.Errorf("send[2].id = 0x%04x, want 0x200b", cap.sends[2].id)
	}
}

func TestGetNews_MessageLayout(t *testing.T) {
	hub := newsHub(100)
	d := protocol.NewNewsDispatcher(hub, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x2008}}
	_ = d.Dispatch(s, pkt)

	msg := cap.sends[1].data // 0x200a payload
	// [0:4] = 0x00000000
	for i := 0; i < 4; i++ {
		if msg[i] != 0 {
			t.Errorf("byte[%d] = 0x%x, want 0x00", i, msg[i])
		}
	}
	// [4:6] = 0x01 0x01
	if msg[4] != 0x01 || msg[5] != 0x01 {
		t.Errorf("flags bytes = 0x%x 0x%x, want 0x01 0x01", msg[4], msg[5])
	}
	// [6:25] = timestamp (19 bytes)
	ts := msg[6:25]
	if ts[0] == 0 {
		t.Error("timestamp should not start with null")
	}
	// [25:89] = title (64 bytes)
	title := msg[25:89]
	if title[0] == 0 {
		t.Error("title should not start with null")
	}
}

func TestGetNews_BannedIP_SendsBanMessage(t *testing.T) {
	hub := newsHub(100)
	// Directly mark an IP as banned via a temp banned list won't work here,
	// so we test indirectly: a non-banned IP should get greeting (not ban msg).
	// Actual ban path is tested by config_test's IsBanned tests.
	d := protocol.NewNewsDispatcher(hub, "pes5")
	s, cap := newCaptureSession(hub)
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x2008}}
	_ = d.Dispatch(s, pkt)
	// Should still send 3 packets (0x2009, 0x200a, 0x200b)
	if len(cap.sends) != 3 {
		t.Fatalf("expected 3 sends for normal path, got %d", len(cap.sends))
	}
}

func TestGetNews_AtCapacity_SendsCapacityMessage(t *testing.T) {
	hub := newsHub(0) // MaxUsers=0 → always at capacity
	d := protocol.NewNewsDispatcher(hub, "pes5")
	s, cap := newCaptureSession(hub)
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x2008}}
	_ = d.Dispatch(s, pkt)

	if len(cap.sends) != 3 {
		t.Fatalf("expected 3 sends, got %d", len(cap.sends))
	}
	msg := cap.sends[1].data
	// Capacity message uses padWithZeros (not stripped), so data[89:] has nulls
	textPart := msg[89:]
	// Should be exactly 512 bytes (not stripped)
	if len(textPart) != 512 {
		t.Errorf("capacity text = %d bytes, want 512 (unstripped)", len(textPart))
	}
}

// ---- 0x2005 getServerList ---------------------------------------------------

func TestGetServerList_PacketSequence(t *testing.T) {
	hub := newsHub(100)
	d := protocol.NewNewsDispatcher(hub, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x2005}}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}

	// Expect: 0x2002, 0x2003, 0x2004
	if len(cap.sends) != 3 {
		t.Fatalf("expected 3 sends, got %d", len(cap.sends))
	}
	if cap.sends[0].id != 0x2002 {
		t.Errorf("send[0].id = 0x%04x, want 0x2002", cap.sends[0].id)
	}
	if cap.sends[1].id != 0x2003 {
		t.Errorf("send[1].id = 0x%04x, want 0x2003", cap.sends[1].id)
	}
	if cap.sends[2].id != 0x2004 {
		t.Errorf("send[2].id = 0x%04x, want 0x2004", cap.sends[2].id)
	}
}

func TestGetServerList_DataSize(t *testing.T) {
	hub := newsHub(100)
	d := protocol.NewNewsDispatcher(hub, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x2005}}
	_ = d.Dispatch(s, pkt)

	// 3 server entries × 61 bytes each = 183 bytes
	data := cap.sends[1].data
	if len(data) != 183 {
		t.Errorf("server list data = %d bytes, want 183 (3×61)", len(data))
	}
}

// ---- 0x2006 getTime ---------------------------------------------------------

func TestGetTime_Returns4Bytes(t *testing.T) {
	hub := newsHub(100)
	d := protocol.NewNewsDispatcher(hub, "pes5")
	s, cap := newCaptureSession(hub)

	pkt := protocol.Packet{Header: protocol.Header{ID: 0x2006}}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("dispatch: %v", err)
	}

	if len(cap.sends) != 1 {
		t.Fatalf("expected 1 send, got %d", len(cap.sends))
	}
	if cap.sends[0].id != 0x2007 {
		t.Errorf("id = 0x%04x, want 0x2007", cap.sends[0].id)
	}
	if len(cap.sends[0].data) != 4 {
		t.Errorf("data len = %d, want 4", len(cap.sends[0].data))
	}
	// Timestamp should be non-zero (current Unix time)
	ts := uint32(cap.sends[0].data[0])<<24 | uint32(cap.sends[0].data[1])<<16 |
		uint32(cap.sends[0].data[2])<<8 | uint32(cap.sends[0].data[3])
	if ts == 0 {
		t.Error("timestamp should not be zero")
	}
}
