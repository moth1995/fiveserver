package crypto

import (
	"fmt"

	"golang.org/x/crypto/blowfish"
)

// DecryptECB decrypts data using Blowfish ECB mode with the given key.
// Mirrors Python: Blowfish.new(key, Blowfish.MODE_ECB).decrypt(data).
// data must be a multiple of 8 bytes (the Blowfish block size).
func DecryptECB(key, data []byte) ([]byte, error) {
	cipher, err := blowfish.NewCipher(key)
	if err != nil {
		return nil, fmt.Errorf("blowfish: new cipher: %w", err)
	}
	if len(data)%blowfish.BlockSize != 0 {
		return nil, fmt.Errorf("blowfish: data length %d is not a multiple of block size %d",
			len(data), blowfish.BlockSize)
	}
	out := make([]byte, len(data))
	for i := 0; i < len(data); i += blowfish.BlockSize {
		cipher.Decrypt(out[i:i+blowfish.BlockSize], data[i:i+blowfish.BlockSize])
	}
	return out, nil
}

// EncryptECB encrypts data using Blowfish ECB mode with the given key.
func EncryptECB(key, data []byte) ([]byte, error) {
	cipher, err := blowfish.NewCipher(key)
	if err != nil {
		return nil, fmt.Errorf("blowfish: new cipher: %w", err)
	}
	if len(data)%blowfish.BlockSize != 0 {
		return nil, fmt.Errorf("blowfish: data length %d is not a multiple of block size %d",
			len(data), blowfish.BlockSize)
	}
	out := make([]byte, len(data))
	for i := 0; i < len(data); i += blowfish.BlockSize {
		cipher.Encrypt(out[i:i+blowfish.BlockSize], data[i:i+blowfish.BlockSize])
	}
	return out, nil
}
