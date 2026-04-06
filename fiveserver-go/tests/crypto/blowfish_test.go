package crypto_test

import (
	"bytes"
	"testing"

	"github.com/fiveserver/fiveserver-go/internal/crypto"
)

func TestDecryptECB_RoundTrip(t *testing.T) {
	key := []byte("secretkey")
	plaintext := []byte("12345678abcdefgh") // 16 bytes = 2 blocks

	encrypted, err := crypto.EncryptECB(key, plaintext)
	if err != nil {
		t.Fatalf("EncryptECB: %v", err)
	}

	decrypted, err := crypto.DecryptECB(key, encrypted)
	if err != nil {
		t.Fatalf("DecryptECB: %v", err)
	}

	if !bytes.Equal(plaintext, decrypted) {
		t.Errorf("round-trip mismatch: got %x, want %x", decrypted, plaintext)
	}
}

func TestDecryptECB_UnalignedData_ReturnsError(t *testing.T) {
	key := []byte("key")
	data := []byte("12345") // 5 bytes — not multiple of 8
	_, err := crypto.DecryptECB(key, data)
	if err == nil {
		t.Fatal("expected error for unaligned data, got nil")
	}
}

func TestEncryptECB_UnalignedData_ReturnsError(t *testing.T) {
	key := []byte("key")
	data := []byte("123") // 3 bytes — not multiple of 8
	_, err := crypto.EncryptECB(key, data)
	if err == nil {
		t.Fatal("expected error for unaligned data, got nil")
	}
}

func TestDecryptECB_DifferentKeys_DifferentOutput(t *testing.T) {
	key1 := []byte("key11111")
	key2 := []byte("key22222")
	data := []byte("12345678") // 8 bytes

	enc1, err := crypto.EncryptECB(key1, data)
	if err != nil {
		t.Fatalf("EncryptECB key1: %v", err)
	}

	enc2, err := crypto.EncryptECB(key2, data)
	if err != nil {
		t.Fatalf("EncryptECB key2: %v", err)
	}

	if bytes.Equal(enc1, enc2) {
		t.Error("different keys produced same ciphertext")
	}
}

func TestDecryptECB_SingleBlock(t *testing.T) {
	key := []byte("myblowfishkey")
	data := make([]byte, 8) // one block of zeros

	enc, err := crypto.EncryptECB(key, data)
	if err != nil {
		t.Fatalf("EncryptECB: %v", err)
	}
	if len(enc) != 8 {
		t.Fatalf("expected 8 bytes output, got %d", len(enc))
	}

	dec, err := crypto.DecryptECB(key, enc)
	if err != nil {
		t.Fatalf("DecryptECB: %v", err)
	}
	if !bytes.Equal(dec, data) {
		t.Errorf("single block round-trip failed: %x != %x", dec, data)
	}
}

func TestDecryptECB_MultipleBlocks(t *testing.T) {
	key := []byte("testkey")
	// 4 blocks = 32 bytes; each block independent in ECB mode
	data := []byte("AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH")

	enc, err := crypto.EncryptECB(key, data)
	if err != nil {
		t.Fatalf("EncryptECB: %v", err)
	}
	dec, err := crypto.DecryptECB(key, enc)
	if err != nil {
		t.Fatalf("DecryptECB: %v", err)
	}
	if !bytes.Equal(dec, data) {
		t.Errorf("multi-block round-trip failed")
	}
}

func TestDecryptECB_EmptyData_ReturnsError(t *testing.T) {
	// 0 bytes is a multiple of 8 (0 mod 8 == 0), so it should succeed with empty output
	key := []byte("key")
	out, err := crypto.DecryptECB(key, []byte{})
	if err != nil {
		t.Fatalf("empty data should succeed: %v", err)
	}
	if len(out) != 0 {
		t.Errorf("expected empty output, got %d bytes", len(out))
	}
}
