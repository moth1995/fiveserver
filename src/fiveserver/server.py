"""Ordered asyncio TCP listener for the FiveServer packet protocol."""


import argparse
import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any, Mapping

import uvicorn

from .config import load_settings
from .database import MySQLDatabase, Profile, RankingRepository, User, UserRepository
from .games.definitions import FIVE_GAMES, SIX_GAMES, make_registry
from .handlers.chat import send_history
from .handlers.lobbies import Lobby, configured_lobbies, leave_lobby
from .handlers.news import VERSION
from .network import resolve_server_ip
from .logging_setup import (
    close_logging, configure_logging, flush_logging, logging_context, log_incoming_packet, log_outgoing_packet,
)
from .protocol.dispatcher import GameId, PacketRegistry, ServiceRole
from .protocol.formats import ChatPrefixEncoder, WireFormats, active_formats
from .protocol.wire import HEARTBEAT_ID, InvalidPacket, Packet, encode_packet, read_packet
from .scheduling import Scheduler
from .web.admin import AdminRepository, DEFAULT_ADMIN_PASSWORD, create_admin_app
from .web.registration import RegistrationRepository, create_registration_app, parse_banned_networks


LOGGER = logging.getLogger(__name__)
READ_TIMEOUT_SECONDS = 180
CHAT_HISTORY_DELAY_SECONDS = 3
CLOSE_TIMEOUT_SECONDS = 2


@dataclass
class Connection:
    """Per-client asyncio connection, session, lobby, room, and match state."""
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    game: GameId
    role: ServiceRole
    settings: dict[str, Any]
    server_ip: str
    repository: UserRepository | None = None
    sessions: dict[str, object] = field(default_factory=dict)
    session_lock: asyncio.Lock | None = None
    user_info: dict[str, tuple[GameId, bytes | None]] = field(default_factory=dict)
    user: User | None = None
    selected_profile: Profile | None = None
    banned: bool = False
    lobbies: list[Lobby] = field(default_factory=list)
    lobby_id: int | None = None
    room_id: int = 0
    spectator: bool = False
    cancelled_until: datetime | None = None
    no_lobby_chat: int = 0
    needs_lobby_chat_replay: bool = False
    ip1: bytes = b""
    ip2: bytes = b""
    udp_port1: int = 0
    udp_port2: int = 0
    lobby_unknown: int = 0
    online_count: int = 0
    counter: int = 1
    session_id: int = 0
    closed: bool = False
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    background_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    _chat_history_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _chat_history_lobby: Lobby | None = field(default=None, init=False, repr=False)

    @property
    def formats(self) -> WireFormats:
        """Return formats for the active identity, including recovered variants."""
        return active_formats(self)

    @property
    def local_port(self) -> int:
        """Return the local listener port."""
        return int(self.writer.get_extra_info("sockname")[1])

    @property
    def peer_ip(self) -> str | None:
        """Return the connected peer IP if available."""
        peer = self.writer.get_extra_info("peername")
        return str(peer[0]) if peer else None

    @property
    def peer_address(self) -> str | None:
        """Return the peer ``host:port`` used by historical packet logs."""
        peer = self.writer.get_extra_info("peername")
        if not peer:
            return None
        host = str(peer[0])
        return f"[{host}]:{peer[1]}" if ":" in host else f"{host}:{peer[1]}"

    async def reply(self, packet_id: int, payload: bytes = b"") -> None:
        """Serialize and send a response under the connection write lock."""
        async with self.write_lock:
            if self.closed:
                return
            counter = self.counter
            log_outgoing_packet(self, packet_id, payload, counter)
            self.writer.write(encode_packet(packet_id, payload, counter))
            self.counter += 1
            await self.writer.drain()

    async def disconnect(self) -> None:
        """Cancel background work, close the writer, and suppress close races."""
        if not self.closed:
            self.closed = True
            for task in tuple(self.background_tasks):
                task.cancel()
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
            self.writer.close()
            try:
                await asyncio.wait_for(self.writer.wait_closed(), timeout=CLOSE_TIMEOUT_SECONDS)
            except TimeoutError:
                self.writer.transport.abort()
            except (ConnectionError, OSError):
                pass

    def schedule_chat_history(self, lobby: Lobby, *, encoder: ChatPrefixEncoder | None = None) -> None:
        """Schedule delayed chat replay, coalescing triggers for the same lobby."""
        self.needs_lobby_chat_replay = False
        pending = self._chat_history_task
        if pending is not None and not pending.done():
            if self._chat_history_lobby is lobby:
                return  # A replay is already queued; one replay is enough.
            pending.cancel()

        async def delayed() -> None:
            await asyncio.sleep(CHAT_HISTORY_DELAY_SECONDS)
            if not self.closed and self.lobby_id is not None and self.lobbies[self.lobby_id] is lobby:
                try:
                    await send_history(self, lobby, encoder=encoder)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOGGER.exception("Failed to replay lobby chat history")

        task = asyncio.create_task(delayed())
        self._chat_history_task = task
        self._chat_history_lobby = lobby
        self.background_tasks.add(task)

        def finished(done: asyncio.Task[None]) -> None:
            self.background_tasks.discard(done)
            if self._chat_history_task is done:
                self._chat_history_task = None
                self._chat_history_lobby = None

        task.add_done_callback(finished)


@dataclass
class FiveServer:
    """Asyncio server owning listeners, sessions, lobbies, and the packet registry."""
    settings: dict[str, Any]
    games: tuple[GameId, ...]
    server_ip: str
    repository: UserRepository | None = None
    banned_networks: tuple[IPv4Network, ...] = ()
    started_at: datetime = field(default_factory=datetime.now)
    registry: PacketRegistry = field(init=False)
    listeners: list[asyncio.AbstractServer] = field(default_factory=list)
    sessions: dict[str, object] = field(default_factory=dict)
    session_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_session_id: int = 1
    user_info: dict[str, tuple[GameId, bytes | None]] = field(default_factory=dict)
    connections: dict[int, Connection] = field(default_factory=dict)
    lobbies: list[Lobby] = field(init=False)
    protocol_formats: Mapping[GameId, Mapping[ServiceRole, WireFormats]] | None = None

    def __post_init__(self) -> None:
        self.registry = make_registry(self.games, formats=self.protocol_formats)
        self.lobbies = configured_lobbies(self.settings)

    def _ports(self) -> list[tuple[int, GameId, ServiceRole]]:
        game_ports: dict[str, int] = self.settings["GamePorts"]
        network: dict[str, Any] = self.settings["NetworkServer"]
        ports = [(int(port), GameId(name), ServiceRole.NEWS) for name, port in game_ports.items()]
        family_game = self.games[0]
        ports.extend(
            [
                (int(network["networkMenuService"]), family_game, ServiceRole.NETWORK_MENU),
                (int(network["mainService"]), family_game, ServiceRole.MAIN),
            ]
        )
        login_ports: dict[str, int] = network["loginService"]
        ports.extend((int(port), GameId(game), ServiceRole.LOGIN) for game, port in login_ports.items())
        return ports

    async def start(self) -> None:
        """Bind every configured news, login, menu, and main TCP listener."""
        host = str(self.settings.get("ListenOn", ""))
        try:
            for port, game, role in self._ports():
                listener = await asyncio.start_server(
                    partial(self.handle_client, game=game, role=role),
                    host=host,
                    port=port,
                )
                self.listeners.append(listener)
                LOGGER.info("Listening on %s:%s for %s %s", host or "0.0.0.0", port, game, role)
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        """Close listeners and disconnect all active clients."""
        listeners, self.listeners = self.listeners, []
        for listener in listeners:
            listener.close()
        # On Python 3.12+, Server.wait_closed() also waits for every active
        # connection to drop. Disconnect clients first or shutdown deadlocks
        # until the peers leave on their own.
        await asyncio.gather(
            *(connection.disconnect() for connection in tuple(self.connections.values())),
            return_exceptions=True,
        )
        if listeners:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(listener.wait_closed() for listener in listeners)),
                    timeout=5,
                )
            except TimeoutError:
                LOGGER.warning("Timed out waiting for TCP listeners to close.")

    def _is_banned(self, writer: asyncio.StreamWriter) -> bool:
        peer = writer.get_extra_info("peername")
        if not peer:
            return False
        try:
            return any(IPv4Address(peer[0]) in network for network in self.banned_networks)
        except ValueError:
            return False

    def _admitted(self, context: Connection) -> bool:
        if context.role == ServiceRole.NEWS:
            return True
        max_users = int(self.settings.get("MaxUsers", 1000))
        return not context.banned and len(self.sessions) < max_users

    async def _serve_packets(self, context: Connection) -> None:
        while not context.closed:
            packet: Packet = await asyncio.wait_for(read_packet(context.reader), timeout=READ_TIMEOUT_SECONDS)
            log_incoming_packet(
                context, packet.header.packet_id, packet.payload, packet.header.counter,
            )
            if packet.header.packet_id == HEARTBEAT_ID:
                await context.reply(HEARTBEAT_ID, packet.payload)
                continue
            context.online_count = len(self.sessions)
            await self.registry.dispatch(context, packet)

    async def _cleanup(self, context: Connection) -> None:
        try:
            await leave_lobby(context, disconnected=True)  # type: ignore[arg-type]
        except Exception:
            LOGGER.exception("Failed to remove disconnected player from lobby")
        finally:
            if context.user is not None and self.sessions.get(context.user.hash) is context:
                del self.sessions[context.user.hash]
            await context.disconnect()
            self.connections.pop(id(context), None)

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, game: GameId, role: ServiceRole
    ) -> None:
        """Create connection state and run admission, packet service, and cleanup."""
        context = Connection(
            reader, writer, game, role, self.settings, self.server_ip,
            self.repository, self.sessions, session_lock=self.session_lock,
            session_id=self.next_session_id,
        )
        self.next_session_id += 1
        context.user_info = self.user_info
        context.lobbies = self.lobbies
        context.banned = self._is_banned(writer)
        self.connections[id(context)] = context
        with logging_context(context):
            LOGGER.info("Connection established from %s for %s.", context.peer_address, game)
            reason = "Connection ended."
            try:
                if self._admitted(context):
                    await self._serve_packets(context)
                    reason = "Connection was closed cleanly."
                else:
                    reason = "Connection rejected."
                    if context.banned:
                        LOGGER.warning("NOTICE: disconnecting banned IP (%s).", context.peer_ip)
                    else:
                        LOGGER.warning("Connection rejected because the server is full.")
            except asyncio.IncompleteReadError:
                reason = "Connection was closed cleanly."
            except ConnectionError as exc:
                reason = str(exc) or type(exc).__name__
                LOGGER.warning("Connection error: %s", reason)
            except (InvalidPacket, LookupError, ValueError, asyncio.TimeoutError) as exc:
                reason = str(exc) or type(exc).__name__
                LOGGER.warning("Closing connection: %s", reason)
            except Exception as exc:  # Keep dependency failures in the central format.
                reason = str(exc) or type(exc).__name__
                LOGGER.exception("Unhandled connection error")
            finally:
                try:
                    await self._cleanup(context)
                finally:
                    LOGGER.info("Connection lost: %s", reason)


def _load_banned(settings: dict[str, Any]) -> tuple[IPv4Network, ...]:
    banned_file = Path(str(settings.get("BannedList", "")))
    if not banned_file.is_file():
        LOGGER.info("NOTICE: banned-list file absent.")
        return ()
    return parse_banned_networks(load_settings(banned_file).get("Banned", []))


def _web_servers(
    settings: dict[str, Any], six: bool, server: FiveServer,
    database: MySQLDatabase, user_repository: UserRepository,
    admin_settings: dict[str, Any],
) -> tuple[uvicorn.Server, uvicorn.Server, uvicorn.Server]:
    banned = server.banned_networks
    web_dir = Path("web6" if six else "web")
    app = create_registration_app(RegistrationRepository(database), web_dir, banned)
    admin_repository = AdminRepository(database, user_repository)
    admin_app = create_admin_app(server, admin_repository, admin_settings)
    stats_app = create_admin_app(server, admin_repository, admin_settings, public=True)
    host = str(settings.get("ListenOn", "")) or "0.0.0.0"
    http_server = uvicorn.Server(uvicorn.Config(
        app, host=host, port=int(settings["WebInterface"]["port"]), log_level=None, lifespan="off",
        log_config=None, use_colors=False,
    ))
    admin_host = host
    if (os.getenv("FIVESERVER_ADMIN_PASSWORD") or admin_settings["AdminPassword"]) == DEFAULT_ADMIN_PASSWORD:
        admin_host = "127.0.0.1"
    keys_dir = Path(str(admin_settings["KeysDirectory"]))
    admin_server = uvicorn.Server(uvicorn.Config(
        admin_app, host=admin_host, port=int(admin_settings["AdminPort"]),
        ssl_keyfile=str(keys_dir / "serverkey.pem"), ssl_certfile=str(keys_dir / "servercert.pem"),
        log_level=None, lifespan="off", log_config=None, use_colors=False,
    ))
    stats_server = uvicorn.Server(uvicorn.Config(
        stats_app, host="127.0.0.1", port=int(admin_settings["AdminPort"]) + 1,
        log_level=None, lifespan="off", log_config=None, use_colors=False,
    ))
    return http_server, admin_server, stats_server


async def _serve(servers: tuple[uvicorn.Server, ...]) -> None:
    tasks = [asyncio.create_task(_serve_web(item, name)) for item, name in zip(
        servers, ("registration", "admin", "statistics"), strict=True,
    )]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for item in servers:
        item.should_exit = True
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()


async def _serve_web(server: uvicorn.Server, name: str) -> None:
    """Label each real Uvicorn instance without changing its lifecycle or logs."""
    with logging_context(f"uvicorn.{name}"):
        await server.serve()


def _validate_admin_tls(settings: dict[str, Any]) -> None:
    """Fail before database/listener startup with actionable first-run guidance."""
    directory = Path(str(settings["KeysDirectory"]))
    missing = [str(directory / name) for name in ("serverkey.pem", "servercert.pem")
               if not (directory / name).is_file()]
    if missing:
        raise FileNotFoundError(
            "Admin HTTPS TLS files missing: " + ", ".join(missing)
            + ". See FIRST-RUN HTTPS SETUP in README; install a certificate and an "
            "unencrypted private key at both paths."
        )


async def run(config: Path, games: tuple[GameId, ...]) -> None:
    """Assemble the database, TCP server, HTTP services, and scheduler."""
    settings = load_settings(config)
    six = games[0] in SIX_GAMES
    admin_path = Path("etc/conf/admin6.yaml" if six else "etc/conf/admin.yaml")
    admin_settings = load_settings(admin_path)
    configure_logging(
        Path(str(admin_settings["FiveserverLogFile"])),
        debug=bool(settings.get("Debug")),
        console=bool(settings.get("LogConsole", True)), timezone_=str(settings.get("LogTimezone", "UTC")),
    )
    LOGGER.info("Loading configuration from %s", config)
    LOGGER.info("Fiveserver %s starting (%s)", VERSION, games[0].value)
    _validate_admin_tls(admin_settings)
    database = await MySQLDatabase.connect(settings["DB"])
    server: FiveServer | None = None
    scheduler: Scheduler | None = None
    web_servers: tuple[uvicorn.Server, ...] = ()
    try:
        LOGGER.info("Database connected: %s", settings["DB"]["name"])
        database.start_keepalive()
        banned = _load_banned(settings)
        if banned:
            LOGGER.info("Loaded %d banned network(s)", len(banned))
        user_repository = UserRepository(database, pes6=six)
        server_ip = await resolve_server_ip(settings)
        server = FiveServer(settings, games, server_ip, user_repository, banned)
        web_servers = _web_servers(
            settings, six, server, database, user_repository, admin_settings,
        )
        scheduler = Scheduler(settings, server.lobbies, RankingRepository(database))
        await server.start()
        scheduler.start()
        LOGGER.info("Server IP-address: %s", server_ip)
        LOGGER.info("Fiveserver %s ready", VERSION)
        LOGGER.debug("TCP listeners ready: %d", len(server.listeners))
        await _serve(web_servers)
    finally:
        LOGGER.info("Fiveserver shutting down")
        for web_server in web_servers:
            web_server.should_exit = True
        if scheduler is not None:
            await scheduler.close()
        if server is not None:
            await server.close()
        await database.close()
        LOGGER.info("Fiveserver stopped")
        flush_logging()


def main() -> None:
    """Parse the five/six family and start the asyncio runtime."""
    parser = argparse.ArgumentParser()
    parser.add_argument("family", choices=("five", "six"))
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    config = args.config or Path("etc/conf/fiveserver.yaml" if args.family == "five" else "etc/conf/sixserver.yaml")
    configure_logging(None, debug=False, announce_debug=False)
    try:
        asyncio.run(run(config, tuple(sorted(FIVE_GAMES if args.family == "five" else SIX_GAMES))))
    except KeyboardInterrupt:
        LOGGER.info("Server interrupted, shutting down.")
    except Exception:
        LOGGER.exception("Fatal server error")
        flush_logging()
        raise SystemExit(1) from None
    finally:
        close_logging()


if __name__ == "__main__":
    main()
