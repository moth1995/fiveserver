package protocol_test

import (
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/config"
	"github.com/fiveserver/fiveserver-go/internal/model"
	"github.com/fiveserver/fiveserver-go/internal/protocol"
)

// ---- helpers ----------------------------------------------------------------

func newSession(hub *protocol.Hub) *protocol.Session {
	return &protocol.Session{
		Conn:        &protocol.ConnSender{RemoteAddr: "127.0.0.1:9999"},
		Hub:         hub,
		GameVersion: "pes5",
	}
}

func minConfig() *config.Config {
	return &config.Config{
		MaxUsers:   100,
		ServerName: "test",
	}
}

// ---- Dispatcher -------------------------------------------------------------

func TestDispatcher_RegisterAndDispatch(t *testing.T) {
	d := protocol.NewDispatcher()
	called := false
	d.Register(0x2008, func(s *protocol.Session, pkt protocol.Packet) error {
		called = true
		return nil
	})

	hub := protocol.NewHub(minConfig())
	s := newSession(hub)
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x2008}}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("Dispatch error: %v", err)
	}
	if !called {
		t.Fatal("handler was not called")
	}
}

func TestDispatcher_UnknownPacket_SilentlyDropped(t *testing.T) {
	d := protocol.NewDispatcher()
	hub := protocol.NewHub(minConfig())
	s := newSession(hub)
	// No handler registered — should return nil without panicking
	pkt := protocol.Packet{Header: protocol.Header{ID: 0xDEAD}}
	if err := d.Dispatch(s, pkt); err != nil {
		t.Fatalf("expected nil for unknown packet, got: %v", err)
	}
}

func TestDispatcher_OverwriteHandler(t *testing.T) {
	d := protocol.NewDispatcher()
	callCount := 0
	d.Register(0x3003, func(s *protocol.Session, pkt protocol.Packet) error {
		callCount++
		return nil
	})
	d.Register(0x3003, func(s *protocol.Session, pkt protocol.Packet) error {
		callCount += 10
		return nil
	})
	hub := protocol.NewHub(minConfig())
	s := newSession(hub)
	pkt := protocol.Packet{Header: protocol.Header{ID: 0x3003}}
	_ = d.Dispatch(s, pkt)
	if callCount != 10 {
		t.Fatalf("expected second handler (10), got callCount=%d", callCount)
	}
}

// ---- Hub --------------------------------------------------------------------

func TestHub_AddRemoveSession(t *testing.T) {
	hub := protocol.NewHub(minConfig())
	s := newSession(hub)
	s.User = &model.ConnectedUser{
		User:    &model.User{Hash: "abc"},
		Profile: &model.Profile{ID: 1, Name: "player1"},
	}

	hub.UserOnline(s)
	hub.AddSession(s)
	if hub.OnlineCount() != 1 {
		t.Fatalf("expected 1 online, got %d", hub.OnlineCount())
	}

	got, ok := hub.GetSession("player1")
	if !ok || got != s {
		t.Fatal("GetSession should return the added session")
	}

	hub.UserOffline(s)
	if hub.OnlineCount() != 0 {
		t.Fatalf("expected 0 after remove, got %d", hub.OnlineCount())
	}
	if _, ok := hub.GetSession("player1"); ok {
		t.Fatal("session should not be found after remove")
	}
}

func TestHub_AddSession_NilUser_NoOp(t *testing.T) {
	hub := protocol.NewHub(minConfig())
	s := newSession(hub) // User is nil
	hub.AddSession(s)
	if hub.OnlineCount() != 0 {
		t.Fatal("adding session with nil User should be a no-op")
	}
}

func TestHub_AddSession_NilProfile_NoOp(t *testing.T) {
	hub := protocol.NewHub(minConfig())
	s := newSession(hub)
	s.User = &model.ConnectedUser{User: &model.User{Hash: "x"}} // Profile nil
	hub.AddSession(s)
	if hub.OnlineCount() != 0 {
		t.Fatal("adding session with nil Profile should be a no-op")
	}
}

func TestHub_AtCapacity(t *testing.T) {
	cfg := &config.Config{MaxUsers: 2}
	hub := protocol.NewHub(cfg)

	addUser := func(name string) *protocol.Session {
		s := newSession(hub)
		s.User = &model.ConnectedUser{
			User:    &model.User{Hash: name},
			Profile: &model.Profile{ID: len(name), Name: name},
		}
		hub.UserOnline(s)
		hub.AddSession(s)
		return s
	}

	addUser("user1")
	addUser("user2")

	if !hub.AtCapacity() {
		t.Fatal("hub should be at capacity with 2/2 users")
	}
}

func TestHub_Sessions_Snapshot(t *testing.T) {
	hub := protocol.NewHub(minConfig())
	for _, name := range []string{"a", "b", "c"} {
		s := newSession(hub)
		s.User = &model.ConnectedUser{
			User:    &model.User{Hash: name},
			Profile: &model.Profile{Name: name},
		}
		hub.AddSession(s)
	}
	snap := hub.Sessions()
	if len(snap) != 3 {
		t.Fatalf("expected 3 sessions, got %d", len(snap))
	}
}

// ---- ConnSender -------------------------------------------------------------

func TestConnSender_Delegates(t *testing.T) {
	sendDataCalled := false
	sendZerosCalled := false
	sendCalled := false

	cs := &protocol.ConnSender{
		SendDataFn:  func(id uint16, data []byte) error { sendDataCalled = true; return nil },
		SendZerosFn: func(id uint16, length int) error { sendZerosCalled = true; return nil },
		SendFn:      func(pkt protocol.Packet) error { sendCalled = true; return nil },
		RemoteAddr:  "1.2.3.4:5678",
	}

	_ = cs.SendData(0x2009, []byte{0, 0, 0, 0})
	_ = cs.SendZeros(0x2009, 4)
	_ = cs.Send(protocol.Packet{})

	if !sendDataCalled || !sendZerosCalled || !sendCalled {
		t.Fatal("not all delegate methods were called")
	}
	if cs.RemoteAddr != "1.2.3.4:5678" {
		t.Fatalf("RemoteAddr = %q", cs.RemoteAddr)
	}
}
