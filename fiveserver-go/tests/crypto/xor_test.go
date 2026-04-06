package crypto_test

import (
	"bytes"
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/crypto"
)

func TestXorData_RoundTrip(t *testing.T) {
	input := []byte{0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80}
	if got := crypto.XorData(crypto.XorData(input, 0), 0); !bytes.Equal(got, input) {
		t.Fatalf("round-trip failed: got %x, want %x", got, input)
	}
}

func TestXorData_KnownFixture_Start0(t *testing.T) {
	// XOR [0x00,0x01,0x02,0x03] with key [0xa6,0x77,0x95,0x7c] at offset 0
	input := []byte{0x00, 0x01, 0x02, 0x03}
	want := []byte{0xa6, 0x76, 0x97, 0x7f}
	if got := crypto.XorData(input, 0); !bytes.Equal(got, want) {
		t.Fatalf("start=0 fixture failed: got %x, want %x", got, want)
	}
}

func TestXorData_AllZerosKey_Start0(t *testing.T) {
	// XOR 8 zero bytes with start=0 should reproduce two full key cycles
	input := make([]byte, 8)
	want := []byte{0xa6, 0x77, 0x95, 0x7c, 0xa6, 0x77, 0x95, 0x7c}
	if got := crypto.XorData(input, 0); !bytes.Equal(got, want) {
		t.Fatalf("8-zero start=0 failed: got %x, want %x", got, want)
	}
}

func TestXorData_KeyXorSelf_Start0(t *testing.T) {
	// XOR-ing the key with itself should produce zeros
	input := []byte{0xa6, 0x77, 0x95, 0x7c}
	want := []byte{0x00, 0x00, 0x00, 0x00}
	if got := crypto.XorData(input, 0); !bytes.Equal(got, want) {
		t.Fatalf("self-XOR failed: got %x, want %x", got, want)
	}
}

func TestXorData_OffsetWrap(t *testing.T) {
	// start=8 wraps to key offset 0 (8 % 4 == 0), same result as start=0
	input := make([]byte, 4)
	if got0, got8 := crypto.XorData(input, 0), crypto.XorData(input, 8); !bytes.Equal(got0, got8) {
		t.Fatalf("start=8 should equal start=0: got0=%x got8=%x", got0, got8)
	}
}

func TestXorData_Start1(t *testing.T) {
	// start=1: first key byte is 0x77
	input := []byte{0x00, 0x00, 0x00, 0x00}
	want := []byte{0x77, 0x95, 0x7c, 0xa6}
	if got := crypto.XorData(input, 1); !bytes.Equal(got, want) {
		t.Fatalf("start=1 failed: got %x, want %x", got, want)
	}
}
