"""Registration parity, deterministic discovery and strict request decoding."""

import asyncio
import hashlib
import importlib
import json
import struct
from itertools import permutations
from types import SimpleNamespace

import pytest

from fiveserver.games.definitions import discover_handlers, make_registry
from fiveserver.database import Profile, ProfileStats
from fiveserver.protocol.dispatcher import (
    ALL_GAMES, FIVE_GAMES, SIX_GAMES, GameDefinition, GameId, PacketRegistry,
    ServiceRole, packet_handler,
)
from fiveserver.protocol.packets import ProfileIdRequest
from fiveserver.protocol.wire import Packet, PacketHeader


def handler(*, games=ALL_GAMES, override=False, role=ServiceRole.MAIN, **kwargs):
    @packet_handler(0x1234, games=games, role=role, override=override, **kwargs)
    async def receive(ctx, request):
        ctx.received = request

    return receive


def test_all_502_effective_registrations_match_pre_refactor_snapshot():
    registry = make_registry(ALL_GAMES)
    # Only these reviewed forwarding functions were removed. Check their new
    # targets explicitly, then restore their historical names for comparison
    # with the original sealed fingerprint (all keys and other winners intact).
    redirects = {
        (ServiceRole.LOGIN, 0x3010): ("get_profiles", "get_profiles_pes6"),
        (ServiceRole.MAIN, 0x4100): ("select_menu_profile", "select_pes6_main_profile"),
        (ServiceRole.MAIN, 0x4102): ("get_menu_profile", "get_pes6_main_profile"),
        (ServiceRole.MAIN, 0x4202): ("select_lobby", "select_lobby_pes6_main"),
        (ServiceRole.MAIN, 0x4210): ("user_list", "user_list_pes6_main"),
        (ServiceRole.MAIN, 0x4300): ("room_list", "room_list_pes6"),
        (ServiceRole.MAIN, 0x4310): ("create_room", "create_room_pes6"),
        (ServiceRole.MAIN, 0x432A): ("exit_room_pes5", "exit_room_pes6"),
        (ServiceRole.NEWS, 0x2005): ("server_list", "pes6_server_list"),
    }
    inventory = []
    for (game, role, packet_id), (fn, _) in sorted(registry._handlers.items()):
        name = fn.__name__
        if game in SIX_GAMES and (role, packet_id) in redirects:
            current, historical = redirects[(role, packet_id)]
            assert name == current
            name = historical
        inventory.append([str(game), str(role), packet_id, fn.__module__ + "." + name])
    assert len(inventory) == 502
    # Captured before removing the manual inventory; includes all game/role keys
    # and the identity of the winning function, not just packet counts.
    assert hashlib.sha256(json.dumps(inventory, separators=(",", ":")).encode()).hexdigest() == (
        "853ccb73e004f125c012a2fd4557db283e4ec6b386cf6c1d06d4951c01fe076b"
    )
    assert len(discover_handlers()) == 72
    assert discover_handlers() is discover_handlers()
    assert make_registry(ALL_GAMES)._handlers == registry._handlers
    with pytest.raises(TypeError):
        registry._handlers[(GameId.PES5, ServiceRole.MAIN, 0)] = (None, None)


def test_new_handler_module_is_discovered_without_central_inventory(tmp_path, monkeypatch):
    package = tmp_path / "synthetic_handlers"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "added.py").write_text(
        "from fiveserver.protocol.dispatcher import *\n"
        "@packet_handler(0x1234, games=FIVE_GAMES, role=ServiceRole.MAIN)\n"
        "async def added(ctx, request):\n    ctx.received = request\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    found = discover_handlers("synthetic_handlers")
    registry = PacketRegistry.from_handlers(ALL_GAMES, found)
    for game in FIVE_GAMES:
        assert registry.registered(game, ServiceRole.MAIN) == {0x1234}
    for game in SIX_GAMES:
        assert registry.registered(game, ServiceRole.MAIN) == set()
    assert registry.registered(GameId.PES5, ServiceRole.LOGIN) == set()


def test_family_and_individual_overrides_are_import_order_independent():
    base = handler()
    family = handler(games=SIX_GAMES, override=True)
    individual = handler(games={GameId.WE2007}, override=True)
    for order in permutations((individual, base, family)):
        registry = PacketRegistry.from_handlers(ALL_GAMES, order)
        assert registry._handlers[(GameId.PES5, ServiceRole.MAIN, 0x1234)][0] is base
        assert registry._handlers[(GameId.PES6, ServiceRole.MAIN, 0x1234)][0] is family
        assert registry._handlers[(GameId.WE2007, ServiceRole.MAIN, 0x1234)][0] is individual


def test_duplicate_missing_base_and_competing_overrides_are_rejected():
    base = handler()
    override = handler(games=SIX_GAMES, override=True)
    for functions, message in [
        ((base, base), "Duplicate"),
        ((override,), "no base"),
        ((base, override, handler(games=SIX_GAMES, override=True)), "Competing"),
        ((base, handler(games={GameId.PES6, GameId.PES5}, override=True),
          handler(games={GameId.PES6, GameId.WE9}, override=True)), "Competing"),
    ]:
        with pytest.raises(ValueError, match=message):
            PacketRegistry.from_handlers(ALL_GAMES, functions)
    with pytest.raises(ValueError, match="not declared"):
        PacketRegistry((GameDefinition(GameId.PES5, (handler(games=SIX_GAMES),)),))


@pytest.mark.parametrize("kwargs", [
    {"games": frozenset()}, {"games": {"pes5"}}, {"role": "main"},
    {"role": ()}, {"role": (ServiceRole.MAIN, ServiceRole.MAIN)},
    {"decoder": 42}, {"decoder": lambda a, b: a}, {"request_type": int},
    {"request_type": ProfileIdRequest, "decoder": bytes},
])
def test_invalid_declarations_fail_before_dispatch(kwargs):
    with pytest.raises((ValueError, TypeError)):
        handler(**kwargs)


def test_missing_game_declaration_and_async_decoder_are_rejected():
    with pytest.raises(TypeError):
        packet_handler(1, role=ServiceRole.MAIN)

    async def decode(payload):
        return payload

    with pytest.raises(ValueError, match="synchronous"):
        handler(decoder=decode)


def test_game_ids_remain_yaml_strings_and_groups_are_immutable():
    assert str(GameId.WE2007) == "we2007"
    assert isinstance(GameId.PES5, str)
    assert isinstance(ALL_GAMES, frozenset)
    assert FIVE_GAMES.isdisjoint(SIX_GAMES)
    assert ALL_GAMES == frozenset(GameId)


def test_an_override_cannot_replace_a_different_service_role():
    with pytest.raises(ValueError, match="no base"):
        PacketRegistry.from_handlers(ALL_GAMES, (
            handler(role=ServiceRole.LOGIN), handler(games=SIX_GAMES, override=True),
        ))


def test_typed_request_decodes_once_and_rejects_short_and_extra_bytes():
    fn = handler(request_type=ProfileIdRequest)
    registry = PacketRegistry.from_handlers(FIVE_GAMES, (fn,))
    ctx = SimpleNamespace(game=GameId.PES5, role=ServiceRole.MAIN)

    async def scenario():
        await registry.dispatch(ctx, Packet(PacketHeader(0x1234, 4, 19), b"\xff\xff\xff\xfc"))
        assert isinstance(ctx.received, ProfileIdRequest)
        assert ctx.received.profile_id == -4
        del ctx.received
        for payload in (b"", bytes(3), bytes(5)):
            with pytest.raises(ValueError, match="requires 4 bytes"):
                await registry.dispatch(ctx, Packet(PacketHeader(0x1234, len(payload), 20), payload))
            assert not hasattr(ctx, "received")

    asyncio.run(scenario())


def test_actual_profile_handlers_receive_models_and_keep_family_response_layouts():
    class Repository:
        async def profile_by_id(self, profile_id):
            assert profile_id == -4
            return Profile(0, 1, profile_id, "Synthetic")

        async def profile_stats(self, profile_id):
            return ProfileStats()

    async def scenario():
        registry = make_registry(ALL_GAMES)
        for game, size in ((GameId.PES5, 57), (GameId.PES6, 418)):
            replies = []

            async def reply(packet_id, payload=b""):
                replies.append((packet_id, payload))

            ctx = SimpleNamespace(game=game, role=ServiceRole.MAIN, repository=Repository(),
                                  settings={"ShowStats": True}, reply=reply)
            await registry.dispatch(ctx, Packet(PacketHeader(0x4102, 4, 1), struct.pack("!i", -4)))
            assert len(replies) == 1 and replies[0][0] == 0x4103
            assert len(replies[0][1]) == 4 + size
            assert replies[0][1][:8] == bytes(4) + b"\xff\xff\xff\xfc"

    asyncio.run(scenario())


def test_custom_struct_decoder_errors_enter_existing_value_error_path():
    def malformed(payload):
        return struct.unpack("!i", payload)

    registry = PacketRegistry.from_handlers(FIVE_GAMES, (handler(decoder=malformed),))
    ctx = SimpleNamespace(game=GameId.PES5, role=ServiceRole.MAIN)
    with pytest.raises(ValueError, match="Malformed packet"):
        asyncio.run(registry.dispatch(ctx, Packet(PacketHeader(0x1234, 0, 1), b"")))
    assert not hasattr(ctx, "received")


def test_context_only_handler_is_adapted_at_startup(monkeypatch):
    @packet_handler(0x1234, games=FIVE_GAMES, role=ServiceRole.MAIN)
    async def receive(ctx):
        ctx.received = True

    registry = PacketRegistry.from_handlers(FIVE_GAMES, (receive,))

    def unexpected_inspection(*args, **kwargs):
        raise AssertionError("Dispatch must not inspect signatures")

    monkeypatch.setattr("fiveserver.protocol.dispatcher.inspect.signature", unexpected_inspection)
    ctx = SimpleNamespace(game=GameId.PES5, role=ServiceRole.MAIN)
    asyncio.run(registry.dispatch(ctx, Packet(PacketHeader(0x1234, 0, 1), b"")))
    assert ctx.received is True
