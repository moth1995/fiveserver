package server

import (
	"fmt"
	"net"
	"sync"
	"sync/atomic"

	"github.com/fiveserver/fiveserver-go/internal/crypto"
	"github.com/fiveserver/fiveserver-go/internal/protocol"
)

// HandlerFunc is called for every non-heartbeat packet received on a Conn.
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
	return c.Send(protocol.Packet{
		Header: protocol.Header{
			ID:          id,
			Length:      uint16(len(data)),
			PacketCount: atomic.LoadUint32(&c.packetCount),
		},
		Data: data,
	})
}

// SendZeros sends a packet with length zero bytes of data.
func (c *Conn) SendZeros(id uint16, length int) error {
	return c.SendData(id, make([]byte, length))
}

// Serve listens on addr and spawns a goroutine for each accepted connection.
// handler is called for every non-heartbeat packet. Blocks until the listener
// is closed.
func Serve(addr string, handler HandlerFunc) error {
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return fmt.Errorf("server: listen %s: %w", addr, err)
	}
	defer ln.Close()
	for {
		raw, err := ln.Accept()
		if err != nil {
			// net.ErrClosed is returned when ln.Close() is called
			return fmt.Errorf("server: accept: %w", err)
		}
		conn := &Conn{
			Conn:        raw,
			RemoteAddr:  raw.RemoteAddr().String(),
			packetCount: 1,
		}
		go readLoop(conn, handler)
	}
}

// readLoop reads XOR-encrypted packets from conn until the connection closes.
// It handles heartbeat packets (0x0005) internally and calls handler for all
// others. Mirrors Python PacketReceiver.dataReceived() framing logic exactly.
func readLoop(conn *Conn, handler HandlerFunc) {
	defer conn.Close()

	buf := make([]byte, 0, 4096)
	tmp := make([]byte, 4096)

	for {
		n, err := conn.Conn.Read(tmp)
		if err != nil {
			return
		}
		buf = append(buf, tmp[:n]...)

		for {
			// Need at least 8 bytes to read the header
			if len(buf) < 8 {
				break
			}

			// Step 1: XOR-decrypt the first 8 bytes (start=0) to get the header
			hdrBytes := crypto.XorData(buf[:8], 0)
			hdr, err := protocol.UnmarshalHeader(hdrBytes)
			if err != nil {
				return
			}

			// Step 2: wait until the full packet is buffered
			total := int(hdr.Length) + 24
			if len(buf) < total {
				break
			}

			// Step 3: XOR-decrypt the full packet (header already known, re-decrypt
			// starting at offset 8 for the MD5+data portion) — matches Python:
			// packet.makePacket(stream.xorData(recvd[:hdr.length+24], 8))
			pktBytes := crypto.XorData(buf[:total], 8)
			// Prepend the already-decrypted header bytes so Unmarshal sees a
			// complete, consistent buffer.
			full := make([]byte, total)
			copy(full[:8], hdrBytes)
			copy(full[8:], pktBytes[8:])

			buf = buf[total:]

			pkt, err := protocol.Unmarshal(full)
			if err != nil {
				// Bad packet — drop connection
				return
			}

			// Heartbeat: echo it back, increment counter (matches Python _packetReceived)
			if pkt.Header.ID == 0x0005 {
				pkt.Header.PacketCount = atomic.LoadUint32(&conn.packetCount)
				_ = conn.Send(pkt)
				continue
			}

			handler(conn, pkt)
		}
	}
}
