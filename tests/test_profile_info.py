import struct

from fiveserver.database import Profile, ProfileStats
from fiveserver.handlers.profile_info import encode_pes5_profile, encode_pes6_profile, format_profile_info


def test_profile_info_wire_sizes_and_fields():
    profile = Profile(0, 1, 42, "Player", 3, 99, 450, 2)
    profile.fav_team = 10
    profile.fav_player = 11
    profile.comment = "Hello"
    stats = ProfileStats(4, 3, 2, 12, 8, 1, 5, (100, 200))

    five = format_profile_info(profile, stats, encoder=encode_pes5_profile, show_stats=True)
    assert len(five) == 57
    assert five[:4] == struct.pack("!i", 42)
    assert five[20:25] == struct.pack("!Bi", 2, 450)
    assert five[-12:] == struct.pack("!HHii", 8, 10, 11, 3)

    six = format_profile_info(profile, stats, encoder=encode_pes6_profile, show_stats=True)
    assert len(six) == 418
    assert six[:4] == struct.pack("!i", 42)
    assert six[52:56] == bytes(4)
    assert six[134:139] == b"Hello"
    assert six[-10:] == struct.pack("!HHHHH", 100, 200, 0xFFFF, 0xFFFF, 0xFFFF)


