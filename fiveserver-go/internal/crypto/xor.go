package crypto

// xorKey is the 4-byte rolling key used to encrypt/decrypt all TCP traffic.
var xorKey = [4]byte{0xa6, 0x77, 0x95, 0x7c}

// XorData XORs each byte of data with the rolling 4-byte key, starting at
// offset start into the key (start % 4). This matches the Python xorData()
// in lib/fiveserver/stream.py exactly.
//
// Usage:
//
//	Incoming: decrypt the first 8 header bytes with start=0, then decrypt the
//	          full packet (header+MD5+data) with start=8.
//	Outgoing: encrypt the full serialised packet with start=0.
func XorData(data []byte, start int) []byte {
	out := make([]byte, len(data))
	for i, b := range data {
		out[i] = b ^ xorKey[(start+i)%4]
	}
	return out
}
