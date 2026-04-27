package protocol

import (
	"encoding/binary"
	"fmt"
	"time"

	"github.com/fiveserver/fiveserver-go/internal/model"
)

// VERSION is the server version string embedded in greeting titles.
const VERSION = "0.5.0"

// NewNewsDispatcher returns a Dispatcher pre-registered with the three news
// handlers (0x2008, 0x2005, 0x2006). version is "pes5", "we9", or "we9le".
func NewNewsDispatcher(hub *Hub, version string) *Dispatcher {
	d := NewDispatcher()
	d.Register(0x2008, makeGetNews(hub, version))
	d.Register(0x2005, makeGetServerList(hub, version))
	d.Register(0x2006, makeGetTime())
	d.DefaultHandler = echoDefaultHandler()
	return d
}

// ---- 0x2008 getNews ---------------------------------------------------------

func makeGetNews(hub *Hub, version string) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		cfg := hub.Config()

		// Always send 0x2009 zeros first (4 bytes)
		if err := s.Conn.SendZeros(0x2009, 4); err != nil {
			return err
		}

		ip := s.Conn.RemoteAddr
		// strip port if present
		if host, _, err := splitHostPort(ip); err == nil {
			ip = host
		}

		if cfg.IsBanned(ip) {
			if err := s.Conn.SendData(0x200a, buildMessage(
				fmt.Sprintf("Fiveserver (v%s)", VERSION),
				"Sorry, but you are currently banned\r\n"+
					"from playing on this server. Please\r\n"+
					"contact server administrator if you\r\n"+
					"believe that there was a mistake.",
				false,
			)); err != nil {
				return err
			}
		} else if hub.AtCapacity() {
			msg := fmt.Sprintf(
				"Sorry, but the server is currently at capacity.\r\n"+
					"We already have a maximum of %d users logged in,\r\n"+
					"so please come back at a later time.\r\n"+
					"Thanks.\r\n", hub.OnlineCount())
			if err := s.Conn.SendData(0x200a, buildMessage(
				fmt.Sprintf("Fiveserver (v%s)", VERSION),
				msg,
				false, // capacity message uses padWithZeros (not stripped)
			)); err != nil {
				return err
			}
		} else {
			// Normal greeting
			title := fmt.Sprintf("SYSTEM: Fiveserver v%s", VERSION)
			text := cfg.Greeting.Text
			if text == "" {
				text = "Welcome to Fiveserver -\r\n" +
					"independent community server\r\n" +
					"supporting PES5/WE9/WE9LE games.\r\n" +
					"Have a good time, play some nice\r\n" +
					"football and try to score goals."
			}
			if err := s.Conn.SendData(0x200a, buildMessage(title, text, true)); err != nil {
				return err
			}
			// NEW_FEATURES: additional version-specific announcement (mirrors Python NEW_FEATURES dict)
			if ann, ok := cfg.NewFeatures[version]; ok {
				if err := s.Conn.SendData(0x200a, buildMessage(ann.Title, ann.Text, true)); err != nil {
					return err
				}
			}
		}

		// Always terminate with 0x200b (0 bytes)
		return s.Conn.SendZeros(0x200b, 0)
	}
}

// buildMessage constructs the 0x200a payload.
//
//	[4]  \x00\x00\x00\x00
//	[2]  \x01\x01
//	[19] UTC timestamp, null-padded
//	[64] title, null-padded
//	[N]  text: if stripText → StripZeros(PadWithZeros(text,512))
//	           else         → PadWithZeros(text,512)
func buildMessage(title, text string, stripText bool) []byte {
	ts := time.Now().UTC().Format("2006-01-02 15:04:05")
	var data []byte
	data = append(data, 0, 0, 0, 0) // 4 zero bytes
	data = append(data, 0x01, 0x01) // flags
	data = append(data, model.PadWithZeros(ts, 19)...)
	data = append(data, model.PadWithZeros(title, 64)...)
	padded := model.PadWithZeros(text, 512)
	if stripText {
		data = append(data, model.StripZeros(padded)...)
	} else {
		data = append(data, padded...)
	}
	return data
}

// ---- 0x2005 getServerList ---------------------------------------------------

// serverEntry mirrors one row of the Python servers list in getServerList_2005.
// Wire layout per entry (65 bytes):
//
//	[4] id        int32  big-endian  (always -1)
//	[4] type      int32  big-endian  (2=main, 3=menu, 1=login)
//	[32] name     null-padded string
//	[15] ip       null-padded string (IPv4 max 15 chars)
//	[2] port      uint16 big-endian
//	[2] users     uint16 big-endian
//	[2] unknown   uint16 big-endian  (same as type)
type serverEntry struct {
	id      int32
	stype   int32
	name    string
	ip      string
	port    int
	users   int
	unknown int32
}

func encodeServerEntry(e serverEntry) []byte {
	b := make([]byte, 65)
	binary.BigEndian.PutUint32(b[0:4], uint32(e.id))
	binary.BigEndian.PutUint32(b[4:8], uint32(e.stype))
	copy(b[8:40], model.PadWithZeros(e.name, 32))
	copy(b[40:55], model.PadWithZeros(e.ip, 15))
	binary.BigEndian.PutUint16(b[55:57], uint16(e.port))
	binary.BigEndian.PutUint16(b[57:59], uint16(e.users))
	binary.BigEndian.PutUint16(b[59:61], uint16(e.unknown))
	return b[:61] // 4+4+32+15+2+2+2 = 61 bytes (matches Python struct)
}

func makeGetServerList(hub *Hub, version string) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		cfg := hub.Config()

		// Determine the login port for this game version by direct map lookup.
		loginPort := cfg.NetworkServer.LoginService[version]

		serverIP := cfg.ServerIPWAN()

		onlineCount := hub.OnlineCount()
		if onlineCount > 0 {
			onlineCount-- // Python: max(0, numUsersOnline-1)
		}

		entries := []serverEntry{
			{-1, 2, cfg.ServerName, serverIP, cfg.NetworkServer.MainService, onlineCount, 2},
			{-1, 3, "NETWORK_MENU", serverIP, cfg.NetworkServer.NetworkMenuService, 0, 3},
			{-1, 1, "LOGIN", serverIP, loginPort, 0, 1},
		}

		var data []byte
		for _, e := range entries {
			data = append(data, encodeServerEntry(e)...)
		}

		if err := s.Conn.SendZeros(0x2002, 4); err != nil {
			return err
		}
		if err := s.Conn.SendData(0x2003, data); err != nil {
			return err
		}
		return s.Conn.SendZeros(0x2004, 4)
	}
}

// ---- 0x2006 getTime ---------------------------------------------------------

func makeGetTime() HandlerFunc {
	return func(s *Session, pkt Packet) error {
		b := make([]byte, 4)
		binary.BigEndian.PutUint32(b, uint32(time.Now().Unix()))
		return s.Conn.SendData(0x2007, b)
	}
}

// ---- helpers ----------------------------------------------------------------

// splitHostPort splits "host:port" into ("host", "port", nil).
// Falls back to returning the input unchanged if no port is found.
func splitHostPort(addr string) (host, port string, err error) {
	for i := len(addr) - 1; i >= 0; i-- {
		if addr[i] == ':' {
			return addr[:i], addr[i+1:], nil
		}
	}
	return "", "", fmt.Errorf("no port in %q", addr)
}
