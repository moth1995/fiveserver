import asyncio
from datetime import timedelta
import hashlib
from pathlib import Path
import os
import uuid

import asyncmy
import pytest

from fiveserver.web.admin import AdminRepository
from fiveserver.database import MatchRecord, MySQLDatabase, Profile, RankingRepository, UserRepository
from fiveserver.web.registration import RegistrationRepository


ROOT = Path(__file__).resolve().parents[1]


def _db_env():
    return {
        "host": os.getenv("FIVESERVER_TEST_DB_HOST"),
        "port": int(os.getenv("FIVESERVER_TEST_DB_PORT", "3306")),
        "user": os.getenv("FIVESERVER_TEST_DB_USER", "root"),
        "password": os.getenv("FIVESERVER_TEST_DB_PASSWORD", ""),
    }


async def _load_schema(env, name, schema):
    connection = await asyncmy.connect(**env)
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8")
        await connection.select_db(name)
        statements = (ROOT / schema).read_text(encoding="utf-8").split(";")
        async with connection.cursor() as cursor:
            for statement in statements:
                if statement.strip():
                    await cursor.execute(statement)
    finally:
        connection.close()


async def _drop_schema(env, name):
    connection = await asyncmy.connect(**env)
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
    finally:
        connection.close()


@pytest.fixture
def schemas():
    env = _db_env()
    if not env["host"]:
        pytest.skip("Set FIVESERVER_TEST_DB_HOST to run MySQL integration tests")
    suffix = uuid.uuid4().hex[:12]
    names = {"five": f"fiveserver_test5_{suffix}", "six": f"fiveserver_test6_{suffix}"}

    async def setup():
        await _load_schema(env, names["five"], "sql/schema.sql")
        await _load_schema(env, names["six"], "sql/schema6.sql")

    async def teardown():
        await _drop_schema(env, names["five"])
        await _drop_schema(env, names["six"])

    asyncio.run(setup())
    try:
        yield env, names
    finally:
        asyncio.run(teardown())


def _config(env, name):
    return {
        "user": env["user"],
        "password": env["password"],
        "name": name,
        "port": env["port"],
        "readServers": [env["host"]],
        "writeServers": [env["host"]],
        "sharePool": True,
        "ConnectionPool": {"minConnections": 1, "maxConnections": 3},
    }


async def _connect(env, name):
    return await MySQLDatabase.connect(_config(env, name))


async def _insert_user(database, suffix="one"):
    digest = hashlib.md5(suffix.encode(), usedforsecurity=False).hexdigest()
    await database.write(
        "INSERT INTO users (username,serial,hash) VALUES (%s,%s,%s)",
        f"user-{suffix}", "S" * 20, digest,
    )
    rows = await database.read("SELECT id,hash FROM users WHERE username=%s", f"user-{suffix}")
    return int(rows[0][0]), str(rows[0][1])


async def _insert_profile(database, user_id, ordinal, name, **fields):
    columns = ["user_id", "ordinal", "name", *fields]
    values = (user_id, ordinal, name, *fields.values())
    placeholders = ",".join(["%s"] * len(values))
    await database.write(
        f"INSERT INTO profiles ({','.join(columns)}) VALUES ({placeholders})", *values
    )
    rows = await database.read("SELECT id FROM profiles WHERE name=%s", name)
    return int(rows[0][0])


def test_pes5_repository_match_transaction_stats_and_ranking(schemas):
    env, names = schemas

    async def scenario():
        database = await _connect(env, names["five"])
        try:
            user_id, user_hash = await _insert_user(database)
            home = await _insert_profile(database, user_id, 0, "home")
            away = await _insert_profile(database, user_id, 1, "away")
            repository = UserRepository(database, pes6=False)

            user = await repository.by_hash(user_hash)
            assert user is not None and user.hash == user_hash
            assert [profile.id for profile in user.profiles] == [home, away, 0]

            match_id = await repository.store_match(
                MatchRecord((home,), (away,), 2, 1, team_home=10, team_away=20)
            )
            assert match_id > 0
            assert await repository.games_played(home) == 1
            stats = await repository.profile_stats(home)
            assert (stats.wins, stats.losses, stats.draws) == (1, 0, 0)
            assert (stats.goals_scored, stats.goals_allowed) == (2, 1)
            assert (stats.streak_current, stats.streak_best) == (1, 1)
            matches = await repository.last_matches(home)
            assert matches[0][:5] == (away, 2, 1, 10, 20)

            profile = user.profiles[0]
            profile.points = 450
            profile.disconnects = 2
            profile.play_time = timedelta(seconds=90)
            await repository.save_profile(profile)
            saved = await database.read(
                "SELECT points,disconnects,seconds_played FROM profiles WHERE id=%s", home
            )
            assert saved[0] == (450, 2, 90)

            await repository.store_profile_settings(
                Profile(0, user_id, home, "home", settings1=b"one", settings2=b"two")
            )
            assert await repository.profile_settings(home) == (b"one", b"two")

            await database.write("UPDATE profiles SET points=%s WHERE id=%s", 600, away)
            await RankingRepository(database).compute_ranks()
            ranks = await database.read("SELECT id,`rank` FROM profiles ORDER BY id")
            assert [int(row[1]) for row in ranks] == [2, 1]

            before = (await database.read("SELECT count(id) FROM matches"))[0][0]
            with pytest.raises(Exception):
                await repository.store_match(
                    MatchRecord((999999,), (away,), 1, 0, team_home=1, team_away=2)
                )
            after = (await database.read("SELECT count(id) FROM matches"))[0][0]
            assert before == after
        finally:
            await database.close()

    asyncio.run(scenario())


def test_registration_admin_and_profile_repositories_round_trip(schemas):
    env, names = schemas

    async def scenario():
        database = await _connect(env, names["five"])
        try:
            registration = RegistrationRepository(database)
            await registration.store(None, "reg-user", "S" * 20, "a" * 32, None)
            user = await registration.find("username", "reg-user")
            assert user is not None
            user_id = int(user[0])
            assert await registration.find("hash", "a" * 32) == user

            await registration.store(user_id, "renamed", "R" * 20, "b" * 32, "1234")
            assert (await registration.find("reset_nonce", "1234"))[1] == "renamed"

            profiles = UserRepository(database, pes6=False)
            profile_id = await _insert_profile(
                database, user_id, 0, "reg-profile", fav_team=25, points=300
            )
            assert await profiles.profile_name_exists("reg-profile")
            assert await profiles.profile_name(profile_id) == "reg-profile"
            fetched = await profiles.profile_by_id(profile_id)
            assert fetched is not None and fetched.fav_team == 25

            admin = AdminRepository(database, profiles)
            total, users = await admin.users(0, 10)
            assert total == 1 and users[0][1] == "renamed"
            total, listed = await admin.profiles_page(0, 10)
            assert total == 1 and listed == [(profile_id, "reg-profile")]
            assert (await admin.profile("reg-profile")).id == profile_id
            assert (await admin.user_by_name("renamed"))[0] == user_id

            nonce = await admin.lock_user(user_id)
            assert (await registration.find("reset_nonce", nonce))[0] == user_id
            await admin.delete_user(user_id)
            assert await registration.find("username", "renamed") is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_pes6_repository_multiplayer_match_and_last_teams(schemas):
    env, names = schemas

    async def scenario():
        database = await _connect(env, names["six"])
        try:
            user_id, user_hash = await _insert_user(database, "six")
            other_user_id, _ = await _insert_user(database, "six-away")
            home = await _insert_profile(database, user_id, 0, "home6", rating=100)
            mate = await _insert_profile(database, user_id, 1, "mate6", rating=110)
            away = await _insert_profile(database, other_user_id, 0, "away6", rating=120)
            away_mate = await _insert_profile(database, other_user_id, 1, "awaymate6", rating=130)
            repository = UserRepository(database, pes6=True)

            user = await repository.by_hash(user_hash)
            assert user is not None and user.profiles[0].rating == 100

            await repository.store_match(
                MatchRecord((home, mate), (away, away_mate), 3, 0, team_home=11, team_away=22)
            )
            rows = await database.read(
                "SELECT profile_id,home FROM matches_played ORDER BY profile_id"
            )
            assert rows == [(home, 1), (mate, 1), (away, 0), (away_mate, 0)]

            stats = await repository.profile_stats(home)
            assert (stats.wins, stats.losses, stats.draws) == (1, 0, 0)
            assert (stats.goals_scored, stats.goals_allowed) == (3, 0)
            assert stats.teams == (11,)
            assert await repository.games_played(away) == 1
            assert (await repository.profile_stats(away)).teams == (22,)

            profile = user.profiles[0]
            profile.comment = "hello"
            profile.points = 700
            await repository.save_profile(profile)
            saved = await database.read(
                "SELECT points,comment FROM profiles WHERE id=%s", home
            )
            assert saved[0] == (700, "hello")
        finally:
            await database.close()

    asyncio.run(scenario())


