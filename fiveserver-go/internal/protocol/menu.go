package protocol

import (
	"context"
	"encoding/binary"
	"log"
	"strings"
	"time"

	"github.com/fiveserver/fiveserver-go/internal/db"
	"github.com/fiveserver/fiveserver-go/internal/model"
)

// chatHistoryDelay matches the Python CHAT_HISTORY_DELAY constant: 3 seconds.
const chatHistoryDelay = 3 * time.Second

// NewMenuDispatcher creates a Dispatcher that includes all LoginService handlers
// plus NetworkMenuService-specific ones. Mirrors Python NetworkMenuService.register()
// which calls LoginService.register() first, then adds its own handlers.
func NewMenuDispatcher(hub *Hub, sc *db.StorageController, version string) *Dispatcher {
	d := NewLoginDispatcher(hub, sc, version)
	registerMenuHandlers(d, hub, sc)
	return d
}

func registerMenuHandlers(d *Dispatcher, hub *Hub, sc *db.StorageController) {
	d.Register(0x4100, handleDo4100(hub, sc))
	d.Register(0x4102, handleGetProfile4102(hub, sc))
	d.Register(0x4110, handleSetFavTeam4110(hub, sc))
	d.Register(0x4114, handleSetFavPlayer4114(hub, sc))
	d.Register(0x4200, handleGetLobbies4200(hub))
	d.Register(0x4202, handleSelectLobby4202(hub, sc))
	d.Register(0x4210, handleGetUserList4210(hub))
	d.Register(0x4300, handleGetRoomList4300(hub))
	d.Register(0x3080, handleDo3080())
	d.Register(0x4580, handleGetFriends4580())
	d.Register(0x4600, handleSearchPlayers4600())
	d.Register(0x4780, handleGetInboxMessages4780())
	d.Register(0x4a00, handleQuickMatchSearch4a00(hub))
	d.Register(0x0003, handleMenuDisconnect(hub)) // overrides login's disconnect
}

// ---- 0x4100 selectProfileForMenu --------------------------------------------
// Python: do_4100 — select profile by index, send profile ACK + full stats.

func handleDo4100(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || len(pkt.Data) < 1 {
			return nil
		}
		profileIndex := int(pkt.Data[0])
		if profileIndex >= len(s.User.Profiles) {
			return nil
		}
		s.User.Profile = s.User.Profiles[profileIndex]
		s.User.Conn = s.Conn   // wire current connection so sendToUser fallback works
		hub.AddSession(s)      // register session now that profile is known

		// Run full disconnect cleanup when the TCP connection closes (including
		// forced closes from the admin kick endpoint — those never send 0x0003).
		s.OnClose = func() {
			log.Printf("[menu] connection closed for {%s} — running cleanup", s.User.Profile.Name)
			exitLobbyAndNotify(hub, s)
			hub.UserOffline(s)
		}

		// [4 zeros][4 bytes profile id][33 fixed capability bytes]
		// Python: b'\0'*4 + pack('!i', id) + b'\xff'*7+b'\x80'+b'\xff'*15+b'\xc0'+b'\2'*7+b'\1\0'
		data := make([]byte, 4)
		data = append(data, pack32i(int32(s.User.Profile.ID))...)
		flags := []byte{
			0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x80, // \xff*7 + \x80
			0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,        // 7 of \xff*15
			0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xc0, // remaining 8 + \xc0
			0x02, 0x02, 0x02, 0x02, 0x02, 0x02, 0x02, 0x01, 0x00,
		}
		data = append(data, flags...)
		if err := s.Conn.SendData(0x4101, data); err != nil {
			return err
		}

		ctx := context.Background()
		p, err := db.GetProfileByID(ctx, sc, s.User.Profile.ID)
		if err != nil {
			return s.Conn.SendZeros(0x4103, 0)
		}
		stats, err := db.GetStatsByProfileID(ctx, sc, p.ID)
		if err != nil {
			stats = &model.Stats{}
		}
		info := append([]byte{0, 0, 0, 0}, formatProfileInfo(p, stats, hub.Config().ShowStats)...)
		return s.Conn.SendData(0x4103, info)
	}
}

// ---- 0x4102 getProfile ------------------------------------------------------

func handleGetProfile4102(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if len(pkt.Data) < 4 {
			return s.Conn.SendZeros(0x4103, 0)
		}
		profileID := int(int32(binary.BigEndian.Uint32(pkt.Data[0:4])))
		ctx := context.Background()
		p, err := db.GetProfileByID(ctx, sc, profileID)
		if err != nil {
			return s.Conn.SendZeros(0x4103, 0)
		}
		stats, err := db.GetStatsByProfileID(ctx, sc, p.ID)
		if err != nil {
			stats = &model.Stats{}
		}
		data := append([]byte{0, 0, 0, 0}, formatProfileInfo(p, stats, hub.Config().ShowStats)...)
		return s.Conn.SendData(0x4103, data)
	}
}

// ---- 0x4110 setFavouriteTeam ------------------------------------------------

func handleSetFavTeam4110(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.Profile == nil || len(pkt.Data) < 2 {
			return nil
		}
		s.User.Profile.FavTeam = int64(binary.BigEndian.Uint16(pkt.Data[0:2]))
		ctx := context.Background()
		_ = db.UpdateProfileStats(ctx, sc, s.User.Profile)
		return s.Conn.SendZeros(0x4112, 4)
	}
}

// ---- 0x4114 setFavouritePlayer ----------------------------------------------

func handleSetFavPlayer4114(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.Profile == nil || len(pkt.Data) < 4 {
			return nil
		}
		s.User.Profile.FavPlayer = int64(int32(binary.BigEndian.Uint32(pkt.Data[0:4])))
		ctx := context.Background()
		_ = db.UpdateProfileStats(ctx, sc, s.User.Profile)
		return s.Conn.SendZeros(0x4116, 4)
	}
}

// ---- 0x4200 getLobbies ------------------------------------------------------

func handleGetLobbies4200(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		lobbies := hub.Lobbies()
		// [2 bytes count big-endian][35 bytes × count]
		data := make([]byte, 2)
		binary.BigEndian.PutUint16(data, uint16(len(lobbies)))
		for _, l := range lobbies {
			data = append(data, l.Bytes()...)
		}
		return s.Conn.SendData(0x4201, data)
	}
}

// ---- 0x4202 selectLobby -----------------------------------------------------
// Python: selectLobby_4202 — store network state, enter lobby, broadcast join,
// schedule chat history replay after chatHistoryDelay.

func handleSelectLobby4202(hub *Hub, sc *db.StorageController) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil || s.User.Profile == nil || len(pkt.Data) < 37 {
			return nil
		}

		lobbyID := int(pkt.Data[0])
		state := &model.NetworkState{
			IP1:      append([]byte(nil), pkt.Data[1:17]...),
			IP2:      append([]byte(nil), pkt.Data[19:35]...),
			UDPPort1: binary.BigEndian.Uint16(pkt.Data[17:19]),
			UDPPort2: binary.BigEndian.Uint16(pkt.Data[35:37]),
		}
		if len(pkt.Data) >= 39 {
			state.SomeField = binary.BigEndian.Uint16(pkt.Data[37:39])
		}
		s.User.State = state
		s.User.LobbyIndex = lobbyID

		if err := s.Conn.SendZeros(0x4203, 4); err != nil {
			return err
		}

		lobby, ok := hub.GetLobby(lobbyID)
		if !ok {
			log.Printf("[menu] %s: unknown lobby id %d", s.Conn.RemoteAddr, lobbyID)
			return nil
		}
		log.Printf("[menu] User {%s} entering lobby %d (%s)", s.User.Profile.Name, lobbyID+1, lobby.Name)
		lobby.Enter(s.User)

		// Notify all lobby members of the new user joining
		playerData := formatPlayerInfo(s.User, 0)
		for _, u := range lobby.Players() {
			sendToUser(hub, u, 0x4220, playerData)
		}

		// Schedule chat history replay after chatHistoryDelay
		time.AfterFunc(chatHistoryDelay, func() {
			sendChatHistory(lobby, s)
		})
		return nil
	}
}

// ---- 0x4210 getUserList -----------------------------------------------------

func handleGetUserList4210(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil {
			return nil
		}
		if err := s.Conn.SendZeros(0x4211, 4); err != nil {
			return err
		}
		lobby, ok := hub.GetLobby(s.User.LobbyIndex)
		if !ok {
			return s.Conn.SendZeros(0x4213, 4)
		}
		for _, u := range lobby.Players() {
			roomID := 0
			if u.State != nil && u.State.InRoom && u.State.Room != nil {
				roomID = u.State.Room.ID
			}
			if err := s.Conn.SendData(0x4212, formatPlayerInfo(u, roomID)); err != nil {
				return err
			}
		}
		return s.Conn.SendZeros(0x4213, 4)
	}
}

// ---- 0x4300 getRoomList -----------------------------------------------------

func handleGetRoomList4300(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User == nil {
			return s.Conn.SendZeros(0x4301, 4)
		}
		if err := s.Conn.SendZeros(0x4301, 4); err != nil {
			return err
		}
		lobby, ok := hub.GetLobby(s.User.LobbyIndex)
		if !ok {
			return s.Conn.SendZeros(0x4303, 4)
		}
		for _, room := range lobby.Rooms() {
			n := len(room.Players)
			data := make([]byte, 0, 4+1+1+32+1+48)
			data = append(data, pack32i(int32(room.ID))...)
			data = append(data, 1) // active flag
			if room.UsePassword {
				data = append(data, 1)
			} else {
				data = append(data, 0)
			}
			data = append(data, model.PadWithZeros(room.Name, 32)...)
			data = append(data, byte(room.MatchTime/5))
			// Player IDs (4 bytes each), padded to 48 bytes total
			playerBytes := make([]byte, 48)
			for i, u := range room.Players {
				if u.Profile != nil && i*4+4 <= 48 {
					binary.BigEndian.PutUint32(playerBytes[i*4:], uint32(int32(u.Profile.ID)))
				}
			}
			_ = n
			data = append(data, playerBytes...)
			if err := s.Conn.SendData(0x4302, data); err != nil {
				return err
			}
		}
		return s.Conn.SendZeros(0x4303, 4)
	}
}

// ---- 0x3080 stub -------------------------------------------------------------

func handleDo3080() HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if err := s.Conn.SendZeros(0x3082, 4); err != nil {
			return err
		}
		return s.Conn.SendZeros(0x3086, 0)
	}
}

// ---- 0x4580 getFriends -------------------------------------------------------

func handleGetFriends4580() HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if err := s.Conn.SendZeros(0x4581, 4); err != nil {
			return err
		}
		return s.Conn.SendZeros(0x4583, 4)
	}
}

// ---- 0x4600 searchPlayers ---------------------------------------------------

func handleSearchPlayers4600() HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if err := s.Conn.SendZeros(0x4601, 4); err != nil {
			return err
		}
		return s.Conn.SendZeros(0x4603, 4)
	}
}

// ---- 0x4780 getInboxMessages ------------------------------------------------

func handleGetInboxMessages4780() HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if err := s.Conn.SendZeros(0x4781, 4); err != nil {
			return err
		}
		return s.Conn.SendZeros(0x4783, 4)
	}
}

// ---- 0x4a00 quickMatchSearch ------------------------------------------------
// Python: sends "no results" then exits the lobby.

func handleQuickMatchSearch4a00(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if err := s.Conn.SendData(0x4a01, []byte{0, 0, 0, 1}); err != nil {
			return err
		}
		if s.User != nil && s.User.Profile != nil {
			log.Printf("[menu] User {%s} exiting lobby %d (quick match search)", s.User.Profile.Name, s.User.LobbyIndex+1)
		}
		exitLobbyAndNotify(hub, s)
		return nil
	}
}

// ---- 0x0003 disconnect (menu override) --------------------------------------
// Python: disconnect_0003 — exit lobby, mark user offline, notify remaining users.

func handleMenuDisconnect(hub *Hub) HandlerFunc {
	return func(s *Session, pkt Packet) error {
		if s.User != nil && s.User.Profile != nil {
			log.Printf("[menu] User {%s} exiting lobby %d (disconnect)", s.User.Profile.Name, s.User.LobbyIndex+1)
		}
		// Clear OnClose so the serveConn defer does not run cleanup a second time
		// after this graceful 0x0003 handler already did it.
		s.OnClose = nil
		exitLobbyAndNotify(hub, s)
		if s.User != nil {
			hub.UserOffline(s)
		}
		return nil
	}
}

// ---- FilterChat -------------------------------------------------------------

// FilterChat returns the original message, or "[warning]" if any banned word
// (case-insensitive substring) is found. Mirrors Python chat_4400 filter logic.
func FilterChat(msg string, bannedWords []string, warning string) string {
	lower := strings.ToLower(msg)
	for _, word := range bannedWords {
		if strings.Contains(lower, strings.ToLower(word)) {
			return "[" + warning + "]"
		}
	}
	return msg
}

// ---- broadcastSystemChat ----------------------------------------------------

// BroadcastSystemChat sends a server-generated chat message to every player in
// the lobby and adds it to the chat history.
// Matches Python NetworkMenuService.broadcastSystemChat exactly.
func BroadcastSystemChat(hub *Hub, lobby *model.Lobby, text string) {
	msg := model.NewChatMessage(model.SystemProfile, text)
	data := buildChatPacket([]byte{0}, []byte{0, 0, 0, 0}, model.SystemProfile, text)
	for _, u := range lobby.Players() {
		sendToUser(hub, u, 0x4402, data)
	}
	lobby.AddChatMessage(msg)
}

// StartDayChangeTimer fires broadcastSystemChat + PurgeOldChat for every lobby
// at startup and then once per day at midnight — matching Python systemDayChange.
func StartDayChangeTimer(hub *Hub) {
	var tick func()
	tick = func() {
		now := time.Now()
		message := "Date: " + now.Format("Mon Jan  2 15:04:05 2006") + " " + now.Format("MST")
		for _, lobby := range hub.Lobbies() {
			if len(lobby.Players()) == 0 {
				// no connection to broadcast through — just add to history
				lobby.AddChatMessage(model.NewChatMessage(model.SystemProfile, message))
			} else {
				BroadcastSystemChat(hub, lobby, message)
			}
			lobby.PurgeOldChat()
		}
		// schedule next run at next midnight + 1 second (mirrors Python td.seconds+1)
		midnight := time.Date(now.Year(), now.Month(), now.Day()+1, 0, 0, 1, 0, now.Location())
		time.AfterFunc(time.Until(midnight), tick)
	}
	// run immediately on startup (mirrors Python reactor.callLater(0, self.systemDayChange))
	go tick()
}

// ---- chat helpers -----------------------------------------------------------

// sendChatHistory replays lobby chat history to a newly joined user.
// Private messages are only replayed to their sender or recipient.
// Called via time.AfterFunc with chatHistoryDelay.
func sendChatHistory(lobby *model.Lobby, s *Session) {
	if lobby == nil || s == nil || s.User == nil || s.User.Profile == nil {
		return
	}
	myID := s.User.Profile.ID
	for _, msg := range lobby.ChatHistory() {
		if msg.To != nil {
			if myID != msg.From.ID && myID != msg.To.ID {
				continue
			}
		}
		special := []byte{0, 0, 0, 0}
		if msg.To != nil {
			if raw, ok := msg.Special.([]byte); ok && len(raw) >= 4 {
				special = raw[:4]
			}
		}
		data := buildChatPacket([]byte{0}, special, msg.From, msg.Text)
		_ = s.Conn.SendData(0x4402, data)
	}
}

// buildChatPacket constructs the 0x4402 wire payload.
//
//	[1]   chatType byte (first byte of chatType field)
//	[4]   special (pkt.Data[2:6] for private, zeros for broadcast)
//	[4]   fromProfile.ID int32 big-endian
//	[16]  fromProfile.Name null-padded
//	[N]   message truncated to 126 bytes + \0\0
func buildChatPacket(chatTypeByte, special []byte, from *model.Profile, text string) []byte {
	b := make([]byte, 0, 1+4+4+16+128)
	if len(chatTypeByte) > 0 {
		b = append(b, chatTypeByte[0])
	} else {
		b = append(b, 0)
	}
	if len(special) >= 4 {
		b = append(b, special[:4]...)
	} else {
		b = append(b, 0, 0, 0, 0)
	}
	b = append(b, pack32i(int32(from.ID))...)
	b = append(b, model.PadWithZeros(from.Name, 16)...)
	msg := []byte(text)
	if len(msg) > 126 {
		msg = msg[:126]
	}
	b = append(b, msg...)
	b = append(b, 0, 0)
	return b
}

// ---- player/profile info formatters -----------------------------------------

// formatPlayerInfo serialises a user's lobby presence into the 31-byte wire format.
// Mirrors Python NetworkMenuService.formatPlayerInfo.
//
//	[4]  profile.ID int32 big-endian
//	[16] profile.Name null-padded
//	[1]  inRoom (0 or 1)
//	[4]  roomId int32 big-endian
//	[4]  noLobbyChat int32 big-endian
//	[1]  0
//	[1]  0
func formatPlayerInfo(u *model.ConnectedUser, roomID int) []byte {
	b := make([]byte, 0, 31)
	var profID int32
	var profName string
	if u.Profile != nil {
		profID = int32(u.Profile.ID)
		profName = u.Profile.Name
	}
	b = append(b, pack32i(profID)...)
	b = append(b, model.PadWithZeros(profName, 16)...)

	inRoom := byte(0)
	noLobbyChat := 0
	if u.State != nil {
		if u.State.InRoom {
			inRoom = 1
		}
		noLobbyChat = u.State.NoLobbyChat
	}
	b = append(b, inRoom)
	b = append(b, pack32i(int32(roomID))...)
	b = append(b, pack32i(int32(noLobbyChat))...)
	b = append(b, 0, 0)
	return b
}

// formatProfileInfo serialises a profile + stats for the 0x4103 wire format.
// Mirrors Python NetworkMenuService.formatProfileInfo. Total: 57 bytes.
//
//	[4]  id int32
//	[16] name null-padded
//	[1]  division
//	[4]  points int32
//	[2]  games uint16 (wins+losses+draws)
//	[2]  wins uint16
//	[2]  losses uint16
//	[2]  draws uint16
//	[2]  streak_current uint16
//	[2]  streak_best uint16
//	[2]  disconnects uint16
//	[2]  PAD
//	[2]  goals_scored uint16
//	[2]  PAD
//	[2]  goals_allowed uint16
//	[2]  fav_team uint16
//	[4]  fav_player int32
//	[4]  rank int32
func formatProfileInfo(p *model.Profile, s *model.Stats, showStats bool) []byte {
	if !showStats {
		s = &model.Stats{}
		p = &model.Profile{
			ID: p.ID, Name: p.Name,
			FavPlayer: p.FavPlayer, FavTeam: p.FavTeam, Rank: p.Rank,
		}
	}
	b := make([]byte, 0, 57)
	b = append(b, pack32i(int32(p.ID))...)
	b = append(b, model.PadWithZeros(p.Name, 16)...)
	b = append(b, getDivision(p.Points))
	b = append(b, pack32i(int32(p.Points))...)
	games := uint16(s.Wins + s.Losses + s.Draws)
	b = append(b, byte(games>>8), byte(games))
	b = append(b, byte(s.Wins>>8), byte(s.Wins))
	b = append(b, byte(s.Losses>>8), byte(s.Losses))
	b = append(b, byte(s.Draws>>8), byte(s.Draws))
	b = append(b, byte(s.StreakCurrent>>8), byte(s.StreakCurrent))
	b = append(b, byte(s.StreakBest>>8), byte(s.StreakBest))
	b = append(b, byte(p.Disconnects>>8), byte(p.Disconnects))
	b = append(b, 0, 0)                                         // PAD1
	b = append(b, byte(s.GoalsScored>>8), byte(s.GoalsScored))
	b = append(b, 0, 0)                                         // PAD2
	b = append(b, byte(s.GoalsAllowed>>8), byte(s.GoalsAllowed))
	b = append(b, byte(p.FavTeam>>8), byte(p.FavTeam))
	b = append(b, pack32i(int32(p.FavPlayer))...)
	b = append(b, pack32i(int32(p.Rank))...)
	return b
}

// ---- shared send helper -----------------------------------------------------

// sendToUser delivers a packet to a ConnectedUser by looking up their Session in the Hub.
// Falls back to u.Conn if the session is not found in the hub (e.g. not yet profiled).
func sendToUser(hub *Hub, u *model.ConnectedUser, id uint16, data []byte) {
	name := ""
	if u.Profile != nil {
		name = u.Profile.Name
	}
	if u.Profile != nil {
		if sess, ok := hub.GetSession(u.Profile.Name); ok {
			log.Printf("[sendToUser] {%s} pkt=0x%04x — via hub session (addr=%s)", name, id, sess.Conn.RemoteAddr)
			if err := sess.Conn.SendData(id, data); err != nil {
				log.Printf("[sendToUser] {%s} pkt=0x%04x — hub send error: %v", name, id, err)
			}
			return
		}
	}
	// fallback: direct conn (set by server layer)
	if u.Conn != nil {
		if cs, ok := u.Conn.(*ConnSender); ok {
			log.Printf("[sendToUser] {%s} pkt=0x%04x — via direct conn (addr=%s)", name, id, cs.RemoteAddr)
			if err := cs.SendData(id, data); err != nil {
				log.Printf("[sendToUser] {%s} pkt=0x%04x — direct send error: %v", name, id, err)
			}
			return
		}
	}
	log.Printf("[sendToUser] {%s} pkt=0x%04x — no route found (hub miss, no conn)", name, id)
}

// ---- exitLobbyAndNotify -----------------------------------------------------

// exitLobbyAndNotify removes the user from their current lobby and broadcasts
// a 0x4221 "user left" packet to all remaining members.
// Safe to call if the user is not in a lobby (no-op).
func exitLobbyAndNotify(hub *Hub, s *Session) {
	if s.User == nil || s.User.Profile == nil {
		return
	}
	lobby, ok := hub.GetLobby(s.User.LobbyIndex)
	if !ok {
		return
	}
	lobby.Exit(s.User)
	s.User.LobbyIndex = -1

	leaveData := pack32i(int32(s.User.Profile.ID))
	for _, u := range lobby.Players() {
		sendToUser(hub, u, 0x4221, leaveData)
	}
}
