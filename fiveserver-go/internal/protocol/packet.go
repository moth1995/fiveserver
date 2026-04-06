package protocol

import (
	"crypto/md5"
	"encoding/binary"
	"fmt"
)

// Header is the 8-byte packet header (big-endian).
//
// Wire layout:
//
//	[0:2]  ID           uint16
//	[2:4]  Length       uint16  (data length only, excludes header+MD5)
//	[4:8]  PacketCount  uint32
type Header struct {
	ID          uint16
	Length      uint16
	PacketCount uint32
}

// Packet is a fully parsed PES5 packet.
type Packet struct {
	Header Header
	Data   []byte
}

// MarshalHeader serialises h to exactly 8 bytes (big-endian).
func MarshalHeader(h Header) []byte {
	b := make([]byte, 8)
	binary.BigEndian.PutUint16(b[0:2], h.ID)
	binary.BigEndian.PutUint16(b[2:4], h.Length)
	binary.BigEndian.PutUint32(b[4:8], h.PacketCount)
	return b
}

// UnmarshalHeader parses exactly 8 bytes into a Header.
func UnmarshalHeader(b []byte) (Header, error) {
	if len(b) < 8 {
		return Header{}, fmt.Errorf("packet: header too short (%d bytes)", len(b))
	}
	return Header{
		ID:          binary.BigEndian.Uint16(b[0:2]),
		Length:      binary.BigEndian.Uint16(b[2:4]),
		PacketCount: binary.BigEndian.Uint32(b[4:8]),
	}, nil
}

// Marshal serialises p to wire bytes: 8-byte header | 16-byte MD5 | N-byte data.
// The result is ready for XOR encryption before sending.
func Marshal(p Packet) []byte {
	hdr := MarshalHeader(p.Header)
	sum := packetMD5(hdr, p.Data)
	out := make([]byte, 8+16+len(p.Data))
	copy(out[0:8], hdr)
	copy(out[8:24], sum[:])
	copy(out[24:], p.Data)
	return out
}

// Unmarshal parses decrypted wire bytes into a Packet.
// Returns an error if the MD5 checksum does not match.
func Unmarshal(b []byte) (Packet, error) {
	if len(b) < 24 {
		return Packet{}, fmt.Errorf("packet: too short to contain header+MD5 (%d bytes)", len(b))
	}
	hdr, err := UnmarshalHeader(b[0:8])
	if err != nil {
		return Packet{}, err
	}
	if int(hdr.Length) != len(b)-24 {
		return Packet{}, fmt.Errorf("packet: header.Length=%d but got %d data bytes",
			hdr.Length, len(b)-24)
	}
	data := b[24 : 24+hdr.Length]
	wantSum := packetMD5(b[0:8], data)
	var gotSum [16]byte
	copy(gotSum[:], b[8:24])
	if wantSum != gotSum {
		return Packet{}, fmt.Errorf("packet: MD5 mismatch (got %x, want %x)", gotSum, wantSum)
	}
	return Packet{Header: hdr, Data: data}, nil
}

// packetMD5 computes the MD5 over headerBytes + data, matching the Python
// hashlib.md5(b'%s%s' % (header, data)) in model/packet.py.
func packetMD5(headerBytes, data []byte) [16]byte {
	h := md5.New()
	h.Write(headerBytes)
	h.Write(data)
	var sum [16]byte
	copy(sum[:], h.Sum(nil))
	return sum
}
