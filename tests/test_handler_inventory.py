import json
from collections import defaultdict
from pathlib import Path

from fiveserver.protocol.dispatcher import GameId, ServiceRole
from fiveserver.games.definitions import make_registry

ROLES = {
    "NewsProtocol": (ServiceRole.NEWS,),
    "LoginService": (ServiceRole.LOGIN, ServiceRole.NETWORK_MENU, ServiceRole.MAIN),
    "NetworkMenuService": (ServiceRole.NETWORK_MENU, ServiceRole.MAIN),
    "MainService": (ServiceRole.MAIN,),
}


def inventory_rows():
    root = Path(__file__).resolve().parent
    return json.loads((root / "fixtures/expected_handlers.json").read_text(encoding="utf-8"))


def test_every_original_packet_has_a_live_registration():
    expected = defaultdict(set)
    for row in inventory_rows():
        game = GameId.PES5 if row["source"] == "pes5.py" else GameId.PES6
        for role in ROLES[row["class"]]:
            expected[(game, role)].add(int(row["packet_id"], 16))
    registry = make_registry((GameId.PES5, GameId.PES6))
    for key, packet_ids in expected.items():
        assert packet_ids <= registry.registered(*key)


def test_original_registrations_include_role_collisions():
    root = Path(__file__).resolve().parent
    rows = json.loads((root / "fixtures/expected_handlers.json").read_text(encoding="utf-8"))
    assert any(row["packet_id"] == "0x3003" and row["handler"] == "authenticate_3003" for row in rows)
    assert any(row["packet_id"] == "0x4310" and row["class"] == "MainService" for row in rows)
    assert any(row["source"] == "pes5.py" and row["packet_id"] == "0x4310" for row in rows)
    assert any(row["source"] == "pes6.py" and row["packet_id"] == "0x4310" for row in rows)
    assert any(row["packet_id"] == "0x2200" and row["class"] == "NewsProtocol" for row in rows)


