"""
Protocol implementations for PES6
"""

from twisted.internet import defer

from datetime import datetime
import functools
import re
import struct

from fiveserver.model import user, lobby, util
from fiveserver import data9, log
from fiveserver.protocol import pes6


# 0x2202 web-server list: (ordinal, url, version). Client entry (reader
# pes2009.exe FUN_00d324b0, 12 x 0xa8 at session+0x3360): u8 ordinal,
# url[128], u16 version, u32, hash[32]. Only url (getter FUN_00d61c80) and
# version are ever read; version of 9/10 (EULAs) is compared with save data
# (FUN_00d2df20) and a change shows the agreement again. u32/hash: unused.
# The scheme is ignored: WinINet requests (FUN_008b6d60 & co.) use TLS for
# any port but 80, so http://host:9191 is also fetched over HTTPS.
_WEB_PAGES = [
    (0, "http://pes2009web.winning-eleven.net/pes09pc/web/ranking/pesgetrank.html", 0),
    (1, "http://pes2009web.winning-eleven.net/pes09pc/web/ranking/pesRankingWeek.html", 0),
    (2, "http://pes2009web.winning-eleven.net/pes09pc/web/pc_spec/pc_spec.php", 0),
    (3, "http://pes2009web.winning-eleven.net/pes09pc/web/dlcontents/download.php", 0),
    (4, "http://pes2009web.winning-eleven.net/pes09pc/web/dlcontents/download_desc.php", 0),
    (5, "https://id.konami.net/quick/selectCountry.jsp", 0),
    (6, "https://id.konami.net/login.do", 0),
    (7, "https://pes2009web.winning-eleven.net/pes09pc/web/konamiid/auth.php", 0),
    (8, "http://pes2009web.winning-eleven.net/pes09pc/web/fdvideo/fdvideo.php", 0),
    (9, "http://pes2009web.winning-eleven.net/pes09pc/web/agreement/pc/eula_pes_en.txt", 0),
    (10, "http://pes2009web.winning-eleven.net/pes09pc/web/agreement/pc/eula_konami_en.txt", 0),
    (11, "", 0),
]


@functools.lru_cache(maxsize=None)
def webPagePackets(base=None):
    """
    0x2202 payloads (6 entries each). base (nineserver.yaml WebBaseUrl)
    replaces scheme+host of every pes2009web URL. Built once per base.
    """
    entries = [
        struct.pack("!B", ordinal)
        + util.padWithZeros(
            re.sub(r"^https?://pes2009web\.winning-eleven\.net", base, url)
            if base
            else url,
            128,
        )
        + struct.pack("!HI", version, 0)
        + bytes(32)  # hash: unused by the client
        for ordinal, url, version in _WEB_PAGES
    ]
    return tuple(b"".join(entries[i : i + 6]) for i in range(0, len(entries), 6))


# Teams allowed online (0x4101, relayed per player in 0x4330/0x4347):
# u16[400], 0xffff padded. pes2009.exe FUN_00ce2530/FUN_00c90ea0 drop every
# team of the national (category 8), club (0x15) and classic (9) lists that
# isn't in it (FUN_008af000) from team select. Same set as goalserver/Konami:
# all national teams and clubs except the classics (902-908) and 1533-1552.
ONLINE_TEAMS = (
    tuple(range(1, 25)) + (26, 27, 28, 29, 30, 31) + tuple(range(33, 41))
    + (42,) + tuple(range(44, 58)) + tuple(range(100, 104)) + tuple(range(105, 123))
    + (124, 125, 130, 131, 132, 133, 134, 135, 138, 139, 172, 173, 174)
    + tuple(range(176, 182)) + (186, 188, 190, 191, 192, 193, 194, 197, 198, 199, 200,
    202, 205, 206, 212, 213, 216, 217, 218, 219, 221, 234, 236)
    + tuple(range(238, 249)) + (250, 251, 252, 253, 255, 258, 259, 260, 261, 263, 264,
    265, 266, 267, 269, 270, 273, 320, 321, 323, 327, 333, 339, 349, 351, 357, 362,
    363, 368, 371, 395, 396, 399, 400, 405, 409, 413, 414, 415, 418, 1031, 1032, 1051,
    1058, 1099, 1164, 1197, 1203, 1205, 1207, 1215, 1216, 1218, 1223, 1227, 1232,
    1252, 1528, 1550, 1582, 1583, 1589, 1706, 1733, 1746, 1832)
)
ONLINE_TEAMS_BLOCK = struct.pack("!400H", *ONLINE_TEAMS, *[0xFFFF] * (400 - len(ONLINE_TEAMS)))

# 0x4101 (parser FUN_00d35240, copied out by FUN_00d44ff0): result u32,
# profile id u32, ONLINE_TEAMS_BLOCK, then this tail. Values as goalserver.
LOGIN_4101 = struct.Struct("!Ii800s5B48s5B10IBIIIBIIB")
LOGIN_4101_TAIL = (
    0, 0, 0, 0,  # u8: unknown (808 or 864 != 0 sets a menu flag)
    8,  # u8: unknown, read by room code (session+0x3b4c)
    b"",  # [48]: unknown string (session+0x5ed8d)
    0, 0, 0, 0, 0,  # u8: unknown (861 == 0 sets two UI flags)
    # connection bars (FUN_00d710a0): ping ms for 3/2/1 bars and bandwidth
    # per peer for 3/2/1 bars, interleaved as on the wire
    60, 1000000, 100, 128000, 150, 1,
    4, 6, 8,  # u32: unknown, never read
    2000,  # u32: in-match net setting (default 2000)
    1,  # u8: unknown (session+0x3b75)
    300,  # u32: in-match net setting (default 300 offline)
    783, 3661,  # u32: unknown, copied into a report struct (FUN_008bda90)
    0,  # u8: pending room invitations at login (same counter as 0x4c21)
    100000, 100001,  # u32: unknown (default 100000)
    0,  # u8: unknown (session+0x3b76)
)

# Profile record, PES6 0x4103 layout + PES2009 extensions (parser
# FUN_00d334b0; offsets are packet offsets, after the result u32):
#   4 id, 8 name[48], 56 group id, 60 group name[48], 108 group status,
#   109 division, 110 points, 114 rating/games/wins/losses/draws/streak/
#   best streak/disconnects (u16), 130 goals scored/allowed (u32),
#   138 comment[256], 394 rank, 398 medals: competition gold/silver, unk,
#   winners cup gold/silver, unk (u16), 410 u8 unk, 411 u8 language,
#   412 last 5 teams (u16), 422 settings[28] (0x4120, PROFILE_SETTINGS),
#   450 2 u32, 458 12 u8, 470 7 u16, 484 2 u32, 492 5 u16 (0xffff),
#   502 u64: unknown; 510 game version[16].
PROFILE_4103 = struct.Struct("!i48si48sBBi8Hii256si6HBB5H28sII12s7HII5HQ16s")

# 0x3042 (select profile; parser FUN_00d3d2b0) is a shorter record. Goalserver
# sends the 0x4103 layout minus the id (398 bytes), so from 124 on its bytes
# are misread by the client. Wire: result, name[48], group id, group name[48],
# group status, division, points, rating/games/wins/losses/draws/streak/best
# streak (u16; no disconnects), 124 goals scored/allowed (u32, by analogy to
# 0x4103), 132 comment[256], 388 rank, 392 u32 unk, 396 u64 unk (404 bytes).
PROFILE_3042 = struct.Struct("!I48si48sBBi7Hii256siiQ")


# 0x4120 (client -> server) = 28-byte settings block + comment (NUL-terminated).
# The block maps 1:1 onto 0x4103[422:450] (reader FUN_00d334b0). Fields in
# data9.SETTINGS_COLUMNS order: u32 unk_0, u16 birth_year (guess),
# u8 birth_month, u8 birth_day, u8 unk_8, u16 country, u16 unk_11,
# u16 fav_team, u32 fav_player, u8 unk_19, u32 unk_20, u32 unk_24.
PROFILE_SETTINGS = struct.Struct("!IHBBBHHHIBII")

PRESET_PHRASE_REJECTED = 0xFFFFFFFF  # 0x4e01 result for a banned phrase
INVITE_REJECTED = 0xFFFFFFFF  # 0x4c22 result for a room invitation

ROOM_SLOTS = 8  # PES2009 rooms carry 8 player slots (PES6: 4)
GAME_VERSION = b"1.00.02"  # sent by goalserver in room/profile info


def normalizeSerial(serial) -> str:
    """'apvm-69n6-...' / b'APVM69N6...\\0' -> 'APVM69N6...'"""
    serial = util.toUnicode(
        util.stripZeros(serial) if isinstance(serial, bytes) else serial or ""
    )
    return re.sub(r"[\s-]", "", serial).upper()


def recordStats(profile, stats):
    """u16 run of 0x4103 (0x3042 takes the first 7)"""
    return (
        profile.rating,  # "Categoria" on the records screen
        stats.wins + stats.losses + stats.draws,
        stats.wins,
        stats.losses,
        stats.draws,
        stats.streak_current,
        stats.streak_best,
        profile.disconnects,
    )


def getHomePlayerNames(match: lobby.Match6) -> str:
    home_players = [match.teamSelection.home_captain]
    home_players.extend(match.teamSelection.home_more_players)
    return ",".join([x.name for x in home_players])


def getAwayPlayerNames(match: lobby.Match6) -> str:
    away_players = [match.teamSelection.away_captain]
    away_players.extend(match.teamSelection.away_more_players)
    return ",".join([x.name for x in away_players])


class NewsProtocol(pes6.NewsProtocol):
    """
    News-service for PES2009
    """

    GREETING = {
        "title": "SYSTEM: Fiveserver v%s",
        "text": (
            "Welcome to Fiveserver -\r\n"
            "independent community server\r\n"
            "supporting PES2009 games.\r\n"
            "Have a good time, play some nice\r\n"
            "football and try to score goals.\r\n"
            "\r\n"
            "Credits:\r\n"
            "Protocol analysis: reddwarf, juce\r\n"
            "Server programming: juce, reddwarf"
        ),
    }

    SERVER_NAME = "Fiveserver"

    NEW_FEATURES = {
        "0.5.1": ("NEW features in 0.5.1:", "* introducing PES2009 support!\r\n")
    }

    def register(self):
        pes6.NewsProtocol.register(self)
        self.addHandler(0x2200, self.getWebServerList_2200)
        self.addHandler(0x2300, self.do_2300)

    def getServerList_2005(self, pkt):
        myport = self.transport.getHost().port
        gameName = None
        for name, port in self.factory.serverConfig.GamePorts.items():
            if port == myport:
                gameName = name
                break
        serverIP = self.factory.configuration.serverIP_wan
        loginPort = self.factory.serverConfig.NetworkServer["loginService"][gameName]
        mainPort = self.factory.serverConfig.NetworkServer["mainService"]
        menuPort = self.factory.serverConfig.NetworkServer["networkMenuService"]
        numOnline = max(0, self.factory.getNumUsersOnline() - 1)
        # PES2009 PC expects 6 entries (a=0); binary captures confirm 366 bytes = 6×61
        servers = [
            (0, 1, "UNK_MENU", serverIP, myport, 0, 1),
            (0, 2, "LOGIN", serverIP, loginPort, 0, 200),
            (0, 3, self.SERVER_NAME, serverIP, mainPort, numOnline, 300),
            (0, 8, "UNK_MENU", serverIP, myport, 0, 803),
            (0, 8, "UNK_MENU", serverIP, myport, 0, 804),
            (0, 8, "NETWORK_MENU", serverIP, menuPort, 0, 805),
        ]
        data = b"".join(
            b"%s%s%s%s%s%s%s"
            % (
                struct.pack("!i", a),
                struct.pack("!i", b),
                name.encode("utf-8") + b"\0" * (32 - len(name[:32])),
                ip.encode("utf-8") + b"\0" * (15 - len(ip)),
                struct.pack("!H", port),
                struct.pack("!H", c),
                struct.pack("!H", d),
            )
            for a, b, name, ip, port, c, d in servers
        )
        self.sendZeros(0x2002, 4)
        self.sendData(0x2003, data)
        self.sendZeros(0x2004, 4)

    def getWebServerList_2200(self, pkt):
        base = getattr(self.factory.serverConfig, "WebBaseUrl", None)
        self.sendData(0x2201, struct.pack("!II", 0, 6))
        for data in webPagePackets(base):
            self.sendData(0x2202, data)
        self.sendZeros(0x2203, 4)

    def do_2300(self, pkt):
        self.sendData(0x2301, b"\0\0\0\0\x01\x31")


class RosterHandler:
    """
    Provide means of extracting roster hash
    from the client auth packet data.
    """

    def getRosterHash(self, pkt_data):
        # 0x3003 (pes2009.exe FUN_00d3de60): [0x32:0x42] = md5(nonce +
        # md5(player data)), [0x42:0x52] = md5(nonce + md5(team data)).
        # nonce = the 16 bytes we send in 0x3002; keep them constant (zeros)
        # or hashes stop being comparable between users.
        return pkt_data[0x32:0x52]

    def disconnect_0003(self, pkt):
        # PES2009 waits for the server to close after 0x0003 before moving to
        # the next service (goalserver closes the socket on 0x0003 too)
        super().disconnect_0003(pkt)
        self.transport.loseConnection()

    # PES5/PES6 in-game options backup (0x308a/0x3088/0x3089) is not stored
    # for PES2009: schema9's `settings` table holds the 0x4120 block instead.
    def askForSettings_308a(self, pkt):
        self.sendData(0x3087, struct.pack("!I", 0xFFFFFEDD))  # "no settings"

    def do_3088(self, pkt):
        pass

    def do_3089(self, pkt):
        self.sendZeros(0x308B, 4)


class LoginService(RosterHandler, pes6.LoginService):
    """
    Login-service for PES2009
    """

    AUTH_RESULT_SUFFIX = b"\0"

    def checkSerial(self, pkt_data, usr):
        """
        0x3003 carries the game serial at [0x5a:0x6e] (20 chars). The user
        hash doesn't include it, so compare against the registered serial.
        """
        return normalizeSerial(pkt_data[0x5A:0x6E]) == normalizeSerial(usr.serial)

    @defer.inlineCallbacks
    def getProfiles_3010(self, pkt):
        # the client parses a single profile per account (0x3012 = 70 bytes)
        profiles = self._user.profiles[:1]
        if self.factory.serverConfig.ShowStats:
            results = yield defer.DeferredList(
                [self.factory.matchData.getGames(profile.id) for profile in profiles]
            )
        else:
            # hide all stats
            results = yield defer.succeed([(True, 0) for profile in profiles])
            profiles = [self.makePristineProfile(profile) for profile in profiles]
        data = b"\0" * 4 + b"".join(
            [
                b"%(index)s%(id)s%(name)s%(playTime)s"
                b"%(division)s%(points)s%(rating)s%(games)s"
                % {
                    b"index": struct.pack("!B", i),
                    b"id": struct.pack("!i", profile.id),
                    b"name": util.padWithZeros(profile.name, 48),
                    b"division": struct.pack(
                        "!B", self.factory.ratingMath.getDivision(profile.points)
                    ),
                    b"playTime": struct.pack(
                        "!i", int(profile.playTime.total_seconds())
                    ),
                    b"points": struct.pack("!i", profile.points),
                    b"games": struct.pack("!H", games),
                    b"rating": struct.pack("!H", profile.rating),
                }
                for (_, games), (i, profile) in zip(results, enumerate(profiles))
            ]
        )
        self.sendData(0x3012, data)
        defer.returnValue(None)

    def createProfile_3020(self, pkt):
        # PES2009 has one profile per account: slot 0 only, and only while
        # the account has none (id 0 = empty; deleted profiles reset to 0)
        if pkt.data[0] != 0 or self._user.profiles[0].id > 0:
            log.msg(
                "Profile create refused for {%s}: slot %d, existing id %d"
                % (self._user.username, pkt.data[0], self._user.profiles[0].id)
            )
            self.sendData(0x3022, struct.pack("!I", 0xFFFFFFFF))
            return
        return super().createProfile_3020(pkt)

    @defer.inlineCallbacks
    def selectProfile_3040(self, pkt):
        profileId = struct.unpack("!i", pkt.data[0:4])[0]
        index, self._user.profile = self._user.getProfileById(profileId)
        if self._user.profile is None:
            log.msg("ERROR: user profile not found for id: %d" % profileId)
            self.sendZeros(0x3041, 4)
            return
        profile = self._user.profile
        if not self.factory.serverConfig.ShowStats:
            profile = self.makePristineProfile(profile)
        stats = yield self.getStats(profile.id)
        self.sendData(
            0x3042,
            PROFILE_3042.pack(
                0,
                util.padWithZeros(profile.name, 48),
                0,  # group id
                b"",  # group name
                0,  # group member status
                self.factory.ratingMath.getDivision(profile.points),
                profile.points,
                *recordStats(profile, stats)[:7],
                stats.goals_scored,
                stats.goals_allowed,
                util.padWithZeros(profile.comment or "", 256),
                profile.rank,
                0,
                0,
            ),
        )

    def getMatchResults_3070(self, pkt):
        self.sendZeros(0x3071, 4)
        self.sendZeros(0x3073, 4)

    def do_3120(self, pkt):
        self.sendZeros(0x3121, 4)
        self.sendZeros(0x3123, 0)


class LoginServicePES2009(LoginService):
    """
    Specific implementation of login service for PES2009
    """

    def __init__(self):
        LoginService.__init__(self)
        self.gameName = "pes2009"


class NetworkMenuService(RosterHandler, pes6.NetworkMenuService):
    """
    PES2009 implementation.
    The service that communicates with the player, when
    he/she is in the "NETWORK MENU" mode.
    """

    def formatPlayerInfo(self, usr, roomId, stats=None):
        """
        183-byte player record used by 0x4212/0x4220/0x4222
        (client parser pes2009.exe FUN_00d34ea0)
        """
        if stats is None:
            stats = user.Stats(usr.profile.id, 0, 0, 0, 0, 0, 0, 0)
        return b"".join(
            [
                struct.pack("!i", usr.profile.id),
                util.padWithZeros(usr.profile.name, 48),
                struct.pack("!i", 0),  # group id
                b"\0" * 48,  # group name
                b"\0",  # group member status
                struct.pack(
                    "!B", self.factory.ratingMath.getDivision(usr.profile.points)
                ),
                struct.pack("!i", roomId),
                struct.pack("!i", usr.profile.points),
                struct.pack("!H", usr.profile.rating),
                struct.pack("!H", stats.wins + stats.losses + stats.draws),
                struct.pack("!H", stats.wins),
                struct.pack("!H", stats.losses),
                struct.pack("!H", stats.draws),
                b"\0" * 47,  # u8*3, u64, u32, u32, u16, [6], [20]: unknown
                b"\x3c" + b"\0" * 11,  # 12 u8: unknown, goalserver sends 0x3c first
            ]
        )

    def formatProfileInfo(self, profile, stats, settings=None):
        """
        0x4103 payload minus the leading result u32 (PROFILE_4103, 522 bytes).
        settings = stored 0x4120 values (data9.SETTINGS_COLUMNS).
        """
        if not self.factory.serverConfig.ShowStats:
            profile = self.makePristineProfile(profile)
        teams = list(stats.teams)[:5]
        settings = settings or data9.DEFAULT_SETTINGS
        return PROFILE_4103.pack(
            profile.id,
            util.padWithZeros(profile.name, 48),
            0,  # group id
            b"",  # group name
            0,  # group member status
            self.factory.ratingMath.getDivision(profile.points),
            profile.points,
            *recordStats(profile, stats),
            stats.goals_scored,
            stats.goals_allowed,
            util.padWithZeros(profile.comment or "", 256),
            profile.rank,
            0, 0, 0, 0, 0, 0,  # medals (competition, winners cup) + unknown
            0,  # unknown
            0,  # language
            *teams, *[0xFFFF] * (5 - len(teams)),  # teams of the last 5 matches
            PROFILE_SETTINGS.pack(*[settings[c] for c in data9.SETTINGS_COLUMNS]),
            0, 0,
            b"",
            0, 0, 0, 0, 0, 0, 0,
            0, 0,
            *[0xFFFF] * 5,
            0,
            GAME_VERSION,
        )

    @defer.inlineCallbacks
    def getProfile_4102(self, pkt):
        profileId = struct.unpack("!i", pkt.data[0:4])[0]
        profile = yield self.factory.getPlayerProfile(profileId)
        if not profile:
            self.sendZeros(0x4103, 0)
            return
        stats = yield self.getStats(profile.id)
        settings = yield self.factory.profileData.getSettings(profile.id)
        self.sendData(
            0x4103, b"\0\0\0\0" + self.formatProfileInfo(profile, stats, settings)
        )

    @defer.inlineCallbacks
    def setProfileSettings_4120(self, pkt):
        """
        0x4120: settings[28] (birthday, country, fav team/player, ...)
        + comment (NUL-terminated, the game allows 40 chars). Reply 0x4121.
        """
        size = PROFILE_SETTINGS.size
        settings = dict(
            zip(
                data9.SETTINGS_COLUMNS,
                PROFILE_SETTINGS.unpack(bytes(pkt.data[:size]).ljust(size, b"\0")),
            )
        )
        log.msg("Profile settings for {%s}: %s" % (self._user.profile.name, settings))
        profile = self._user.profile
        profile.comment = util.toUnicode(util.stripZeros(pkt.data[size:]))
        yield self.factory.profileData.storeSettings(profile.id, settings)
        yield self.factory.storeProfile(profile)
        self.sendZeros(0x4121, 4)

    def containsBannedWord(self, text):
        """Chat.bannedWords from the yaml, case-insensitive substring match"""
        text = util.toUnicode(text).lower()
        return any(
            word.lower() in text
            for word in self.factory.serverConfig.Chat["bannedWords"]
        )

    def setPresetPhrase_4e00(self, pkt):
        """
        0x4e00: custom preset phrase (NUL-terminated). 0x4e01 (parser
        FUN_00d3f700) is a u32 result: 0 = accepted, anything else = rejected.
        """
        phrase = util.stripZeros(pkt.data)
        if self.containsBannedWord(phrase):
            log.msg(
                "Preset phrase from {%s} rejected (banned word): %s"
                % (self._user.profile.name, util.toUnicode(phrase))
            )
            # ponytail: which non-zero code maps to which in-game message is
            # unknown; 0xffffffff = generic failure. Tune after testing.
            self.sendData(0x4E01, struct.pack("!I", PRESET_PHRASE_REJECTED))
        else:
            self.sendZeros(0x4E01, 4)

    def getInviteHistory_4c10(self, pkt):
        """
        0x4c10: profile id u32 -> list of recently met players to invite.
        Reply 0x4c11 result u32, 0x4c12 entries (FUN_00d3f8b0, max 20, each
        FUN_00d34200: profile id u32 + name[0x30] + u32 + u8), 0x4c13 end.
        TODO: keep a history of opponents/room mates and send it as 0x4c12.
        For now: always an empty list.
        """
        self.sendZeros(0x4C11, 4)
        self.sendZeros(0x4C13, 0)

    def inviteToRoom_4c20(self, pkt):
        """
        0x4c20: invite player into my room: name[0x30] + u8.
        The reply is 0x4c22 (u32 result, 0 = sent; menu waits on it in
        FUN_00d50980), NOT 0x4c21: 0x4c21 is an unsolicited u8 "pending
        invitations" counter (FUN_00d508e0, >0 = has invites).
        TODO: implement invitations (find the invitee by name, bump their
        0x4c21 counter, relay their answer; 0x4c23 = u32 result, unknown use).
        Rejected for now.
        """
        name = util.toUnicode(util.stripZeros(pkt.data[:0x30]))
        log.msg("Room invitation to {%s} rejected: not implemented" % name)
        # ponytail: non-zero = failure; which code shows which message unknown
        self.sendZeros(0x4C22, 4)
        self.sendZeros(0x4C23, 4)

    def register(self):
        pes6.NetworkMenuService.register(self)
        self.addHandler(0x4120, self.setProfileSettings_4120)
        self.addHandler(0x4C10, self.getInviteHistory_4c10)
        self.addHandler(0x4C20, self.inviteToRoom_4c20)
        self.addHandler(0x4E00, self.setPresetPhrase_4e00)

    def do_4100(self, pkt):
        profileIndex = struct.unpack("!B", pkt.data[0:1])[0]
        self._user.profile = self._user.profiles[profileIndex]
        self.sendData(
            0x4101,
            LOGIN_4101.pack(
                0, self._user.profile.id, ONLINE_TEAMS_BLOCK, *LOGIN_4101_TAIL
            ),
        )


class MainService(NetworkMenuService, pes6.MainService):
    """
    PES2009 implementation
    The main game server, which keeps track of matches, goals
    and other important statistics.
    """

    @defer.inlineCallbacks
    def connectionLost(self, reason):
        pes6.LoginService.connectionLost(self, reason)
        if self._user.state:
            room = self._user.state.room
            if room:
                room.cancelParticipation(self._user)
                yield self.exitingRoom(room, self._user)
                # update participation of remaining players in room
                data = self.formatRoomParticipationStatus(room)
                for player in room.players:
                    player.sendData(0x4365, data)
            self.exitingLobby(self._user)

    def formatHomeOrAway(self, room, usr):
        if room.teamSelection:
            return room.teamSelection.getHomeOrAway(usr)
        return 0xFF

    def formatTeamsAndGoals(self, room):
        homeTeam, awayTeam = 0xFFFF, 0xFFFF
        if room.teamSelection:
            homeTeam = (
                room.teamSelection.home_team_id
                if room.teamSelection.home_team_id != None
                else 0xFFFF
            )
            awayTeam = (
                room.teamSelection.away_team_id
                if room.teamSelection.away_team_id != None
                else 0xFFFF
            )
        homeGoals1st, homeGoals2nd, homeGoalsEt1, homeGoalsEt2, homeGoalsPen = (
            0,
            0,
            0,
            0,
            0,
        )
        awayGoals1st, awayGoals2nd, awayGoalsEt1, awayGoalsEt2, awayGoalsPen = (
            0,
            0,
            0,
            0,
            0,
        )
        if room.match:
            homeGoals1st = room.match.score_home_1st
            homeGoals2nd = room.match.score_home_2nd
            homeGoalsEt1 = room.match.score_home_et1
            homeGoalsEt2 = room.match.score_home_et2
            homeGoalsPen = room.match.score_home_pen
            awayGoals1st = room.match.score_away_1st
            awayGoals2nd = room.match.score_away_2nd
            awayGoalsEt1 = room.match.score_away_et1
            awayGoalsEt2 = room.match.score_away_et2
            awayGoalsPen = room.match.score_away_pen
        return b"%s%s%s%s%s%s%s%s%s%s%s%s" % (
            struct.pack("!H", homeTeam),
            struct.pack("!B", homeGoals1st),  # 1st
            struct.pack("!B", homeGoals2nd),  # 2nd
            struct.pack("!B", homeGoalsEt1),  # et1
            struct.pack("!B", homeGoalsEt2),  # et2
            struct.pack("!B", homeGoalsPen),  # pen
            struct.pack("!H", awayTeam),
            struct.pack("!B", awayGoals1st),  # 1st
            struct.pack("!B", awayGoals2nd),  # 2nd
            struct.pack("!B", awayGoalsEt1),  # et1
            struct.pack("!B", awayGoalsEt2),  # et2
            struct.pack("!B", awayGoalsPen),
        )  # pen

    def formatRoomInfo(self, room):
        """
        324-byte room record used by 0x4302/0x4306
        (client parser pes2009.exe FUN_00d36c00)
        """
        n = len(room.players)
        if room.match:
            match_state = room.match.state
            match_clock = room.match.clock
        else:
            match_state, match_clock = 0, 0
        return b"%s%s%s%s%s%s%s%s%s%s%s" % (
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
                        # matchstarter or 1st host?
                        struct.pack("!B", room.isMatchStarter(usr)),
                        struct.pack("!B", self.formatHomeOrAway(room, usr)),  # team
                        struct.pack("!B", usr.state.spectator),  # spectator
                        struct.pack("!B", room.getPlayerPosition(usr)),  # pos in room
                        struct.pack("!B", room.getPlayerParticipate(usr)),
                    )  # participate
                    for usr in room.players
                ]
            ),
            b"\0\0\0\0\0\0\xff\0\0\xff" * (ROOM_SLOTS - n),  # empty players
            self.formatTeamsAndGoals(room),
            # settings from 0x4310. ponytail: order of the last 4 inferred by
            # matching goalserver bytes (04 01 03 00) to a 0x4310 capture
            struct.pack(
                "!BBBBBBBBBB",
                0,
                int(room.usePassword),
                0,
                0,
                0,
                0,
                room.maxPlayers,
                room.isFriendly,
                room.teamCategory,
                room.limitInvitations,
            ),
            b"\0" * 68,  # 60-byte block (FUN_00d35600) + u32 + u32: unknown
            util.padWithZeros(GAME_VERSION, 16) + b"\0",
        )

    def formatRoomParticipationStatus(self, room):
        """
        Used to format the 0x4365 payload
        """

        n = len(room.players)
        data = b"%s%s" % (
            b"".join(
                [
                    b"%s%s%s"
                    % (
                        struct.pack("!i", usr.profile.id),
                        struct.pack("!B", room.getPlayerPosition(usr)),
                        struct.pack("!B", room.getPlayerParticipate(usr)),
                    )
                    for usr in room.players
                ]
            ),
            b"\0\0\0\0\0\xff" * (ROOM_SLOTS - n),
        )
        return data

    def formatStunInfo(self, usr, room, prefix):
        """
        0x4330 (prefix=4 zero bytes) / 0x4347 (no prefix) record:
        ONLINE_TEAMS_BLOCK + ip1 + port1 + ip2 + port2 + profile id + ...
        """
        data = b"%s%s%s%s%s%s%s" % (
            prefix,
            ONLINE_TEAMS_BLOCK,
            util.padWithZeros(usr.state.ip1, 16),
            struct.pack("!H", usr.state.udpPort1),
            util.padWithZeros(usr.state.ip2, 16),
            struct.pack("!H", usr.state.udpPort2),
            struct.pack("!i", usr.profile.id),
        )
        participate = struct.pack("!B", room.getPlayerParticipate(usr))
        if prefix:
            return data + b"\x01\x01" + participate  # 0x4330, as goalserver
        return data + b"\0\0" + participate + b"\0" * 8  # 0x4347

    def sendStunInfo(self, room):
        """0x4346/0x4347*/0x4348: connection info of everyone in the room"""
        self.sendZeros(0x4346, 4)
        for usr in room.players:
            self.sendData(0x4347, self.formatStunInfo(usr, room, b""))
        self.sendZeros(0x4348, 4)

    def becomeSpectator_4366(self, pkt):
        self._user.state.spectator = 1
        # 0x4367 (FUN_00d41840): result u32 + spectator u8
        self.sendData(0x4367, b"\0\0\0\0\x01")

    def do_4351(self, pkt):
        """
        Contains connection information of playing players
        Received from hosting player
        Send to possible spectators
        """
        data = bytes(pkt.data)
        room = self._user.state.room
        if room:
            spectatingPlayers = (
                player
                for player in room.players
                if player not in room.participatingPlayers
            )
            for player in spectatingPlayers:
                player.sendData(0x4351, data)
        self.sendZeros(0x4352, 4)

    def backToMatchMenu_4383(self, pkt):
        """
        0x4384 (pes2009.exe FUN_00d332b0): result u32, then 8 records of
        id u32, added points u16, new points u32, u16, u16, 7 x u8.
        No group section (PES6 had one).
        """
        room = self._user.state.room
        players = room.participatingPlayers[:ROOM_SLOTS]
        data = b"\0\0\0\0" + b"".join(
            struct.pack("!iHiHH", usr.profile.id, 0, usr.profile.points, 0, 0)
            + b"\0" * 7
            for usr in players
        )
        data += b"\0" * 21 * (ROOM_SLOTS - len(players))
        self.sendData(0x4384, data)

    def quickGameSearch_6020(self, pkt):
        self.sendZeros(0x6021, 0)

    def getStunInfo_4345(self, pkt):
        roomId = struct.unpack("!i", pkt.data[0:4])[0]
        thisLobby = self.factory.getLobbies()[self._user.state.lobbyId]
        room = thisLobby.getRoomById(roomId)
        if room is None:
            self.sendZeros(0x4346, 4)
            self.sendZeros(0x4348, 4)
            return
        self.sendStunInfo(room)

    def chat_4400(self, pkt):
        thisLobby = self.factory.getLobbies()[self._user.state.lobbyId]
        chatType = pkt.data[0:2]
        message = util.stripZeros(pkt.data[10:])
        if self.containsBannedWord(message):
            message = b"[%s]" % self.factory.serverConfig.Chat["warningMessage"].encode(
                "utf-8"
            )
        data = b"%s%s%s%s%s" % (
            chatType,
            pkt.data[2:6],
            struct.pack("!i", self._user.profile.id),
            util.padWithZeros(self._user.profile.name, 48),
            # util.padWithZeros(message, 128))
            message[:126] + b"\0\0",
        )
        if chatType == b"\x00\x01":
            # add to lobby chat history
            thisLobby.addToChatHistory(
                lobby.ChatMessage(self._user.profile, message.decode("utf-8"))
            )
            # lobby chat
            for usr in thisLobby.players.values():
                usr.sendData(0x4402, data)
        elif chatType == b"\x01\x08":
            # room chat
            room = self._user.state.room
            if room:
                for usr in room.players:
                    usr.sendData(0x4402, data)
        elif chatType == b"\x00\x02":
            # private message
            profileId = struct.unpack("!i", pkt.data[6:10])[0]
            usr = thisLobby.getPlayerByProfileId(profileId)
            if usr:
                # add to lobby chat history
                thisLobby.addToChatHistory(
                    lobby.ChatMessage(
                        self._user.profile,
                        message.decode("utf-8"),
                        usr.profile,
                        pkt.data[2:6],
                    )
                )
                usr.sendData(0x4402, data)
                if usr != self._user:
                    self._user.sendData(0x4402, data)  # echo to self
            else:
                log.msg("WARN: user with profile id = " "%d not found." % profileId)
        elif chatType == b"\x01\x05":
            # match chat
            room = self._user.state.room
            if room:
                for usr in room.players:
                    usr.sendData(0x4402, data)
        elif chatType == b"\x01\x07":
            # stadium chat
            room = self._user.state.room
            if room:
                for usr in room.players:
                    usr.sendData(0x4402, data)

    def sendChatHistory(self, aLobby, who):
        if aLobby is None or who is None:
            return
        for chatMessage in list(aLobby.chatHistory):
            chatType = b"\0\1"
            if chatMessage.toProfile is not None:
                if who.profile.id not in [
                    chatMessage.fromProfile.id,
                    chatMessage.toProfile.id,
                ]:
                    continue
                special = chatMessage.special
            else:
                special = b"\0\0\0\0"
            data = b"%s%s%s%s%s" % (
                chatType,
                special,
                struct.pack("!i", chatMessage.fromProfile.id),
                util.padWithZeros(chatMessage.fromProfile.name, 48),
                chatMessage.text.encode("utf-8")[:126] + b"\0\0",
            )
            who.sendData(0x4402, data)

    def broadcastSystemChat(self, aLobby, text):
        chatMessage = lobby.ChatMessage(lobby.SYSTEM_PROFILE, text)
        for usr in aLobby.players.values():
            data = b"%s%s%s%s%s" % (
                b"\0\1",
                b"\0\0\0\0",
                struct.pack("!i", chatMessage.fromProfile.id),
                util.padWithZeros(chatMessage.fromProfile.name, 48),
                chatMessage.text.encode("utf-8")[:126] + b"\0\0",
            )
            usr.sendData(0x4402, data)
        aLobby.addToChatHistory(chatMessage)

    def broadcastRoomChat(self, room, text):
        chatMessage = lobby.ChatMessage(lobby.SYSTEM_PROFILE, text)
        for usr in room.players:
            data = b"%s%s%s%s%s" % (
                b"\x01\x08",
                b"\0\0\0\0",
                struct.pack("!i", chatMessage.fromProfile.id),
                util.padWithZeros(chatMessage.fromProfile.name, 48),
                chatMessage.text.encode("utf-8")[:126] + b"\0\0",
            )
            usr.sendData(0x4402, data)

    def sendRoomUpdate(self, room):
        thisLobby = self.factory.getLobbies()[self._user.state.lobbyId]
        data = self.formatRoomInfo(room)
        for usr in thisLobby.players.values():
            usr.sendData(0x4306, data)

    @defer.inlineCallbacks
    def sendPlayerUpdate(self, roomId):
        thisLobby = self.factory.getLobbies()[self._user.state.lobbyId]
        stats = yield self.getStats(self._user.profile.id)
        data = self.formatPlayerInfo(self._user, roomId, stats)
        for usr in thisLobby.players.values():
            usr.sendData(0x4222, data)

    @defer.inlineCallbacks
    def getUserList_4210(self, pkt):
        self.sendZeros(0x4211, 4)
        thisLobby = self.factory.getLobbies()[self._user.state.lobbyId]
        for usr in thisLobby.players.values():
            if usr.state.inRoom == 1:
                roomId = usr.state.room.id
            else:
                roomId = 0
            stats = yield self.getStats(usr.profile.id)
            data = self.formatPlayerInfo(usr, roomId, stats)
            self.sendData(0x4212, data)
        self.sendZeros(0x4213, 4)
        yield defer.succeed(None)

    def createRoom_4310(self, pkt):
        """
        0x4310: name[0x40] unk[0x40] password[0x10] use_password u8
        team_category u8 limit_invitations u8 max_players? u8 unk u8 is_friendly u8
        """
        thisLobby = self.factory.getLobbies()[self._user.state.lobbyId]
        roomName = util.stripZeros(pkt.data[0:64])
        if thisLobby.isRoom(roomName):
            self.sendData(0x4311, b"\xff\xff\xff\x10")
            return
        room = lobby.Room(thisLobby)
        room.name = roomName
        room.usePassword = pkt.data[0x90] == 1
        if room.usePassword:
            room.password = util.stripZeros(pkt.data[0x80:0x90])
        room.teamCategory = pkt.data[0x91]
        room.limitInvitations = pkt.data[0x92]
        room.maxPlayers = pkt.data[0x93]
        room.isFriendly = pkt.data[0x95]
        # put room creator into the room
        room.enter(self._user)
        # add room to the lobby
        thisLobby.addRoom(room)
        log.msg("Room created: %s" % repr(room))
        # notify all users in the lobby about the new room
        self.sendRoomUpdate(room)
        # notify all users in the lobby that player is now in a room
        self.sendPlayerUpdate(room.id)
        self.sendZeros(0x4311, 4)
        self.sendStunInfo(room)

    def getRoomList_4300(self, pkt):
        self.sendZeros(0x4301, 4)
        thisLobby = self.factory.getLobbies()[self._user.state.lobbyId]
        for room in thisLobby.rooms.values():
            data = self.formatRoomInfo(room)
            self.sendData(0x4302, data)
        self.sendZeros(0x4303, 4)

    def setOwner_4349(self, pkt):
        newOwnerProfileId = struct.unpack("!i", pkt.data[0:4])[0]
        thisLobby = self.factory.getLobbies()[self._user.state.lobbyId]
        room = self._user.state.room
        if room:
            usr = thisLobby.getPlayerByProfileId(newOwnerProfileId)
            if not usr:
                log.msg("WARN: player %s cannot become owner: not in the room.")
            else:
                room.setOwner(usr)
                self.sendRoomUpdate(room)
        self.sendZeros(0x434A, 4)

    def setRoomName_434d(self, pkt):
        """
        0x434d (pes2009.exe FUN_00d48380):
        name[0x80] use_password u8 password[0x10]
        """
        newName = util.stripZeros(pkt.data[0:0x80])
        thisLobby = self.factory.getLobbies()[self._user.state.lobbyId]
        room = self._user.state.room
        data = b"\0\0\0\0"
        if room:
            if newName != room.name:
                # prevent renaming to existing rooms
                if thisLobby.isRoom(newName):
                    data = b"\xff\xff\xff\xff"
                else:
                    thisLobby.renameRoom(room, newName)
            room.usePassword = pkt.data[0x80] == 1
            if room.usePassword:
                room.password = util.stripZeros(pkt.data[0x81:0x91])
            self.sendRoomUpdate(room)
        self.sendData(0x434E, data)

    def do_4330(self, room):
        """
        Notify people INSIDE room of
        ip,ports and participation status
        """
        data = self.formatStunInfo(self._user, room, b"\0\0\0\0")
        for otherUsr in room.players:
            if otherUsr != self._user:
                otherUsr.sendData(0x4330, data)

    def joinRoom_4320(self, pkt):
        """0x4320: room_id u32, password[0x10], unk u8"""
        roomId = struct.unpack("!i", pkt.data[0:4])[0]
        thisLobby = self.factory.getLobbies()[self._user.state.lobbyId]
        room = thisLobby.getRoomById(roomId)
        if room is None:
            log.msg("ERROR: Room (id=%d) does not exist." % roomId)
            self.sendData(0x4321, b"\0\0\0\1\0")
            return
        if room.usePassword and util.stripZeros(pkt.data[4:20]) != room.password:
            log.msg("ERROR: Room (id=%d) password does not match." % roomId)
            self.sendData(0x4321, b"\xff\xff\xfd\xda\0")
            return
        room.enter(self._user)
        self.sendRoomUpdate(room)
        self.sendPlayerUpdate(room.id)
        matchTime = room.matchSettings.match_time if room.matchSettings else 0
        self.sendData(0x4321, b"\0\0\0\0" + struct.pack("!B", matchTime))
        # give players in room stun of joiner, then joiner stun of everyone
        self.do_4330(room)
        self.sendStunInfo(room)

    def exitingLobby(self, usr):
        usrLobby = self.factory.getLobbies()[usr.state.lobbyId]
        usrLobby.exit(usr)
        # user now considered OFFLINE
        self.factory.userOffline(usr)
        # notify every remaining occupant in the lobby
        for otherUsr in usrLobby.players.values():
            otherUsr.sendData(0x4221, struct.pack("!i", usr.profile.id))

    def exitingRoom(self, room, usr):
        usrLobby = self.factory.getLobbies()[usr.state.lobbyId]
        room = usr.state.room
        room.exit(usr)

        self.sendRoomUpdate(room)
        self.sendPlayerUpdate(room.id)
        self.sendZeros(0x432B, 4)

        # destroy the room, if none left in it
        if room.isEmpty():
            # notify users in lobby that the room is gone
            data = struct.pack("!i", room.id)
            for otherUsr in usrLobby.players.values():
                otherUsr.sendData(0x4305, data)
            usrLobby.deleteRoom(room)

    def exitRoom_432a(self, pkt):
        if self._user.state.inRoom == 0:
            log.msg("WARN: user not in a room.")
            self.sendZeros(0x432B, 4)
        else:
            return self.exitingRoom(self._user.state.room, self._user)

    def toggleParticipate_4363(self, pkt):
        participate = struct.unpack("!B", pkt.data[0:1])[0] == 1
        room = self._user.state.room
        packetPayload = b"\0\0\0\0"  # success
        if room:
            if participate:
                # check roster-hash match with host
                rosterHashMismatch = False
                if room.participatingPlayers:
                    gameHost = room.participatingPlayers[0]
                    rosterHashMismatch = (
                        room.lobby.checkRosterHash
                        and not self.checkHashes(gameHost, self._user)
                    )
                if rosterHashMismatch:
                    packetPayload = b"\0\0\0\1"
                    text = (
                        "Roster mismatch: %s vs %s. "
                        "Player %s cannot participate."
                        % (
                            gameHost.profile.name,
                            self._user.profile.name,
                            self._user.profile.name,
                        )
                    )
                    log.msg(text)
                    self.broadcastRoomChat(room, text.encode("utf-8"))
                elif room.isForcedCancelledParticipation(self._user):
                    packetPayload = b"\xff\xff\xfd\xb6"  # still cancelled
                else:
                    room.participate(self._user)
            else:
                room.cancelParticipation(self._user)
            # share participation status with players in room
            data = self.formatRoomParticipationStatus(room)
            for player in room.players:
                player.sendData(0x4365, data)
        data = b"%s%s%s" % (
            packetPayload,
            struct.pack("!B", participate),
            struct.pack("!B", room.getPlayerParticipate(self._user)),
        )
        self.sendData(0x4364, data)

    def forcedCancelParticipation_4380(self, pkt):
        profileId = struct.unpack("!i", pkt.data[0:4])[0]
        thisLobby = self.factory.getLobbies()[self._user.state.lobbyId]
        room = self._user.state.room
        if room:
            usr = thisLobby.getPlayerByProfileId(profileId)
            room.cancelParticipation(usr)
            usr.state.timeCancelledParticipation = datetime.now()
            data = self.formatRoomParticipationStatus(room)
            for player in room.players:
                player.sendData(0x4365, data)
        self.sendZeros(0x4381, 4)

    def startMatch_4360(self, pkt):
        thisLobby = self.factory.getLobbies()[self._user.state.lobbyId]
        room = self._user.state.room
        if room:
            # 0x4362 (FUN_00d41b30): u8, 8 profile ids (slot order = sides:
            # even home, odd away), 8 u32 (unknown, 0), 8 u8 (goalserver 0x3c
            # per player). Client reads 73 bytes; the trailing 8 (0x02 per
            # player) are goalserver's and ignored.
            players = room.participatingPlayers[:ROOM_SLOTS]
            n = len(players)
            data = b"\x02" + b"".join(
                struct.pack("!i", usr.profile.id) for usr in players
            ).ljust(4 * ROOM_SLOTS, b"\0")
            data += b"\0" * (4 * ROOM_SLOTS)
            data += (b"\x3c" * n).ljust(ROOM_SLOTS, b"\0")
            data += (b"\x02" * n).ljust(ROOM_SLOTS, b"\0")
            for player in room.players:
                player.sendData(0x4362, data)

            # Tell everyone of new phase of room
            room.phase = lobby.RoomState.ROOM_MATCH_SIDE_SELECT
            room.setMatchStarter(self._user)
            room.readyCount = 0
            self.sendRoomUpdate(room)
        self.sendZeros(0x4361, 4)

    def updateRoomPhase(self, room):
        if room.readyCount == len(room.participatingPlayers):
            room.phase += 1

            data = struct.pack("B", room.phase)
            for usr in room.players:
                usr.sendData(0x4344, data)
            # reset count
            room.readyCount = 0
            # Tell everyone of new phase of room
            self.sendRoomUpdate(room)

    def toggleReady_436f(self, pkt):
        payload = struct.unpack("!B", pkt.data[0:1])[0]
        room = self._user.state.room
        if room:
            # phase 2-6: match not really started
            if room.isAtPregameSettings(room):
                if payload == 1:
                    room.readyCount += 1
                elif payload == 0:
                    room.readyCount -= 1

            # phase 7-8: match finished
            elif room.phase > lobby.RoomState.ROOM_MATCH_FORMATION_SELECT:
                # exit match
                if payload == 0:
                    room.cancelParticipation(self._user)
                    if len(room.participatingPlayers) == 0:
                        room.phase = lobby.RoomState.ROOM_IDLE
                        room.match = None
                    else:  # there's still a player in the endmatch screen
                        room.phase = lobby.RoomState.ROOM_MATCH_SERIES_ENDING
                # play again different teams
                elif payload == 3:
                    room.phase = lobby.RoomState.ROOM_MATCH_TEAM_SELECT
                    room.match = None
                # play again same teams
                elif payload == 4:
                    room.phase = lobby.RoomState.ROOM_MATCH_FORMATION_SELECT
                    room.match = None
                self.sendRoomUpdate(room)

            for usr in room.players:
                if usr == self._user:
                    continue
                data = b"%s%s" % (
                    struct.pack("!i", self._user.profile.id),
                    pkt.data[0:1],
                )
                usr.sendData(0x4371, data)
        self.sendZeros(0x4370, 4)

        # if all participating players are ready, next screen
        if room.isAtPregameSettings(room):
            self.updateRoomPhase(room)

    @defer.inlineCallbacks
    def setPlayerSettings_4369(self, pkt):
        # Packet contains which players are in team1 & team2
        self.sendZeros(0x436A, 4)
        room = self._user.state.room
        for usr in room.players:
            data = b"%s%s" % (b"\0", pkt.data)
            usr.sendData(0x436B, data)
        # create new TeamSelection object
        # 0x4369 (FUN_00d49050): 8 x (profile id u32 + 4 u8). The side is not
        # in the packet: like 0x4362, even slots are home, odd slots away,
        # slots 0/1 are the captains.
        room.teamSelection = lobby.TeamSelection()
        for x in range(min(ROOM_SLOTS, len(pkt.data) // 8)):
            profile_id = struct.unpack("!i", pkt.data[x * 8 : x * 8 + 4])[0]
            away = x % 2 == 1
            if profile_id != 0:
                profile = yield self.factory.getPlayerProfile(profile_id)
                if x in [0, 1]:
                    if not away:
                        room.teamSelection.home_captain = profile
                    else:
                        room.teamSelection.away_captain = profile
                else:
                    if not away:
                        room.teamSelection.home_more_players.append(profile)
                    else:
                        room.teamSelection.away_more_players.append(profile)
        self.sendRoomUpdate(room)

    def setGameSettings_436c(self, pkt):
        # Packet contains game settings(time,injuries,penalty etcetera)
        self.sendZeros(0x436D, 4)
        room = self._user.state.room
        data = bytes(pkt.data)
        room.matchSettings = lobby.MatchSettings(*pkt.data)
        for usr in room.players:
            usr.sendData(0x436E, data)
        self.sendRoomUpdate(room)

    def goalScored_4375(self, pkt):
        room = self._user.state.room
        if not room.match:
            log.msg("ERROR: Goal reported, but no match in the room.")
        else:
            if pkt.data[0] == 0:
                log.msg(
                    "GOAL SCORED by HOME team %d (%s)"
                    % (room.teamSelection.home_team_id, getHomePlayerNames(room.match))
                )
                room.match.goalHome()
            else:
                log.msg(
                    "GOAL SCORED by AWAY team %d (%s)"
                    % (room.teamSelection.away_team_id, getAwayPlayerNames(room.match))
                )
                room.match.goalAway()
            log.msg(
                "UPDATE: Team %d (%s) vs Team %d (%s) - %d:%d (in progress)"
                % (
                    room.teamSelection.home_team_id,
                    getHomePlayerNames(room.match),
                    room.teamSelection.away_team_id,
                    getAwayPlayerNames(room.match),
                    room.match.score_home,
                    room.match.score_away,
                )
            )
        self.sendZeros(0x4376, 4)
        # let others in the lobby know
        self.sendRoomUpdate(room)

    def matchClockUpdate_4385(self, pkt):
        clock = struct.unpack("!B", pkt.data[0:1])[0]
        room = self._user.state.room
        if not room or not room.match:
            log.msg("ERROR: got clock update, but no match")
        else:
            room.match.clock = clock
            log.msg(
                "CLOCK: Team %d (%s) vs Team %d (%s). Minute: %d"
                % (
                    room.teamSelection.home_team_id,
                    getHomePlayerNames(room.match),
                    room.teamSelection.away_team_id,
                    getAwayPlayerNames(room.match),
                    room.match.clock,
                )
            )
        self.sendZeros(0x4386, 4)
        # let others in the lobby know
        self.sendRoomUpdate(room)

    @defer.inlineCallbacks
    def recordMatchResult(self, room):
        match = room.match
        duration = datetime.now() - match.startDatetime
        friendly = match.friendly
        log.msg(
            "MATCH FINISHED (%s): "
            "Team %d (%s) - Team %d (%s)  %d:%d. "
            "Match time: %s."
            % (
                "friendly" if friendly else "ranking",
                match.teamSelection.home_team_id,
                getHomePlayerNames(match),
                match.teamSelection.away_team_id,
                getAwayPlayerNames(match),
                match.score_home,
                match.score_away,
                duration,
            )
        )
        # PES2009: every match is stored with its friendly flag; ranking
        # stats/points only count non-friendly ones (data9.MatchData)
        yield self.factory.matchData.store(match)
        participants = [
            match.teamSelection.home_captain,
            match.teamSelection.away_captain,
        ]
        participants.extend(match.teamSelection.home_more_players)
        participants.extend(match.teamSelection.away_more_players)
        for profile in participants:
            profile.playTime += duration
            if not friendly:
                # re-calculate points from ranked results
                stats = yield self.getStats(profile.id)
                profile.points = self.factory.ratingMath.getPoints(stats)
            yield self.factory.storeProfile(profile)

    def matchStateUpdate_4377(self, pkt):
        state = struct.unpack("!B", pkt.data[0:1])[0]
        room = self._user.state.room
        if not room or not room.teamSelection:
            log.msg("ERROR: got match state update, " "but no room or team-selection")
        else:
            if room.match is not None:
                room.match.state = state
            # check if match just started
            if state == lobby.MatchState.FIRST_HALF:
                match = lobby.Match6(room.teamSelection)
                log.msg(
                    "NEW MATCH started: Team %d (%s) vs Team %d (%s)"
                    % (
                        room.teamSelection.home_team_id,
                        getHomePlayerNames(room),
                        room.teamSelection.away_team_id,
                        getAwayPlayerNames(room),
                    )
                )
                match.startDatetime = datetime.now()
                match.home_team_id = match.teamSelection.home_team_id
                match.away_team_id = match.teamSelection.away_team_id
                # friendly vs ranking is a room setting (0x4310), not a lobby one
                match.friendly = bool(room.isFriendly)
                room.match = match
                room.match.state = state
            # check if match is done
            elif state == lobby.MatchState.FINISHED and room.match:
                room.phase = lobby.RoomState.ROOM_MATCH_FINISHED
                self.recordMatchResult(room)
            # let others in the lobby know
            self.sendRoomUpdate(room)
        self.sendZeros(0x4378, 4)

    def teamSelected_4373(self, pkt):
        team = struct.unpack("!H", pkt.data[0:2])[0]
        log.msg("Team selected: %d" % team)
        room = self._user.state.room
        if not room.teamSelection:
            log.msg("ERROR: room has no TeamSelection object")
        else:
            ts = room.teamSelection
            if self._user.profile.id == ts.home_captain.id:
                ts.home_team_id = team
            elif self._user.profile.id == ts.away_captain.id:
                ts.away_team_id = team
        self.sendData(0x4374, b"\0\0\0\0")
        self.sendRoomUpdate(room)

    @defer.inlineCallbacks
    def setComment_4110(self, pkt):
        self._user.profile.comment = pkt.data
        yield self.factory.storeProfile(self._user.profile)
        self.sendZeros(0x4111, 4)

    def relayRoomSettings_4350(self, pkt):
        if not self._user.state:
            return
        room = self._user.state.room
        if room:
            if pkt.data[0:4] == b"\0\0\1\3":  # TODO clean this up (3 - phase?)
                # extract info that we care about
                room.matchTime = 5 * (struct.unpack("!B", pkt.data[12:13])[0] + 1)
                log.msg("match time set to: %d minutes" % room.matchTime)
            # send to others
            for usr in self._user.state.room.players:
                if usr == self._user:
                    continue
                usr.sendData(0x4350, pkt.data)

    def do_3087(self, pkt):
        """
        Do nothing.
        Overriden here to mask pes5 logic
        """

    def do_4c00(self, pkt):
        self.sendZeros(0x4C01, 4)
        self.sendZeros(0x4C03, 4)

    def register(self):
        pes6.MainService.register(self)
        # pes5/pes6 chain skips our NetworkMenuService.register
        self.addHandler(0x4120, self.setProfileSettings_4120)
        self.addHandler(0x4C10, self.getInviteHistory_4c10)
        self.addHandler(0x4C20, self.inviteToRoom_4c20)
        self.addHandler(0x4E00, self.setPresetPhrase_4e00)
        self.addHandler(0x6020, self.quickGameSearch_6020)
        self.addHandler(0x4110, self.setComment_4110)
        self.addHandler(0x4345, self.getStunInfo_4345)
        self.addHandler(0x4400, self.chat_4400)
        self.addHandler(0x4310, self.createRoom_4310)
        self.addHandler(0x4300, self.getRoomList_4300)
        self.addHandler(0x4320, self.joinRoom_4320)
        self.addHandler(0x4363, self.toggleParticipate_4363)
        self.addHandler(0x4360, self.startMatch_4360)
        self.addHandler(0x436F, self.toggleReady_436f)
        self.addHandler(0x4369, self.setPlayerSettings_4369)
        self.addHandler(0x436C, self.setGameSettings_436c)
        self.addHandler(0x4373, self.teamSelected_4373)
        self.addHandler(0x4375, self.goalScored_4375)
        self.addHandler(0x4377, self.matchStateUpdate_4377)
        self.addHandler(0x4385, self.matchClockUpdate_4385)
        self.addHandler(0x4349, self.setOwner_4349)
        self.addHandler(0x434D, self.setRoomName_434d)
        self.addHandler(0x4366, self.becomeSpectator_4366)
        self.addHandler(0x4351, self.do_4351)
        self.addHandler(0x4383, self.backToMatchMenu_4383)
        self.addHandler(0x4380, self.forcedCancelParticipation_4380)
        self.addHandler(0x4C00, self.do_4c00)
