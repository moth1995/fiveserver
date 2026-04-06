package protocol

import (
	"bytes"
	"testing"
)

func TestPacket_RoundTrip(t *testing.T) {
	p := Packet{
		Header: Header{ID: 0x2008, Length: 4, PacketCount: 1},
		Data:   []byte{0x01, 0x02, 0x03, 0x04},
	}
	got, err := Unmarshal(Marshal(p))
	if err != nil {
		t.Fatalf("round-trip error: %v", err)
	}
	if got.Header != p.Header {
		t.Fatalf("header mismatch: got %+v want %+v", got.Header, p.Header)
	}
	if !bytes.Equal(got.Data, p.Data) {
		t.Fatalf("data mismatch: got %x want %x", got.Data, p.Data)
	}
}

func TestPacket_EmptyData(t *testing.T) {
	p := Packet{
		Header: Header{ID: 0x0005, Length: 0, PacketCount: 99},
		Data:   []byte{},
	}
	got, err := Unmarshal(Marshal(p))
	if err != nil {
		t.Fatalf("empty data round-trip error: %v", err)
	}
	if got.Header != p.Header {
		t.Fatalf("header mismatch: got %+v want %+v", got.Header, p.Header)
	}
	if len(got.Data) != 0 {
		t.Fatalf("expected empty data, got %x", got.Data)
	}
}

func TestPacket_MD5Corruption(t *testing.T) {
	p := Packet{
		Header: Header{ID: 0x3003, Length: 3, PacketCount: 5},
		Data:   []byte{0xAA, 0xBB, 0xCC},
	}
	wire := Marshal(p)
	wire[25] ^= 0xFF // flip a bit in the data
	if _, err := Unmarshal(wire); err == nil {
		t.Fatal("expected MD5 mismatch error, got nil")
	}
}

func TestPacket_HeaderBigEndian(t *testing.T) {
	h := Header{ID: 0x200A, Length: 0x0010, PacketCount: 0x00000003}
	b := MarshalHeader(h)
	want := []byte{0x20, 0x0A, 0x00, 0x10, 0x00, 0x00, 0x00, 0x03}
	if !bytes.Equal(b, want) {
		t.Fatalf("big-endian encoding wrong: got %x want %x", b, want)
	}
}

func TestPacket_MD5OverHeaderAndData(t *testing.T) {
	// Verify that the MD5 covers header bytes, not just data.
	// Two packets with same data but different headers must have different MD5s.
	d := []byte{0x01, 0x02}
	p1 := Packet{Header: Header{ID: 0x0001, Length: 2, PacketCount: 1}, Data: d}
	p2 := Packet{Header: Header{ID: 0x0002, Length: 2, PacketCount: 1}, Data: d}
	w1, w2 := Marshal(p1), Marshal(p2)
	if bytes.Equal(w1[8:24], w2[8:24]) {
		t.Fatal("different headers produced identical MD5 — header not included in hash")
	}
}
