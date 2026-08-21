"""
Protocol implementations for PES2008

Duplicates the PES6 implementation (fiveserver.protocol.pes6) as-is;
PES2008 uses the same network protocol as PES6/WE2007.
"""

import struct
import time

from twisted.internet import defer
from fiveserver.model import lobby, user, util
from fiveserver.protocol import pes6
from fiveserver import log


class NewsProtocol(pes6.NewsProtocol):
    """
    News-service for PES2008
    """

    GREETING = {
        "title": "SYSTEM: Fiveserver v%s",
        "text": (
            "Welcome to Fiveserver -\r\n"
            "independent community server\r\n"
            "supporting PES2008 games.\r\n"
            "Have a good time, play some nice\r\n"
            "football and try to score goals.\r\n"
            "\r\n"
            "Credits:\r\n"
            "Protocol analysis: reddwarf, juce, marqisspes6\r\n"
            "Server programming: juce, reddwarf, marqisspes6"
        ),
    }

    SERVER_NAME = "Fiveserver"

    NEW_FEATURES = {
        "0.5.1": ("NEW features in 0.5.1:", "* introducing PES2008 support!\r\n")
    }

    def register(self):
        super().register()
        self.addHandler(0x2300, self.getNetworkMenuService_2300)

    def getNetworkMenuService_2300(self, pkt):
        # PC v1.20 parses this as result:u32be + service-kind:u16be,
        # then resolves that kind against the records received in 0x2003.
        # The following login state authenticates and sends 0x4100 through
        # connection slot 3, so select the main-service record (kind 3).
        self.sendData(0x2301, struct.pack("!IH", 0, 8))

    def getServerList_2005(self, pkt):
        myport = self.transport.getHost().port
        gameName = None
        for name, port in self.factory.serverConfig.GamePorts.items():
            if port == myport:
                gameName = name
                break

        serverIP = self.factory.configuration.serverIP_wan
        servers = [
            (
                -1,
                2,
                "LOGIN",
                serverIP,
                self.factory.serverConfig.NetworkServer["loginService"][gameName],
                max(0, self.factory.getNumUsersOnline() - 1),
                2,
            ),
            (
                -1,
                3,
                self.SERVER_NAME,
                serverIP,
                self.factory.serverConfig.NetworkServer["mainService"],
                max(0, self.factory.getNumUsersOnline() - 1),
                3,
            ),
            (
                -1,
                3,
                "NETWORK_MENU",
                serverIP,
                self.factory.serverConfig.NetworkServer["networkMenuService"],
                max(0, self.factory.getNumUsersOnline() - 1),
                8,
            ),
        ]
        data = b"".join(
            [
                b"%s%s%s%s%s%s%s"
                % (
                    struct.pack("!i", server_id),
                    struct.pack("!i", socket_slot),
                    util.padWithZeros(name, 32),
                    util.padWithZeros(ip, 15),
                    struct.pack("!H", port),
                    struct.pack("!H", users),
                    struct.pack("!H", service_kind),
                )
                for (
                    server_id,
                    socket_slot,
                    name,
                    ip,
                    port,
                    users,
                    service_kind,
                ) in servers
            ]
        )
        self.sendZeros(0x2002, 4)
        self.sendData(0x2003, data)
        self.sendZeros(0x2004, 4)


class LoginService(pes6.LoginService):
    """
    Login-service for PES2008
    """

    def disconnect_0003(self, pkt):
        # PES2008 waits for the result packet before completing the
        # login-service handoff.  Close only after the ACK is queued.
        super().disconnect_0003(pkt)
        if pkt.data and pkt.data[0] == 0x01:
            self.sendZeros(0x0004, 4)
            self.transport.loseConnection()

    def defaultHandler(self, pkt):
        log.msg(
            "WARNING: no handler registered for packet id=0x%04x" % (pkt.header.id,)
        )

        self.sendZeros(pkt.header.id + 1, 4)


class LoginServicePES2008(LoginService):
    """
    Specific implementation of login service for PES2008
    """

    def __init__(self):
        LoginService.__init__(self)
        self.gameName = "pes2008"


class NetworkMenuService(pes6.MainService):
    """
    PES2008 implementation.
    The service that communicates with the player, when
    he/she is in the "NETWORK MENU" mode.
    """

    def register(self):
        super().register()
        self.addHandler(0x4C00, self.do_4c00)
        self.addHandler(0x4C10, self.do_4c10)
        self.addHandler(0x4C20, self.do_4c20)

    def disconnect_0003(self, pkt):
        # PES2008 waits for the result packet before completing the
        # login-service handoff.  Close only after the ACK is queued.
        super().disconnect_0003(pkt)
        if pkt.data and pkt.data[0] == 0x01:
            self.sendZeros(0x0004, 4)
            self.transport.loseConnection()

    def do_4100(self, pkt):
        # PES2008 PC does not follow this with the PES6 4200/4202 lobby
        # selection.  Its 4100 request already carries both network
        # endpoints, so place the player in the server's implicit lobby here.
        result = super().do_4100(pkt)
        if len(pkt.data) >= 42:
            state = user.UserState()
            state.lobbyId = 0
            # 4100 starts with profile-index:u8 + unknown:u16, followed by
            # ip1[16], port1:u16be, ip2[16], port2:u16be, and two trailing
            # fields.  The endpoint block therefore begins at byte 3.
            state.ip1 = pkt.data[3:19]
            state.udpPort1 = struct.unpack("!H", pkt.data[19:21])[0]
            state.ip2 = pkt.data[21:37]
            state.udpPort2 = struct.unpack("!H", pkt.data[37:39])[0]
            state.someField = struct.unpack("!H", pkt.data[39:41])[0]
            state.inRoom = 0
            state.noLobbyChat = 0
            state.room = None
            state.teamId = 0
            state.spectator = 0
            self._user.state = state
            self.factory.getLobbies()[0].enter(self._user, self)
        return result

    def formatProfileInfo(self, profile, stats):
        """Serialize the PES2008 PC v1.20 0x4103 profile structure."""
        if not self.factory.serverConfig.ShowStats:
            profile = self.makePristineProfile(profile)

        recent_teams = list(stats.teams[:5])
        return b"%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s" % (
            struct.pack("!i", profile.id),
            util.padWithZeros(profile.name, 48),
            struct.pack("!B", self.factory.ratingMath.getDivision(profile.points)),
            struct.pack("!i", profile.points),
            struct.pack("!H", profile.rating),
            struct.pack("!H", stats.wins + stats.losses + stats.draws),
            struct.pack("!H", stats.wins),
            struct.pack("!H", stats.losses),
            struct.pack("!H", stats.draws),
            struct.pack("!H", stats.streak_current),
            struct.pack("!H", stats.streak_best),
            struct.pack("!H", profile.disconnects),
            # The client consumes two additional u16 values here. They are
            # not displayed by the profile UI and their semantics are not yet
            # known, so keep them deterministic instead of shifting the
            # following goal counters.
            b"\0" * 4,
            struct.pack("!i", stats.goals_scored),
            struct.pack("!i", stats.goals_allowed),
            util.padWithZeros((profile.comment or "Fiveserver rules!"), 256),
            struct.pack("!i", profile.rank),
            # Two one-byte fields precede the five recent-team ids. PES6 has
            # a larger medal block here, which PES2008 does not parse.
            b"\0\0",
            b"".join(struct.pack("!H", team) for team in recent_teams),
            b"\xff\xff" * (5 - len(recent_teams)),
        )

    def formatRoomInfo(self, room):
        # PC v1.20's 4306 parser consumes a 128-byte room name and eight
        # 10-byte player slots.  The remaining fields retain the PES6 order.
        n = len(room.players)
        if room.match:
            match_state = room.match.state
            match_clock = room.match.clock
        else:
            match_state, match_clock = 0, 0
        teams_and_goals = self.formatTeamsAndGoals(room)
        # PES6 has five score bytes after each team id. PES2008 keeps those
        # fields and consumes six additional bytes for each team.
        teams_and_goals = (
            teams_and_goals[:7] + b"\0" * 6 + teams_and_goals[7:] + b"\0" * 6
        )
        room_settings = self._getRoomCreateSettings(room)
        return b"%s%s%s%s%s%s%s%s%s" % (
            struct.pack("!i", room.id),
            struct.pack("!B", room.phase),
            struct.pack("!B", match_state),
            util.padWithZeros(room.name, 128),
            struct.pack("!B", match_clock),
            b"".join(
                [
                    b"%s%s%s%s%s%s%s"
                    % (
                        struct.pack("!i", usr.profile.id),
                        struct.pack("!B", room.isOwner(usr)),
                        struct.pack("!B", room.isMatchStarter(usr)),
                        struct.pack("!B", self.formatHomeOrAway(room, usr)),
                        struct.pack("!B", usr.state.spectator),
                        struct.pack("!B", room.getPlayerPosition(usr)),
                        struct.pack("!B", room.getPlayerParticipate(usr)),
                    )
                    for usr in room.players[:8]
                ]
            ),
            b"\0\0\0\0\0\0\xff\0\0\xff" * max(0, 8 - n),
            teams_and_goals,
            struct.pack("!BBBBBB", *room_settings),
        )

    @staticmethod
    def _getRoomCreateSettings(room):
        """Return the six-byte settings tail consumed by the 0x4306 parser."""
        return (
            max(0, min(0xFF, room.matchTime)),
            max(0, min(0xFF, room.teamScope)),
            max(0, min(0xFF, room.gameMode)),
            max(0, min(0xFF, room.maxPlayers)),
            max(0, min(0xFF, room.inviteLimit)),
            max(0, min(0xFF, room.creationFlag)),
        )

    def formatRoomListInfo(self, room):
        """Serialize one 238-byte record consumed by the 0x4302 parser.

        The PC v1.20 parser proves the record boundaries and endpoint fields.
        The two u32 values and trailing status fields are still unresolved, so
        keep them deterministic while carrying the known room-create settings
        in their wire order.
        """
        owner = room.owner
        if owner is None and room.players:
            owner = room.players[0]

        if owner is None:
            owner_name = b""
            ip1 = b""
            udp_port1 = 0
            ip2 = b""
            udp_port2 = 0
            owner_profile_id = 0
        else:
            owner_name = owner.profile.name
            ip1 = owner.state.ip1
            udp_port1 = owner.state.udpPort1
            ip2 = owner.state.ip2
            udp_port2 = owner.state.udpPort2
            owner_profile_id = owner.profile.id

        (
            match_time,
            team_scope,
            game_mode,
            max_players,
            invite_limit,
            unknown_flag,
        ) = self._getRoomCreateSettings(room)

        data = b"%s%s%s%s%s%s%s%s%s%s%s%s%s" % (
            struct.pack("!I", room.id),
            util.padWithZeros(room.name, 128),
            struct.pack("!II", 0, 0),
            struct.pack("!BBBB", match_time, team_scope, game_mode, max_players),
            util.padWithZeros(owner_name, 48),
            struct.pack("!B", invite_limit),
            util.padWithZeros(ip1, 16),
            struct.pack("!H", udp_port1),
            util.padWithZeros(ip2, 16),
            struct.pack("!H", udp_port2),
            struct.pack("!I", owner_profile_id),
            struct.pack(
                "!BBB",
                unknown_flag,
                min(len(room.players), 0xFF),
                max(0, min(room.phase, 0xFF)),
            ),
            b"\0\0",
        )
        if len(data) != 238:
            raise ValueError("PES2008 0x4302 record must be 238 bytes")
        return data

    def _sendRoomConnectionInfo(self, room):
        # PES2008 completes a successful 4310 transaction only when the
        # accompanying 4346/4347/4348 room-connection list is terminated.
        # Unlike the PES6 implementation, both boundary packets contain a
        # u32be result field.
        self.sendZeros(0x4346, 4)
        if room is not None:
            for usr in room.players:
                data = b"%s%s%s%s%s%s%s%s" % (
                    b"\0" * 32,
                    util.padWithZeros(usr.state.ip1, 16),
                    struct.pack("!H", usr.state.udpPort1),
                    util.padWithZeros(usr.state.ip2, 16),
                    struct.pack("!H", usr.state.udpPort2),
                    struct.pack("!i", usr.profile.id),
                    struct.pack("!H", 0),
                    struct.pack("!B", room.getPlayerParticipate(usr)),
                )
                self.sendData(0x4347, data)
        self.sendZeros(0x4348, 4)

    def createRoom_4310(self, pkt):
        # roomName[128] + matchTime:u8 + teamScope:u8 + gameMode:u8 +
        # maxPlayers:u32be + inviteLimit:u8 + unknown:u8
        if len(pkt.data) < 137:
            log.msg(
                "WARNING: PES2008 0x4310 payload is %d bytes; expected 137"
                % len(pkt.data)
            )
            self.sendData(0x4311, b"\xff\xff\xff\xff")
            return

        room_name = util.stripZeros(pkt.data[:128])
        match_time, team_scope, game_mode = struct.unpack("!BBB", pkt.data[128:131])
        max_players = struct.unpack("!I", pkt.data[131:135])[0]
        invite_limit, unknown_flag = struct.unpack("!BB", pkt.data[135:137])
        this_lobby = self.factory.getLobbies()[self._user.state.lobbyId]

        try:
            this_lobby.getRoom(room_name)
            self.sendData(0x4311, b"\xff\xff\xff\x10")
            return
        except KeyError:
            pass

        room = lobby.Room(this_lobby)
        room.name = room_name
        room.matchTime = match_time
        room.teamScope = team_scope
        room.gameMode = game_mode
        room.maxPlayers = max_players
        room.inviteLimit = invite_limit
        room.creationFlag = unknown_flag
        room.usePassword = False
        room.password = None

        room.enter(self._user)
        this_lobby.addRoom(room)
        log.msg(
            "Room created: %s (time=%d, teams=%d, mode=%d, max=%d, "
            "invite-limit=%d, unknown=%d)"
            % (
                repr(room),
                match_time,
                team_scope,
                game_mode,
                max_players,
                invite_limit,
                unknown_flag,
            )
        )
        self.sendRoomUpdate(room)
        self.sendPlayerUpdate(room.id)
        self.sendZeros(0x4311, 4)
        self._sendRoomConnectionInfo(room)

    def getRoomList_4300(self, pkt):
        self.sendZeros(0x4301, 4)
        this_lobby = self.factory.getLobbies()[self._user.state.lobbyId]
        for room in this_lobby.rooms.values():
            self.sendData(0x4302, self.formatRoomListInfo(room))
        self.sendZeros(0x4303, 4)

    def getStunInfo_4345(self, pkt):
        room = None
        if len(pkt.data) >= 4:
            room_id = struct.unpack("!i", pkt.data[:4])[0]
            this_lobby = self.factory.getLobbies()[self._user.state.lobbyId]
            room = this_lobby.getRoomById(room_id)
        self._sendRoomConnectionInfo(room)
        if room is not None:
            self.do_4330(room)

    def do_4c00(self, pkt):
        # PC v1.20 treats this as a list transaction:
        # 4c01:u32be count/status, optional 4c02 records, then 4c03:end.
        self.sendZeros(0x4C01, 4)
        self.sendZeros(0x4C03, 0)

    def do_4c10(self, pkt):
        # The in-room variant has the same framing as 4c00:
        # 4c11:u32be status, optional 4c12 records, then 4c13:end.
        self.sendZeros(0x4C11, 4)
        self.sendZeros(0x4C13, 0)

    def do_4c20(self, pkt):
        # 4c20 carries the target profile name in a fixed 48-byte field.
        # Its request state waits on 4c23:u32be; 4c21 is an empty async
        # notification and is not the direct response to this request.
        self.sendZeros(0x4C23, 4)

    def defaultHandler(self, pkt):
        log.msg(
            "WARNING: no handler registered for packet id=0x%04x" % (pkt.header.id,)
        )

        self.sendZeros(pkt.header.id + 1, 4)


class MainService(NetworkMenuService):
    """
    PES2008 implementation
    The main game server, which keeps track of matches, goals
    and other important statistics.
    """
