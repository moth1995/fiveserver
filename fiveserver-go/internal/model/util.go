package model

// PadWithZeros pads or truncates s to exactly n bytes (null-terminated
// C-string style). Mirrors Python util.padWithZeros(s, total).
func PadWithZeros(s string, n int) []byte {
	b := []byte(s)
	if len(b) > n {
		b = b[:n]
	}
	out := make([]byte, n)
	copy(out, b)
	return out
}

// StripZeros returns b up to (but not including) the first null byte.
// Mirrors Python util.stripZeros(s): finds first \0 and returns everything before it.
func StripZeros(b []byte) []byte {
	for i, c := range b {
		if c == 0 {
			return b[:i]
		}
	}
	return b
}
