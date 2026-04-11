package protocol

import (
	"context"
	"encoding/binary"
	"log"
	"time"

	"github.com/fiveserver/fiveserver-go/internal/db"
	"github.com/fiveserver/fiveserver-go/internal/model"
)

// NewMainServiceDispatcher creates a Dispatcher that includes all LoginService
// and NetworkMenuService handlers plus MainService-specific ones.
// Mirrors Python MainService.register() which calls NetworkMenuService.register() first.
func NewMainServiceDispatcher(hub *Hub, sc *db.StorageController, version string) *Dispatcher {
	d := NewMenuDispatcher(hub, sc, version)
	registerMainServiceHandlers(d, hub, sc)
	return d
}

func registerMainServiceHandlers(d *Dispatcher, hub *Hub, sc *db.StorageController) {
	d.Register(0x4310, handleCreateRoom4310(hub))
	d.Register(0x432a, handleExitRoom432a(hub))
	d.Register(0x4364, handleSetMatchTime4364(hub))
	d.Register(0x4366, handleSelectTeam4366(hub))
	d.Register(0x4368, handleGoalScored4368(hub))
	d.Register(0x4370, handleMatchExit4370(hub))
	d.Register(0x4400, handleChat4400(hub))
	d.Register(0x4b00, handlePing4b00(hub))
	d.Register(0x4320, handleChallenge4320(hub, sc))
	d.Register(0x4323, handleChallengeResponse4323(hub))
	d.Register(0x4325, handleCancelChallenge4325(hub))
	d.Register(0x4350, handleRelayRoomSettings4350())
	d.Register(0x4360, handleToggleReady4360(hub))
	d.Register(0x3087, handleMatchSeriesExit3087(hub, sc)) // override login stub
	d.Register(0x0003, handleMainDisconnect(hub, sc))      // override menu disconnect
}

// ---- 0x4310 createRoom ------------------------------------------------------

func handleCreateRoom4310(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.Profile == nil || s.User.State == nil {
			return nil
		}
		lobby, ok := hub.GetLobby(s.User.LobbyIndex)
		if !ok {
			return nil
		}

		roomName := string(model.StripZeros(pkt.Data[0:32]))
		if _, exists := lobby.GetRoomByName(roomName); exists {
			return s.Conn.SendData(0x4311, []byte{0xff, 0xff, 0xff, 0x10})
		}

		room := model.NewRoom(lobby)
		room.Name = roomName
		if len(pkt.Data) > 33 {
			room.UsePassword = pkt.Data[33] == 1
		}
		if room.UsePassword && len(pkt.Data) > 34 {
			room.Password = string(model.StripZeros(pkt.Data[34:50]))
		}

		room.Enter(s.User)
		lobby.AddRoom(room)
		log.Printf("[main] Room created: %q (id=%d) by %s", room.Name, room.ID, s.User.Profile.Name)

		// Notify all lobby members of new room
		roomData := encodeRoomUpdate(room, false)
		playerData := formatPlayerInfo(s.User, room.ID)
		for _, u := range lobby.Players() {
			sendToUser(hub, u, 0x4306, roomData)
			sendToUser(hub, u, 0x4222, playerData)
		}
		return s.Conn.SendZeros(0x4311, 4)
	}
}

// ---- 0x432a exitRoom --------------------------------------------------------

func handleExitRoom432a(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.Profile == nil || s.User.State == nil {
			return s.Conn.SendZeros(0x432b, 4)
		}
		if !s.User.State.InRoom || s.User.State.Room == nil {
			return s.Conn.SendZeros(0x432b, 4)
		}

		room := s.User.State.Room
		lobby, ok := hub.GetLobby(s.User.LobbyIndex)
		if !ok {
			return s.Conn.SendZeros(0x432b, 4)
		}

		room.Exit(s.User)

		// Notify room owner if anyone remains
		if !room.IsEmpty() && room.Owner != nil {
			exitData := append(
				model.PadWithZeros(s.User.Profile.Name, 16),
				model.PadWithZeros(room.Name, 32)...,
			)
			sendToUser(hub, room.Owner, 0x4331, exitData)
			s.User.NeedsLobbyChatReplay = true
		}

		// Send updated room info to all lobby members
		roomData := encodeRoomUpdate(room, true)
		playerData := formatPlayerInfo(s.User, room.ID)
		for _, u := range lobby.Players() {
			sendToUser(hub, u, 0x4306, roomData)
			sendToUser(hub, u, 0x4222, playerData)
		}

		if err := s.Conn.SendZeros(0x432b, 4); err != nil {
			return err
		}

		// Destroy empty room
		if room.IsEmpty() {
			roomIDData := pack32i(int32(room.ID))
			for _, u := range lobby.Players() {
				sendToUser(hub, u, 0x4305, roomIDData)
			}
			lobby.DeleteRoom(room)
		}

		// Replay chat history if needed
		if s.User.NeedsLobbyChatReplay {
			s.User.NeedsLobbyChatReplay = false
			time.AfterFunc(chatHistoryDelay, func() {
				sendChatHistory(lobby, s)
			})
		}
		return nil
	}
}

// ---- 0x4364 setMatchTime ----------------------------------------------------

func handleSetMatchTime4364(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.State == nil || s.User.State.Room == nil {
			return s.Conn.SendZeros(0x4365, 4)
		}
		if len(pkt.Data) < 1 {
			return s.Conn.SendZeros(0x4365, 4)
		}
		matchTime := int(pkt.Data[0]) * 5
		room := s.User.State.Room
		room.MatchTime = matchTime

		lobby, ok := hub.GetLobby(s.User.LobbyIndex)
		if ok {
			roomData := encodeRoomUpdate(room, true)
			for _, u := range lobby.Players() {
				sendToUser(hub, u, 0x4306, roomData)
			}
		}
		return s.Conn.SendZeros(0x4365, 4)
	}
}

// ---- 0x4366 selectTeam ------------------------------------------------------

func handleSelectTeam4366(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.Profile == nil || s.User.State == nil || s.User.State.Room == nil {
			return s.Conn.SendData(0x4367, []byte{0, 0, 0, 1})
		}
		if len(pkt.Data) < 2 {
			return s.Conn.SendData(0x4367, []byte{0, 0, 0, 1})
		}
		team := int(binary.BigEndian.Uint16(pkt.Data[0:2]))
		room := s.User.State.Room

		// Create or update match structure
		if room.Match == nil {
			room.Match = &model.Match{}
		}
		if room.IsOwner(s.User) {
			room.Match.HomeProfileID = s.User.Profile.ID
			room.Match.HomeTeamID = team
		} else {
			room.Match.AwayProfileID = s.User.Profile.ID
			room.Match.AwayTeamID = team
		}
		s.User.State.TeamID = team
		log.Printf("[main] Team selected: %d by %s", team, s.User.Profile.Name)
		if room.Match != nil && room.Match.HomeProfileID != 0 && room.Match.AwayProfileID != 0 {
			log.Printf("[main] NEW MATCH starting: Team %d vs Team %d",
				room.Match.HomeTeamID, room.Match.AwayTeamID)
		}
		return s.Conn.SendData(0x4367, []byte{0, 0, 0, 1})
	}
}

// ---- 0x4368 goalScored ------------------------------------------------------

func handleGoalScored4368(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.State == nil || s.User.State.Room == nil {
			return s.Conn.SendData(0x4369, []byte{0, 0, 0, 0})
		}
		room := s.User.State.Room
		if room.Match == nil {
			return s.Conn.SendData(0x4369, []byte{0, 0, 0, 0})
		}
		if len(pkt.Data) > 0 && pkt.Data[0] == 0 {
			room.Match.ScoreHome++
			log.Printf("[main] GOAL by HOME team %d — score %d:%d", room.Match.HomeTeamID, room.Match.ScoreHome, room.Match.ScoreAway)
		} else {
			room.Match.ScoreAway++
			log.Printf("[main] GOAL by AWAY team %d — score %d:%d", room.Match.AwayTeamID, room.Match.ScoreHome, room.Match.ScoreAway)
		}
		return s.Conn.SendData(0x4369, []byte{0, 0, 0, 0})
	}
}

// ---- 0x4370 matchExit -------------------------------------------------------

func handleMatchExit4370(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.State == nil || s.User.State.Room == nil {
			return s.Conn.SendData(0x4371, []byte{0, 0, 0, 0})
		}
		room := s.User.State.Room
		if room.Match != nil && len(pkt.Data) >= 2 {
			exitType := pkt.Data[1]
			if pkt.Data[0] == 0 {
				room.Match.HomeExit = exitType
			} else {
				room.Match.AwayExit = exitType
			}
		}
		return s.Conn.SendData(0x4371, []byte{0, 0, 0, 0})
	}
}

// ---- 0x4400 chat (main service override) ------------------------------------
// Python MainService.chat_4400 handles lobby, room, and private chat.

func handleChat4400(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.Profile == nil || s.User.State == nil {
			log.Printf("[chat] %s: 0x4400 dropped — user/profile/state nil", s.Conn.RemoteAddr)
			return nil
		}
		lobby, ok := hub.GetLobby(s.User.LobbyIndex)
		if !ok {
			log.Printf("[chat] %s: 0x4400 dropped — lobbyIndex=%d not found", s.Conn.RemoteAddr, s.User.LobbyIndex)
			return nil
		}
		if len(pkt.Data) < 10 {
			log.Printf("[chat] %s: 0x4400 dropped — pkt too short (%d bytes)", s.Conn.RemoteAddr, len(pkt.Data))
			return nil
		}

		cfg := hub.Config()
		chatType := pkt.Data[0:2]
		rawMsg := model.StripZeros(pkt.Data[10:])
		text := FilterChat(string(rawMsg), cfg.Chat.BannedWords, cfg.Chat.WarningMessage)
		log.Printf("[chat] %s {%s}: type=%02x%02x msg=%q lobby=%s players=%d",
			s.Conn.RemoteAddr, s.User.Profile.Name, chatType[0], chatType[1], text, lobby.Name, len(lobby.Players()))

		pktData := buildChatPacket(
			chatType[0:1],
			pkt.Data[2:6],
			s.User.Profile,
			text,
		)

		switch {
		case chatType[0] == 0x00 && chatType[1] == 0x01:
			// Lobby chat: broadcast + add to history
			lobby.AddChatMessage(model.NewChatMessage(s.User.Profile, text))
			for _, u := range lobby.Players() {
				log.Printf("[chat] broadcasting to {%s}", u.Profile.Name)
				sendToUser(hub, u, 0x4402, pktData)
			}

		case chatType[0] == 0x01 && chatType[1] == 0x02:
			// Room chat — snapshot Players to avoid race with concurrent enter/exit
			if s.User.State.Room != nil {
				players := append([]*model.ConnectedUser(nil), s.User.State.Room.Players...)
				for _, u := range players {
					sendToUser(hub, u, 0x4402, pktData)
				}
			}

		case chatType[0] == 0x00 && chatType[1] == 0x02:
			// Private message
			if len(pkt.Data) < 10 {
				return nil
			}
			targetProfileID := int(int32(binary.BigEndian.Uint32(pkt.Data[6:10])))
			target := lobby.GetPlayerByProfileID(targetProfileID)
			if target != nil {
				msg := model.NewChatMessage(s.User.Profile, text)
				msg.To = target.Profile
				msg.Special = append([]byte(nil), pkt.Data[2:6]...)
				lobby.AddChatMessage(msg)
				sendToUser(hub, target, 0x4402, pktData)
				if target.Profile != nil && target.Profile.ID != s.User.Profile.ID {
					_ = s.Conn.SendData(0x4402, pktData) // echo to self
				}
			}
		}
		return nil
	}
}

// ---- 0x4b00 ping ------------------------------------------------------------

func handlePing4b00(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if len(pkt.Data) < 4 {
			return s.Conn.SendData(0x4b01, []byte{0xff, 0xff, 0xff, 0xff})
		}
		profileID := int(int32(binary.BigEndian.Uint32(pkt.Data[0:4])))
		lobby, ok := hub.GetLobby(s.User.LobbyIndex)
		if !ok {
			return s.Conn.SendData(0x4b01, []byte{0xff, 0xff, 0xff, 0xff})
		}
		target := lobby.GetPlayerByProfileID(profileID)
		if target == nil || target.State == nil {
			return s.Conn.SendData(0x4b01, []byte{0xff, 0xff, 0xff, 0xff})
		}

		// [4 zeros][16 ip1][2 port1][16 ip2][2 port2][4 profileID]
		data := pack32(0)
		data = append(data, model.PadWithZeros(string(target.State.IP1), 16)...)
		data = append(data, byte(target.State.UDPPort1>>8), byte(target.State.UDPPort1))
		data = append(data, model.PadWithZeros(string(target.State.IP2), 16)...)
		data = append(data, byte(target.State.UDPPort2>>8), byte(target.State.UDPPort2))
		data = append(data, pack32i(int32(target.Profile.ID))...)
		return s.Conn.SendData(0x4b01, data)
	}
}

// ---- 0x4320 challenge -------------------------------------------------------

func handleChallenge4320(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.Profile == nil || s.User.State == nil {
			return s.Conn.SendData(0x4321, []byte{0, 0, 0, 1})
		}
		if len(pkt.Data) < 21 {
			return s.Conn.SendData(0x4321, []byte{0, 0, 0, 1})
		}
		roomID := int(int32(binary.BigEndian.Uint32(pkt.Data[0:4])))
		ping := pkt.Data[20:21]

		lobby, ok := hub.GetLobby(s.User.LobbyIndex)
		if !ok {
			return s.Conn.SendData(0x4321, []byte{0, 0, 0, 1})
		}
		room := lobby.GetRoomByID(roomID)
		if room == nil || room.Owner == nil {
			return s.Conn.SendData(0x4321, []byte{0, 0, 0, 1})
		}

		// Check game version compatibility — mirrors Python isSameGame:
		// the in-game version byte from 0x4200 must match between players.
		if room.Owner.GameVersion != s.User.GameVersion {
			log.Printf("[main] INFO: Game version mismatch (%d vs %d). Match CANCELLED.",
				room.Owner.GameVersion, s.User.GameVersion)
			return s.Conn.SendData(0x4321, []byte{0, 0, 0, 1})
		}

		// Check roster hash if lobby enforces it
		if lobby.CheckRosterHash && hub.Config().Roster.CompareHash {
			if s.User.Info != nil && room.Owner.Info != nil {
				if s.User.Info.RosterHash != room.Owner.Info.RosterHash {
					log.Printf("[main] INFO: Roster-hash mismatch: %s != %s. Match CANCELLED.",
						s.User.Profile.Name, room.Owner.Profile.Name)
					return s.Conn.SendData(0x4321, []byte{0, 0, 0, 1})
				}
			}
		}

		// Enter room as challenger
		room.Enter(s.User)

		// Notify lobby
		roomData := encodeRoomUpdate(room, true)
		playerData := formatPlayerInfo(s.User, room.ID)
		for _, u := range lobby.Players() {
			sendToUser(hub, u, 0x4306, roomData)
			sendToUser(hub, u, 0x4222, playerData)
		}

		// Fetch challenger's stats and send challenge to room owner
		ctx := context.Background()
		stats, err := db.GetStatsByProfileID(ctx, sc, s.User.Profile.ID)
		if err != nil {
			stats = &model.Stats{}
		}
		profInfo := formatProfileInfo(s.User.Profile, stats, hub.Config().ShowStats)
		// [profileInfo padded to 0x57][1 ping][2 zeros]
		challengeData := make([]byte, 0x57)
		copy(challengeData, profInfo)
		challengeData = append(challengeData, ping[0], 0, 0)

		// Store challenger in room.MatchStarter so challengeResponse_4323 can find it.
		// Python: usr.challenger = self._user (stored on the owner's protocol instance).
		room.MatchStarter = s.User

		sendToUser(hub, room.Owner, 0x4322, challengeData)
		return nil // no response to challenger until owner responds
	}
}

// ---- 0x4323 challengeResponse -----------------------------------------------

func handleChallengeResponse4323(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.Profile == nil || s.User.State == nil || s.User.State.Room == nil {
			return nil
		}
		if len(pkt.Data) < 1 {
			return nil
		}
		accepted := pkt.Data[0] == 1
		room := s.User.State.Room
		challenger := room.MatchStarter // set during challenge_4320

		if challenger == nil || challenger.Profile == nil {
			return nil
		}

		challengerSess, hasSess := hub.GetSession(challenger.Profile.Name)
		_ = hasSess

		lobby, ok := hub.GetLobby(s.User.LobbyIndex)
		if !ok {
			return nil
		}

		if accepted {
			challenger.NeedsLobbyChatReplay = true

			// "Ultimate packet" to challenger — bytes 5..0x50 in sequence
			ultimate := make([]byte, 4+0x4c)
			for i := range ultimate[4:] {
				ultimate[4+i] = byte(i + 5)
			}
			if challengerSess != nil {
				_ = challengerSess.Conn.SendData(0x4321, ultimate)
			}

			// Send match confirmation to owner
			data := append(
				model.PadWithZeros(challenger.Profile.Name, 16),
				model.PadWithZeros(room.Name, 32)...,
			)
			_ = s.Conn.SendData(0x4330, data)

			// Mark both players as no-lobby-chat (Python sets to 0 / 0xff — using 0)
			if s.User.State != nil {
				s.User.State.NoLobbyChat = 0
			}
			if challenger.State != nil {
				challenger.State.NoLobbyChat = 0
			}

			// Broadcast updated player info
			ownerData := formatPlayerInfo(s.User, room.ID)
			chalData := formatPlayerInfo(challenger, room.ID)
			for _, u := range lobby.Players() {
				sendToUser(hub, u, 0x4222, ownerData)
				sendToUser(hub, u, 0x4222, chalData)
			}
		} else {
			// Rejected: exit challenger from room
			room.Exit(challenger)

			roomData := encodeRoomUpdate(room, true)
			for _, u := range lobby.Players() {
				sendToUser(hub, u, 0x4306, roomData)
			}
			chalData := formatPlayerInfo(challenger, 0)
			for _, u := range lobby.Players() {
				sendToUser(hub, u, 0x4222, chalData)
			}
			// Notify challenger of rejection
			if challengerSess != nil {
				_ = challengerSess.Conn.SendData(0x4321, []byte{0, 0, 0, 1})
			}
		}
		return nil
	}
}

// ---- 0x4325 cancelChallenge -------------------------------------------------

func handleCancelChallenge4325(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.State == nil {
			return s.Conn.SendZeros(0x4326, 4)
		}
		if !s.User.State.InRoom || s.User.State.Room == nil {
			return s.Conn.SendZeros(0x4326, 4)
		}

		room := s.User.State.Room
		lobby, ok := hub.GetLobby(s.User.LobbyIndex)
		if !ok {
			return s.Conn.SendZeros(0x4326, 4)
		}

		room.Exit(s.User)

		// Notify room owner
		if !room.IsEmpty() && room.Owner != nil {
			sendToUser(hub, room.Owner, 0x4324, []byte{0, 0, 0, 0})
		}

		// Broadcast room update
		roomData := encodeRoomUpdate(room, true)
		playerData := formatPlayerInfo(s.User, room.ID)
		for _, u := range lobby.Players() {
			sendToUser(hub, u, 0x4306, roomData)
			sendToUser(hub, u, 0x4222, playerData)
		}

		if err := s.Conn.SendZeros(0x4326, 4); err != nil {
			return err
		}

		// Destroy empty room
		if room.IsEmpty() {
			roomIDData := pack32i(int32(room.ID))
			for _, u := range lobby.Players() {
				sendToUser(hub, u, 0x4305, roomIDData)
			}
			lobby.DeleteRoom(room)
		}
		return nil
	}
}

// ---- 0x4350 relayRoomSettings -----------------------------------------------

func handleRelayRoomSettings4350() HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.State == nil || s.User.State.Room == nil {
			return nil
		}
		// Forward settings packet to all other room members
		for _, u := range s.User.State.Room.Players {
			if u.Profile != nil && u.Profile.ID == s.User.Profile.ID {
				continue
			}
			if u.Conn != nil {
				if cs, ok := u.Conn.(*ConnSender); ok {
					_ = cs.SendData(0x4350, pkt.Data)
				}
			}
		}
		return nil
	}
}

// ---- 0x4360 toggleReady -----------------------------------------------------

func handleToggleReady4360(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.State == nil || s.User.State.Room == nil {
			return s.Conn.SendZeros(0x4361, 4)
		}
		if len(pkt.Data) < 1 {
			return s.Conn.SendZeros(0x4361, 4)
		}
		ready := pkt.Data[0] == 1
		room := s.User.State.Room

		if ready {
			room.ReadyCount++
		} else {
			room.ReadyCount--
		}

		// Relay to other room members
		for _, u := range room.Players {
			if u.Profile != nil && u.Profile.ID == s.User.Profile.ID {
				continue
			}
			if u.Conn != nil {
				if cs, ok := u.Conn.(*ConnSender); ok {
					_ = cs.SendData(0x4362, pkt.Data)
				}
			}
		}

		if err := s.Conn.SendZeros(0x4361, 4); err != nil {
			return err
		}

		// When both players ready: start match
		if room.ReadyCount == 2 {
			for _, u := range room.Players {
				u.NeedsLobbyChatReplay = true
				if u.Conn != nil {
					if cs, ok := u.Conn.(*ConnSender); ok {
						_ = cs.SendData(0x4344, []byte{4})
					}
				}
			}
			room.ReadyCount = 0
			if room.Match != nil && room.Match.StartTime.IsZero() {
				room.Match.StartTime = time.Now()
			}
		}
		return nil
	}
}

// ---- 0x3087 matchSeriesExit (override) --------------------------------------
// Python do_3087: called at match-series exit. Finalises match result and
// stores it in DB if the lobby tracks stats.

func handleMatchSeriesExit3087(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.State == nil || s.User.State.Room == nil {
			return nil
		}
		room := s.User.State.Room
		if room.Match == nil {
			return nil
		}

		// Atomically take ownership of the match result
		match := room.Match
		room.Match = nil

		homeExitByte, _ := match.HomeExit.(byte)
		awayExitByte, _ := match.AwayExit.(byte)

		// Mutual disconnect: disregard
		if homeExitByte == 1 && awayExitByte == 1 {
			log.Printf("[main] MUTUAL DISCONNECT: Team %d vs Team %d — %d:%d. Match DISREGARDED.",
				match.HomeTeamID, match.AwayTeamID, match.ScoreHome, match.ScoreAway)
			return nil
		}

		// Only record if lobby is not no-stats type
		lobby, ok := hub.GetLobby(s.User.LobbyIndex)
		if !ok || lobby.TypeCode == 0x20 {
			return nil
		}

		log.Printf("[main] MATCH FINISHED: Team %d vs Team %d — %d:%d",
			match.HomeTeamID, match.AwayTeamID, match.ScoreHome, match.ScoreAway)

		ctx := context.Background()
		dbMatch := &model.Match{
			HomeProfileID: match.HomeProfileID,
			AwayProfileID: match.AwayProfileID,
			HomeTeamID:    match.HomeTeamID,
			AwayTeamID:    match.AwayTeamID,
			ScoreHome:     match.ScoreHome,
			ScoreAway:     match.ScoreAway,
			PlayedOn:      time.Now(),
		}
		if _, err := db.RecordMatch(ctx, sc, dbMatch); err != nil {
			log.Printf("[main] ERROR recording match: %v", err)
			return nil
		}

		if !hub.Config().ShowStats {
			return nil
		}

		// Gap 2: compute match duration for SecondsPlayed accounting
		var matchDuration int64
		if !match.StartTime.IsZero() {
			matchDuration = int64(time.Since(match.StartTime).Seconds())
		}

		// Recompute points and accumulate seconds_played for both profiles
		for _, profileID := range []int{match.HomeProfileID, match.AwayProfileID} {
			p, err := db.GetProfileByID(ctx, sc, profileID)
			if err != nil {
				continue
			}
			stats, err := db.GetStatsByProfileID(ctx, sc, profileID)
			if err != nil {
				continue
			}
			p.Points = getPoints(stats.Wins, stats.Losses, stats.Draws)
			p.SecondsPlayed += matchDuration
			_ = db.UpdateProfileStats(ctx, sc, p)
		}
		// Gap 3: recompute rank column asynchronously after each match
		go func() {
			if err := db.ComputeRanks(context.Background(), sc); err != nil {
				log.Printf("[main] ComputeRanks failed: %v", err)
			}
		}()
		return nil
	}
}

// ---- 0x0003 disconnect (main service override) ------------------------------
// Python connectionLost: handles in-match disconnect, applies CountAsLoss penalty,
// exits room, exits lobby, and notifies remaining players.

func handleMainDisconnect(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User != nil && s.User.Profile != nil {
			log.Printf("[main] User {%s} disconnected", s.User.Profile.Name)
		}
		if s.User != nil && s.User.State != nil {
			applyDisconnectPenalty(hub, sc, s)
		}
		exitLobbyAndNotify(hub, s)
		if s.User != nil {
			hub.RemoveSession(s)
		}
		return nil
	}
}

// applyDisconnectPenalty mirrors Python connectionLost disconnect logic.
// If the user was in a room with an active match, increments their disconnect count,
// and if CountAsLoss is enabled, sets the match score as a loss for the disconnecting player.
func applyDisconnectPenalty(hub *Hub, sc *db.StorageController, s *Session) {
	if s.User.State == nil || !s.User.State.InRoom || s.User.State.Room == nil {
		return
	}
	room := s.User.State.Room
	if room.Match == nil {
		return
	}

	// Only penalise if both teams were selected (match actually started)
	if room.Match.HomeTeamID == 0 || room.Match.AwayTeamID == 0 {
		room.Match = nil
		return
	}

	// Increment disconnect counter
	if s.User.Profile != nil {
		s.User.Profile.Disconnects++
		ctx := context.Background()
		_ = db.UpdateProfileStats(ctx, sc, s.User.Profile)
	}

	cfg := hub.Config()
	lobby, ok := hub.GetLobby(s.User.LobbyIndex)
	if ok && lobby.TypeCode != 0x20 { // not a no-stats lobby
		if cfg.Disconnects.CountAsLoss.Enabled {
			// Set score so disconnecting player loses
			playerScore := cfg.Disconnects.CountAsLoss.Score.Player
			opponentScore := cfg.Disconnects.CountAsLoss.Score.Opponent
			if s.User.Profile != nil && room.Match.HomeProfileID == s.User.Profile.ID {
				room.Match.ScoreHome = playerScore
				room.Match.ScoreAway = opponentScore
			} else {
				room.Match.ScoreHome = opponentScore
				room.Match.ScoreAway = playerScore
			}
		} else {
			// Abandoned match: don't record
			room.Match = nil
		}
	}

	// Notify room owner that challenger left
	lobby2, ok2 := hub.GetLobby(s.User.LobbyIndex)
	if !ok2 {
		return
	}
	room.Exit(s.User)
	if !room.IsEmpty() && room.Owner != nil {
		exitData := append(
			model.PadWithZeros(s.User.Profile.Name, 16),
			model.PadWithZeros(room.Name, 32)...,
		)
		sendToUser(hub, room.Owner, 0x4331, exitData)
	}

	// Broadcast room + player updates
	roomData := encodeRoomUpdate(room, true)
	playerData := formatPlayerInfo(s.User, room.ID)
	for _, u := range lobby2.Players() {
		sendToUser(hub, u, 0x4306, roomData)
		sendToUser(hub, u, 0x4222, playerData)
	}

	if room.IsEmpty() {
		roomIDData := pack32i(int32(room.ID))
		for _, u := range lobby2.Players() {
			sendToUser(hub, u, 0x4305, roomIDData)
		}
		lobby2.DeleteRoom(room)
	}
}

// ---- room update encoder ----------------------------------------------------

// encodeRoomUpdate builds the 0x4306 room-info wire payload.
// Both cases use 11 bytes per player slot (mirrors Python exactly):
//
//	withTeams=false: [4 profileID][7 zeros]       (room creation / challenge)
//	withTeams=true:  [4 profileID][2 teamID][5 zeros]  (exit / cancel challenge)
//
// Layout:
//
//	[4]  roomID int32 big-endian
//	[1]  1 (active flag)
//	[1]  usePassword
//	[32] roomName null-padded
//	[1]  matchTime/5
//	[48] player slots: 11 bytes × up to 4 players, zero-padded to 48
func encodeRoomUpdate(room *model.Room, withTeams bool) []byte {
	n := len(room.Players)
	data := make([]byte, 0, 4+1+1+32+1+48)
	data = append(data, pack32i(int32(room.ID))...)
	data = append(data, 1) // active
	if room.UsePassword {
		data = append(data, 1)
	} else {
		data = append(data, 0)
	}
	data = append(data, model.PadWithZeros(room.Name, 32)...)
	data = append(data, byte(room.MatchTime/5))

	playerSlots := make([]byte, 48)
	for i, u := range room.Players {
		if u.Profile == nil || i*11+11 > 48 {
			break
		}
		off := i * 11
		binary.BigEndian.PutUint32(playerSlots[off:], uint32(int32(u.Profile.ID)))
		if withTeams && u.State != nil {
			binary.BigEndian.PutUint16(playerSlots[off+4:], uint16(u.State.TeamID))
		}
		// bytes [6:11] remain zeros
	}
	_ = n
	data = append(data, playerSlots...)
	return data
}
