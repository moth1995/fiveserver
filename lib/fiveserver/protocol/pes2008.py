"""
Protocol implementations for PES2008

Duplicates the PES6 implementation (fiveserver.protocol.pes6) as-is;
PES2008 uses the same network protocol as PES6/WE2007.
"""

import struct
from fiveserver.model import util
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

class MainService(NetworkMenuService):
    """
    PES2008 implementation
    The main game server, which keeps track of matches, goals
    and other important statistics.
    """
