package protocol

import (
	"context"
	"encoding/binary"
	"encoding/hex"
	"log"
	"math"

	"github.com/fiveserver/fiveserver-go/internal/crypto"
	"github.com/fiveserver/fiveserver-go/internal/db"
	"github.com/fiveserver/fiveserver-go/internal/model"
)

// cipherKey is the hex-encoded Blowfish key used to decrypt auth packets.
// Matches Python FiveServerConfig.cipherKey.
const cipherKey = "27501fd04e6b82c831024dac5c6305221974deb9388a21901d576cbbe2f377ef23d75486010f37819afe6c321a0146d21544ec365bf7289a"

// NewLoginDispatcher returns a Dispatcher with all LoginService handlers registered.
func NewLoginDispatcher(hub *Hub, sc *db.StorageController, version string) *Dispatcher {
	d := NewDispatcher()
	registerLoginHandlers(d, hub, sc, version)
	return d
}

func registerLoginHandlers(d *Dispatcher, hub *Hub, sc *db.StorageController, version string) {
	d.Register(0x3001, handleDo3001())
	d.Register(0x3003, handleAuthenticate(hub, sc, version))
	d.Register(0x3010, handleGetProfiles(hub, sc))
	d.Register(0x3020, handleCreateProfile(hub, sc))
	d.Register(0x3030, handleDeleteProfile(hub, sc))
	d.Register(0x3040, handleSelectProfile(hub, sc))
	d.Register(0x3050, handleDo3050())
	d.Register(0x3060, handleDo3060())
	d.Register(0x3070, handleGetMatchResults(hub, sc))
	d.Register(0x3087, handleDo3087())
	d.Register(0x308a, handleAskForSettings(hub, sc))
	d.Register(0x3088, handleDo3088())
	d.Register(0x3089, handleDo3089(hub, sc))
	d.Register(0x3090, handleDo3090())
	d.Register(0x3100, handleDo3100())
	d.Register(0x3120, handleDo3120())
	d.Register(0x0003, handleDisconnect(hub))
}

// ---- 0x3001 -----------------------------------------------------------------

func handleDo3001() HandlerFunc {
	return func(s *Session, pkt Packet) error {
		return s.Conn.SendZeros(0x3002, 16)
	}
}

// ---- 0x3003 authenticate ----------------------------------------------------

func handleAuthenticate(hub *Hub, sc *db.StorageController, version string) HandlerFunc {
	keyBytes, _ := hex.DecodeString(cipherKey)

	return func(s *Session, pkt Packet) error {
		log.Printf("[login] %s: 0x3003 authenticate — data len=%d", s.Conn.RemoteAddr, len(pkt.Data))

		// Decrypt the packet data with Blowfish ECB
		decrypted, err := crypto.DecryptECB(keyBytes, pkt.Data)
		if err != nil {
			log.Printf("[login] %s: blowfish decrypt failed: %v", s.Conn.RemoteAddr, err)
			return s.Conn.SendData(0x3004, pack32(0xffffff10))
		}

		// Extract roster hash [48:64] from decrypted data
		var clientRosterHash []byte
		if len(decrypted) >= 64 {
			clientRosterHash = decrypted[48:64]
		}

		// User hash is the hex of pkt.Data[32:48] (raw, before decryption)
		var userHash string
		if len(pkt.Data) >= 48 {
			userHash = hex.EncodeToString(pkt.Data[32:48])
		}
		log.Printf("[login] %s: userHash=%s", s.Conn.RemoteAddr, userHash)

		ctx := context.Background()
		u, err := db.GetUserByHash(ctx, sc, userHash)
		if err != nil {
			log.Printf("[login] %s: user not found (hash=%s): %v", s.Conn.RemoteAddr, userHash, err)
			return s.Conn.SendData(0x3004, pack32(0xffffff10))
		}
		log.Printf("[login] %s: user found id=%d", s.Conn.RemoteAddr, u.ID)

		// Check already online by user hash — O(1), matches Python isUserOnline(usr).
		if hub.IsUserOnline(u) {
			log.Printf("[login] %s: user id=%d already online", s.Conn.RemoteAddr, u.ID)
			return s.Conn.SendData(0x3004, pack32(0xffffff11))
		}

		// Roster hash check
		if hub.Config().Roster.EnforceHash && hasNullBlock(clientRosterHash) {
			log.Printf("[login] %s: roster hash check failed", s.Conn.RemoteAddr)
			return s.Conn.SendData(0x3004, pack32(0xffffff12))
		}

		// Load profiles
		profiles, err := db.GetProfilesByUserID(ctx, sc, u.ID)
		if err != nil {
			log.Printf("[login] %s: failed to load profiles for user id=%d: %v", s.Conn.RemoteAddr, u.ID, err)
			return s.Conn.SendData(0x3004, pack32(0xffffff10))
		}
		log.Printf("[login] %s: loaded %d profile(s) for user id=%d", s.Conn.RemoteAddr, len(profiles), u.ID)
		// Ensure exactly 3 profile slots
		for len(profiles) < 3 {
			profiles = append(profiles, &model.Profile{Ordinal: len(profiles)})
		}

		s.User = &model.ConnectedUser{
			User:        u,
			Profiles:    profiles,
			GameVersion: version,
			LobbyIndex:  -1,
			Info:        &model.UserInfo{GameName: version},
		}
		if len(clientRosterHash) > 0 {
			s.User.Info.RosterHash = hex.EncodeToString(clientRosterHash)
		}

		// Mark online by hash — mirrors Python factory.userOnline(usr).
		hub.UserOnline(s)
		// Ensure UserOffline is called when this login connection closes
		// (handles unclean drops where 0x0003 is never sent).
		s.OnClose = func() { hub.UserOffline(s) }

		return s.Conn.SendZeros(0x3004, 4)
	}
}

// ---- 0x3010 getProfiles -----------------------------------------------------

func handleGetProfiles(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil {
			return nil
		}
		cfg := hub.Config()
		data := make([]byte, 4) // 4 leading zeros

		for i, p := range s.User.Profiles {
			var games int
			if cfg.ShowStats && p.ID > 0 {
				ctx := context.Background()
				matches, err := db.GetMatchesByProfileID(ctx, sc, p.ID, 10000)
				if err == nil {
					games = len(matches)
				}
			}
			// [1] index, [4] id, [16] name, [4] playtime, [1] division, [4] points, [2] games
			entry := make([]byte, 32)
			entry[0] = byte(i)
			binary.BigEndian.PutUint32(entry[1:5], uint32(int32(p.ID)))
			copy(entry[5:21], model.PadWithZeros(p.Name, 16))
			binary.BigEndian.PutUint32(entry[21:25], uint32(int32(p.SecondsPlayed)))
			entry[25] = getDivision(p.Points)
			binary.BigEndian.PutUint32(entry[26:30], uint32(int32(p.Points)))
			binary.BigEndian.PutUint16(entry[30:32], uint16(games))
			data = append(data, entry...)
		}
		return s.Conn.SendData(0x3012, data)
	}
}

// ---- 0x3020 createProfile ---------------------------------------------------

func handleCreateProfile(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || len(pkt.Data) < 1 {
			return nil
		}
		profileIndex := int(pkt.Data[0])
		name := string(model.StripZeros(pkt.Data[1:]))
		ctx := context.Background()

		// Check name uniqueness
		existing, err := db.GetProfilesByUserID(ctx, sc, -1) // dummy check
		_ = existing
		_ = err

		// Try to create; duplicate name returns DB error
		id, err := db.CreateProfile(ctx, sc, s.User.User.ID, name, profileIndex)
		if err != nil {
			return s.Conn.SendData(0x3022, pack32(0xfffffefc))
		}

		// Refresh the profile slot
		newProfile := &model.Profile{ID: id, UserID: s.User.User.ID, Ordinal: profileIndex, Name: name}
		if profileIndex < len(s.User.Profiles) {
			s.User.Profiles[profileIndex] = newProfile
		}
		return s.Conn.SendZeros(0x3022, 4)
	}
}

// ---- 0x3030 deleteProfile ---------------------------------------------------

func handleDeleteProfile(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || len(pkt.Data) < 1 {
			return nil
		}
		profileIndex := int(pkt.Data[0])
		ctx := context.Background()

		if profileIndex < len(s.User.Profiles) {
			p := s.User.Profiles[profileIndex]
			if p != nil && p.ID > 0 {
				_ = db.DeleteProfile(ctx, sc, p.ID)
			}
			s.User.Profiles[profileIndex] = &model.Profile{Ordinal: profileIndex}
		}
		return s.Conn.SendZeros(0x3032, 4)
	}
}

// ---- 0x3040 selectProfile ---------------------------------------------------

func handleSelectProfile(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || len(pkt.Data) < 4 {
			return nil
		}
		profileID := int(int32(binary.BigEndian.Uint32(pkt.Data[0:4])))

		// Find profile in loaded list
		var found *model.Profile
		for _, p := range s.User.Profiles {
			if p != nil && p.ID == profileID {
				found = p
				break
			}
		}

		if found == nil {
			return s.Conn.SendZeros(0x3041, 4)
		}

		s.User.Profile = found
		hub.AddSession(s) // re-register now that profile is set

		// Response: [4 zeros][16-byte name][padding to 0x18e total]
		data := make([]byte, 4+16+(0x18e-20))
		copy(data[4:20], model.PadWithZeros(found.Name, 16))
		return s.Conn.SendData(0x3042, data)
	}
}

// ---- 0x3070 getMatchResults -------------------------------------------------

// MATCH_RESULT struct (per feature/pes5-last-10-matches):
//
//	[1]  unknown (0)
//	[19] date "YYYY/MM/DD HH:MM:SS" null-padded
//	[16] opponentName null-padded
//	[4]  opponentId  int32 big-endian
//	[1]  myScore
//	[1]  oppScore
//	[2]  myTeamId   uint16 big-endian
//	[2]  oppTeamId  uint16 big-endian
func handleGetMatchResults(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if len(pkt.Data) < 4 {
			return s.Conn.SendData(0x3072, pack32(0))
		}
		profileID := int(int32(binary.BigEndian.Uint32(pkt.Data[0:4])))

		// Re-select profile by ID (mirrors Python selectProfile logic in 3070)
		if s.User != nil {
			for _, p := range s.User.Profiles {
				if p != nil && p.ID == profileID {
					s.User.Profile = p
					break
				}
			}
		}

		data := pack32(0) // leading int32(0)

		if s.User == nil || s.User.Profile == nil {
			return s.Conn.SendData(0x3072, data)
		}

		ctx := context.Background()
		matches, err := db.GetMatchesByProfileID(ctx, sc, profileID, 10)
		if err != nil || len(matches) == 0 {
			return s.Conn.SendData(0x3072, data)
		}

		// Reverse: oldest first (Python iterates reversed(matches))
		for i := len(matches) - 1; i >= 0; i-- {
			m := matches[i]

			// Fetch opponent name
			oppName := "Profile not found"
			oppProfile, err := db.GetProfileByID(ctx, sc, m.OpponentProfileID)
			if err == nil && oppProfile != nil {
				oppName = oppProfile.Name
			}

			entry := make([]byte, 0, 44)
			entry = append(entry, 0) // unknown byte
			entry = append(entry, model.PadWithZeros(m.PlayedOn.UTC().Format("2006/01/02 15:04:05"), 19)...)
			entry = append(entry, model.PadWithZeros(oppName, 16)...)
			entry = append(entry, pack32i(int32(m.OpponentProfileID))...)
			entry = append(entry, byte(m.MyScore))
			entry = append(entry, byte(m.OppScore))
			entry = append(entry, byte(m.MyTeamID>>8), byte(m.MyTeamID))
			entry = append(entry, byte(m.OppTeamID>>8), byte(m.OppTeamID))
			data = append(data, entry...)
		}
		return s.Conn.SendData(0x3072, data)
	}
}

// ---- 0x308a askForSettings --------------------------------------------------

func handleAskForSettings(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if !hub.Config().StoreSettings {
			return s.Conn.SendData(0x3087, pack32(0xfffffedd))
		}
		if s.User == nil || s.User.Profile == nil {
			return s.Conn.SendData(0x3087, pack32(0xfffffedd))
		}

		ctx := context.Background()
		settings, err := db.GetProfileSettings(ctx, sc, s.User.Profile.ID)
		if err != nil || settings.Settings1 == nil || settings.Settings2 == nil {
			return s.Conn.SendData(0x3087, pack32(0xfffffedd))
		}

		// Send profile ID confirmation
		resp := append(pack32(0), pack32i(int32(s.User.Profile.ID))...)
		if err := s.Conn.SendData(0x3087, resp); err != nil {
			return err
		}
		// Send both settings blobs (already stored compressed)
		if err := s.Conn.SendData(0x3088, settings.Settings1); err != nil {
			return err
		}
		if err := s.Conn.SendData(0x3088, settings.Settings2); err != nil {
			return err
		}
		return s.Conn.SendZeros(0x3089, 0)
	}
}

// ---- misc stubs -------------------------------------------------------------

func handleDo3050() HandlerFunc {
	return func(s *Session, pkt Packet) error { return s.Conn.SendZeros(0x3052, 0x47) }
}

func handleDo3060() HandlerFunc {
	return func(s *Session, pkt Packet) error { return s.Conn.SendData(0x3062, []byte{0}) }
}

func handleDo3087() HandlerFunc {
	// "Exit match series" — match recording handled in main_service; stub here
	return func(s *Session, pkt Packet) error { return nil }
}

func handleDo3088() HandlerFunc {
	// Settings update — store compressed blob on profile in memory
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.Profile == nil || len(pkt.Data) < 3 {
			return nil
		}
		if pkt.Data[2] == 3 {
			s.User.Profile.Settings.Settings1 = append([]byte(nil), pkt.Data...)
		} else {
			s.User.Profile.Settings.Settings2 = append([]byte(nil), pkt.Data...)
		}
		return nil
	}
}

func handleDo3089(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if err := s.Conn.SendZeros(0x308b, 4); err != nil {
			return err
		}
		if hub.Config().StoreSettings && s.User != nil && s.User.Profile != nil {
			ctx := context.Background()
			_ = db.StoreProfileSettings(ctx, sc, s.User.Profile.ID, &s.User.Profile.Settings)
		}
		return nil
	}
}

func handleDo3090() HandlerFunc {
	return func(s *Session, pkt Packet) error { return s.Conn.SendZeros(0x3091, 4) }
}

func handleDo3100() HandlerFunc {
	return func(s *Session, pkt Packet) error { return s.Conn.SendZeros(0x3101, 4) }
}

func handleDo3120() HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if err := s.Conn.SendZeros(0x3121, 4); err != nil {
			return err
		}
		return s.Conn.SendZeros(0x3123, 0)
	}
}

func handleDisconnect(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		s.OnClose = nil // prevent double-cleanup from serveConn defer
		if s.User != nil {
			hub.UserOffline(s)
		}
		return nil
	}
}

// ---- helpers ----------------------------------------------------------------

func pack32(v uint32) []byte {
	b := make([]byte, 4)
	binary.BigEndian.PutUint32(b, v)
	return b
}

func pack32i(v int32) []byte {
	return pack32(uint32(v))
}

// hasNullBlock returns true if b contains 4 consecutive zero bytes.
// Mirrors Python: clientRosterHash.find(b'\0\0\0\0') != -1
func hasNullBlock(b []byte) bool {
	for i := 0; i+3 < len(b); i++ {
		if b[i] == 0 && b[i+1] == 0 && b[i+2] == 0 && b[i+3] == 0 {
			return true
		}
	}
	return false
}

// getDivision mirrors Python RatingMath.getDivision(points).
func getDivision(points int) byte {
	thresholds := [4]int{250, 450, 600, 750}
	for div, t := range thresholds {
		if points < t {
			return byte(div)
		}
	}
	return 4
}

// getPoints mirrors Python RatingMath.getPoints(stats) with w1=0.44, w2=0.56.
func getPoints(wins, losses, draws int) int {
	numGames := wins + draws + losses
	var perf float64
	if numGames > 0 {
		perf = (float64(wins) + 0.333*float64(draws)) / float64(numGames)
	}
	score := 0.56 + 0.44*perf*perf + 0.56*(-math.Exp(-float64(numGames)*0.05))
	return int(1000 * score)
}

