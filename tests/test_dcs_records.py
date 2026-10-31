"""Independent legacy layout formulas, not sizes derived from DCS models."""

import struct
from types import SimpleNamespace

import dataclasses_struct as dcs
import pytest

from fiveserver.database import Profile, ProfileStats
from fiveserver.handlers.profile_info import encode_pes5_profile, encode_pes6_profile, format_profile_info
from fiveserver.handlers.room_codecs import _peer_info, participation_status, encode_pes5_room, encode_pes6_room
from fiveserver.handlers.rooms import Room
from fiveserver.protocol import records as wire
from fiveserver.protocol.packets import ProfileIdRequest, TeamSelectRequest


# Sizes independently summed from the original struct fields in
# lib/fiveserver/protocol/pes5.py and pes6.py (Git HEAD).
SIZES = [
    (wire.ResultsRecord, 12), (wire.Pes5ProfileRecord, 57), (wire.Pes6ProfileRecord, 418),
    (wire.RoomPlayerRecord, 10), (wire.TeamGoalsRecord, 7), (wire.RoomTeamsRecord, 14),
    (wire.Pes6RoomRecord, 131), (wire.Pes5RoomRecord, 87),
    (wire.ParticipationRecord, 6), (wire.ParticipationArray, 24), (wire.PeerRecord, 43),
    (wire.PlayerSettingRecord, 8), (wire.PlayerSettingsRecord, 32), (wire.ChatHeader, 10),
    (wire.LoginProfileState, 9), (wire.Pes5LoginProfile, 32), (wire.Pes6LoginProfile, 66),
    (wire.Pes5PlayerRecord, 31), (wire.Pes6PlayerRecord, 127),
    (ProfileIdRequest, 4), (TeamSelectRequest, 2),
    (wire.Pes5RoomCreateHeader, 34), (wire.Pes6RoomCreateHeader, 65),
    (wire.ReadyUpdateRecord, 5), (wire.KickoffRecord, 37), (wire.PostMatchPlayerRecord, 22),
    (wire.PingRecord, 44), (wire.Pes5ChatPrefix, 25), (wire.Pes6ChatPrefix, 58),
    (wire.NewsHeaderRecord, 89),
    (wire.Pes5RoomPeerNames, 48), (wire.Pes5ChallengeRecord, 90), (wire.Pes6RoomRenameHeader, 65),
]


@pytest.mark.parametrize("model,size", SIZES)
def test_exact_sizes_reserved_bytes_round_trip_and_strict_unpacking(model, size):
    assert dcs.get_struct_size(model) == size
    original = bytes(i % 256 for i in range(size))
    assert model.from_packed(original).pack() == original
    for malformed in (original[:-1], original + b"\0"):
        with pytest.raises((ValueError, struct.error)):
            model.from_packed(malformed)


def pad(value, size):
    value = value.encode("utf-8", "replace") if isinstance(value, str) else value
    return value[:size].ljust(size, b"\0")


def legacy_profile(profile, stats, pes6, show_stats):
    if not show_stats:
        profile = Profile(profile.index, profile.user_id, profile.id, profile.name)
    results = struct.pack("!6H", stats.wins, stats.losses, stats.draws,
                          stats.streak_current, stats.streak_best, profile.disconnects)
    division = next((i for i, limit in enumerate((250, 450, 600, 750)) if profile.points < limit), 4)
    games = stats.wins + stats.losses + stats.draws
    if not pes6:
        return (struct.pack("!i16sBiH", profile.id, pad(profile.name, 16), division, profile.points, games)
                + results + bytes(2) + struct.pack("!H", stats.goals_scored) + bytes(2)
                + struct.pack("!HHii", stats.goals_allowed, profile.fav_team, profile.fav_player, profile.rank))
    return (struct.pack("!i48si48sBBiHH", profile.id, pad(profile.name, 48), 0, pad("Playmakers", 48),
                        1, division, profile.points, profile.rating, games)
            + results + struct.pack("!ii", stats.goals_scored, stats.goals_allowed)
            + pad(profile.comment or "Fiveserver rules!", 256) + struct.pack("!i", profile.rank)
            + bytes(14) + b"".join(struct.pack("!H", t) for t in stats.teams)
            + b"\xff\xff" * (5 - len(stats.teams)))


@pytest.mark.parametrize("pes6", [False, True])
@pytest.mark.parametrize("show_stats", [False, True])
@pytest.mark.parametrize("teams", [(), (1, 0x1234), (1, 2, 3, 4, 65535)])
def test_profile_bytes_match_legacy_offsets_padding_and_signedness(pes6, show_stats, teams):
    profile = Profile(0, 1, -7, "é" * 50, rank=-2, points=530, rating=0x1234,
                      disconnects=2, fav_team=0x4567, fav_player=-11, comment=b"x" * 300)
    stats = ProfileStats(8, 2, 3, 27, 14, 4, 6, teams)
    packed = format_profile_info(profile, stats, encoder=(encode_pes5_profile, encode_pes6_profile)[pes6], show_stats=show_stats)
    assert packed == legacy_profile(profile, stats, pes6, show_stats)
    assert len(packed) == (418 if pes6 else 57)
    assert packed[:4] == b"\xff\xff\xff\xf9"
    if pes6:
        assert packed[114:126] == struct.pack("!6H", 8, 2, 3, 4, 6, 2 if show_stats else 0)
        assert packed[408:] == b"".join(struct.pack("!H", t) for t in teams) + b"\xff\xff" * (5-len(teams))
        assert packed[394:408] == bytes(14)


@pytest.mark.parametrize("count", range(5))
def test_pes6_room_matches_legacy_nested_offsets_and_empty_sentinels(count):
    players = [SimpleNamespace(selected_profile=Profile(0, 1, 100 + i), spectator=i % 2)
               for i in range(count)]
    owner = players[0] if players else None
    room = Room(-8, b"room" * 20, owner, locked=True, players=players, phase=4,
                participating_players=players[:2], match_starter=owner)
    packed = encode_pes6_room(room)
    entries = b"".join(struct.pack("!i6B", 100+i, int(i == 0), int(i == 0), 255,
                                   i % 2, i, i if i < 2 else 255) for i in range(count))
    expected = (struct.pack("!iBB64sB", -8, 4, 0, room.name[:64], 0) + entries
                + b"\0\0\0\0\0\0\xff\0\0\xff" * (4-count)
                + b"\xff\xff" + bytes(5) + b"\xff\xff" + bytes(5) + b"\0\1\0\2\0\0")
    assert packed == expected and len(packed) == 131
    assert packed[6:70] == room.name[:64]
    assert packed[125:] == b"\0\1\0\2\0\0"
    participation = (b"".join(struct.pack("!iBB", 100+i, i, i if i < 2 else 255) for i in range(count))
                     + b"\0\0\0\0\0\xff" * (4-count))
    assert participation_status(room) == participation


def test_pes6_team_goals_and_peer_records_match_original_offsets():
    peer = SimpleNamespace(selected_profile=Profile(0, 1, -3), spectator=1,
                           ip1=b"192.0.2.1", ip2=b"198.51.100.2", udp_port1=0x1234, udp_port2=0xabcd)
    selection = SimpleNamespace(home_team_id=0x1234, away_team_id=None, side=lambda pid: 1)
    match = SimpleNamespace(state=3, clock=42, home_goals=[1, 2, 3, 4, 5], away_goals=[5, 4, 3, 2, 1])
    room = Room(7, b"a", peer, players=[peer], participating_players=[peer], team_selection=selection, match6=match)
    packed = encode_pes6_room(room)
    assert packed[111:125] == struct.pack("!H5BH5B", 0x1234, 1, 2, 3, 4, 5, 65535, 5, 4, 3, 2, 1)
    for incoming in (False, True):
        expected = bytes(36 if incoming else 32) + struct.pack(
            "!16sH16sHiHB", peer.ip1, peer.udp_port1, peer.ip2, peer.udp_port2, -3, 0, 0,
        )
        assert _peer_info(peer, room, incoming=incoming) == expected


@pytest.mark.parametrize("update", [False, True])
def test_pes5_room_list_and_update_strides_remain_different(update):
    peer = SimpleNamespace(selected_profile=Profile(0, 1, 0x12345678))
    room = Room(9, b"short", peer, players=[peer], match_time=15)
    entries = struct.pack("!i", 0x12345678) + (bytes(7) if update else b"")
    expected = struct.pack("!iBB32sB", 9, 1, 0, b"short", 3) + entries + bytes(48-len(entries))
    assert encode_pes5_room(room, update=update) == expected


def test_fixed_bytes_are_padded_or_truncated_not_null_terminated():
    for name in (b"a", b"x" * 80, b"a\0b"):
        record = wire.Pes5RoomRecord(1, 1, 0, name, 1, b"")
        assert record.pack()[6:38] == name[:32].ljust(32, b"\0")


@pytest.mark.parametrize("model,size,fields", [
    (wire.ResultsRecord, 12, [("streak_best", 8, "H"), ("disconnects", 10, "H")]),
    (wire.Pes5ProfileRecord, 57, [("points", 21, "i"), ("favourite_player", 49, "i"), ("rank", 53, "i")]),
    (wire.Pes6ProfileRecord, 418, [("group_id", 52, "i"), ("points", 106, "i"), ("rank", 390, "i")]),
    (wire.RoomPlayerRecord, 10, [("profile_id", 0, "i"), ("side", 6, "B"), ("participation", 9, "B")]),
    (wire.TeamGoalsRecord, 7, [("team_id", 0, "H")]),
    (wire.Pes6RoomRecord, 131, [("clock", 70, "B"), ("locked", 126, "B")]),
    (wire.Pes5RoomRecord, 87, [("room_id", 0, "i"), ("match_time_slot", 38, "B")]),
    (wire.ParticipationRecord, 6, [("profile_id", 0, "i"), ("participation", 5, "B")]),
    (wire.PeerRecord, 43, [("port1", 16, "H"), ("port2", 34, "H"), ("profile_id", 36, "i"), ("participation", 42, "B")]),
    (wire.PlayerSettingRecord, 8, [("profile_id", 0, "i"), ("away", 4, "B")]),
    (wire.ChatHeader, 10, [("recipient_id", 6, "i")]),
    (wire.Pes5RoomCreateHeader, 34, [("reserved", 32, "B"), ("locked", 33, "B")]),
    (wire.Pes6RoomCreateHeader, 65, [("locked", 64, "B")]),
    (wire.LoginProfileState, 9, [("seconds_played", 0, "i"), ("division", 4, "B"), ("points", 5, "i")]),
    (wire.Pes5LoginProfile, 32, [("index", 0, "B"), ("profile_id", 1, "i"), ("games", 30, "H")]),
    (wire.Pes6LoginProfile, 66, [("profile_id", 1, "i"), ("rating", 62, "H"), ("games", 64, "H")]),
    (wire.Pes5PlayerRecord, 31, [("in_room", 20, "B"), ("room_id", 21, "i"), ("no_lobby_chat", 25, "i")]),
    (wire.Pes6PlayerRecord, 127, [("division", 105, "B"), ("room_id", 106, "i"), ("points", 110, "i"), ("games", 116, "H")]),
    (wire.ReadyUpdateRecord, 5, [("profile_id", 0, "i"), ("state", 4, "B")]),
    (wire.KickoffRecord, 37, [("phase", 0, "B")]),
    (wire.PostMatchPlayerRecord, 22, [("profile_id", 0, "i")]),
    (wire.PingRecord, 44, [("status", 0, "I"), ("port1", 20, "H"), ("port2", 38, "H"), ("profile_id", 40, "i")]),
    (wire.Pes5ChatPrefix, 25, [("profile_id", 5, "i")]),
    (wire.Pes6ChatPrefix, 58, [("profile_id", 6, "i")]),
    (wire.NewsHeaderRecord, 89, [("status", 0, "I")]),
    (ProfileIdRequest, 4, [("profile_id", 0, "i")]),
    (TeamSelectRequest, 2, [("team_id", 0, "H")]),
    (wire.Pes6RoomRenameHeader, 65, [("reserved", 63, "B"), ("locked", 64, "B")]),
])
def test_decoded_scalar_offsets_endianness_and_signedness(model, size, fields):
    payload = bytes((128 + i) % 256 for i in range(size))
    record = model.from_packed(payload)
    for name, offset, format_code in fields:
        assert getattr(record, name) == struct.unpack_from("!" + format_code, payload, offset)[0]


def test_nested_arrays_decode_at_independent_legacy_strides():
    payload = bytes(range(131))
    room = wire.Pes6RoomRecord.from_packed(payload)
    for index, player in enumerate(room.players):
        assert player.profile_id == struct.unpack_from("!i", payload, 71 + index * 10)[0]
    assert room.teams.home.team_id == struct.unpack_from("!H", payload, 111)[0]
    assert room.teams.away.goals == list(payload[120:125])
    participation = wire.ParticipationArray.from_packed(bytes(range(24)))
    assert [p.position for p in participation.players] == [4, 10, 16, 22]
    settings = wire.PlayerSettingsRecord.from_packed(bytes(range(32)))
    assert [p.reserved for p in settings.players] == [bytes(range(i+5, i+8)) for i in (0, 8, 16, 24)]


def test_names_challenge_and_rename_preserve_the_legacy_boundaries():
    assert wire.Pes5RoomPeerNames(b"p", b"r").pack() == struct.pack("!16s32s", b"p", b"r")
    profile = bytes(range(57))
    challenge = wire.Pes5ChallengeRecord(profile, b"\x80", bytes(2)).pack()
    assert challenge == profile + bytes(30) + b"\x80\0\0"
    header = wire.Pes6RoomRenameHeader.from_packed(b"x" * 63 + b"\x7f\x01")
    assert header.name == b"x" * 63 and header.reserved == 127 and header.locked == 1
