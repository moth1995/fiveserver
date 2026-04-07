package server

import (
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"sync"
	"sync/atomic"

	"github.com/fiveserver/fiveserver-go/internal/crypto"
	"github.com/fiveserver/fiveserver-go/internal/protocol"
)

// HandlerFunc is called for every non-heartbeat packet received on a Conn.
// Kept for compatibility; prefer Serve(addr, dispatcher, onClose) for new code.
type HandlerFunc func(conn *Conn, pkt protocol.Packet)

// Conn wraps a net.Conn, tracks the rolling packet counter, and provides
// thread-safe Send/SendData/SendZeros helpers.
type Conn struct {
	net.Conn
	RemoteAddr string

	mu          sync.Mutex
	packetCount uint32
}

// Send marshals pkt, XOR-encrypts it, and writes it to the connection.
// Increments the internal packet counter. Thread-safe.
func (c *Conn) Send(pkt protocol.Packet) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	wire := crypto.XorData(protocol.Marshal(pkt), 0)
	_, err := c.Conn.Write(wire)
	if err == nil {
		atomic.AddUint32(&c.packetCount, 1)
	}
	return err
}

// SendData builds a Packet from id + data and calls Send.
func (c *Conn) SendData(id uint16, data []byte) error {
	log.Printf("[tcp] %s: send pkt 0x%04x len=%d", c.RemoteAddr, id, len(data))
	return c.Send(protocol.Packet{
		Header: protocol.Header{
			ID:          id,
			Length:      uint16(len(data)),
			PacketCount: atomic.LoadUint32(&c.packetCount),
		},
		Data: data,
	})
}

// SendZeros sends a packet with id and length zero bytes of data.
func (c *Conn) SendZeros(id uint16, length int) error {
	return c.SendData(id, make([]byte, length))
}

// Serve listens on addr and dispatches packets to the given Dispatcher.
// A ConnSender shim is built per connection so the protocol layer never
// imports the server package (avoids import cycle).
//
// Serve blocks until the listener is closed. Pass a channel that is closed
// on shutdown via the stopCh parameter; pass nil to run forever.
func Serve(addr string, d *protocol.Dispatcher, stopCh <-chan struct{}) error {
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return fmt.Errorf("server: listen %s: %w", addr, err)
	}

	// Close the listener when stopCh is closed (graceful shutdown).
	if stopCh != nil {
		go func() {
			<-stopCh
			ln.Close()
		}()
	}

	for {
		raw, err := ln.Accept()
		if err != nil {
			// net.ErrClosed is returned when ln.Close() is called
			return nil
		}
		conn := &Conn{
			Conn:        raw,
			RemoteAddr:  raw.RemoteAddr().String(),
			packetCount: 1,
		}
		log.Printf("[tcp] connection accepted from %s", conn.RemoteAddr)
		go serveConn(conn, d)
	}
}

// serveConn builds a Session+ConnSender for one accepted connection and runs
// the packet read loop, dispatching every packet through d.
func serveConn(conn *Conn, d *protocol.Dispatcher) {
	defer func() {
		log.Printf("[tcp] connection closed: %s", conn.RemoteAddr)
		conn.Close()
	}()

	// ConnSender shim: bridges server.Conn to protocol.Session without an
	// import cycle (protocol cannot import server).
	cs := &protocol.ConnSender{
		SendDataFn:  conn.SendData,
		SendZerosFn: conn.SendZeros,
		SendFn:      conn.Send,
		RemoteAddr:  conn.RemoteAddr,
	}
	s := &protocol.Session{
		Conn:       cs,
		Dispatcher: d,
	}

	buf := make([]byte, 0, 4096)
	tmp := make([]byte, 4096)

	for {
		n, err := conn.Conn.Read(tmp)
		if err != nil {
			if !errors.Is(err, io.EOF) && !errors.Is(err, net.ErrClosed) {
				log.Printf("[tcp] %s: read error: %v", conn.RemoteAddr, err)
			}
			return
		}
		buf = append(buf, tmp[:n]...)

		for {
			if len(buf) < 8 {
				break
			}

			// XOR-decrypt the 8-byte header at offset 0
			hdrBytes := crypto.XorData(buf[:8], 0)
			hdr, err := protocol.UnmarshalHeader(hdrBytes)
			if err != nil {
				log.Printf("[tcp] %s: bad header: %v", conn.RemoteAddr, err)
				return
			}

			total := int(hdr.Length) + 24
			if total > 65536 {
				log.Printf("[tcp] %s: pkt 0x%04x: absurd length %d — closing", conn.RemoteAddr, hdr.ID, hdr.Length)
				return
			}
			if len(buf) < total {
				break
			}

			// XOR-decrypt the remainder at offset 8, then prepend decrypted header
			pktBytes := crypto.XorData(buf[:total], 8)
			full := make([]byte, total)
			copy(full[:8], hdrBytes)
			copy(full[8:], pktBytes[8:])

			buf = buf[total:]

			pkt, err := protocol.Unmarshal(full)
			if err != nil {
				log.Printf("[tcp] %s: unmarshal error: %v", conn.RemoteAddr, err)
				return
			}

			log.Printf("[tcp] %s: recv pkt 0x%04x len=%d", conn.RemoteAddr, pkt.Header.ID, pkt.Header.Length)

			// Heartbeat: echo back, no dispatch
			if pkt.Header.ID == 0x0005 {
				pkt.Header.PacketCount = atomic.LoadUint32(&conn.packetCount)
				_ = conn.Send(pkt)
				continue
			}

			if err := d.Dispatch(s, pkt); err != nil {
				log.Printf("[tcp] %s: dispatch 0x%04x error: %v", conn.RemoteAddr, pkt.Header.ID, err)
			}
		}
	}
}
