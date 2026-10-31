"""Container health check for the registration HTTP endpoint and MariaDB."""

import argparse
import asyncio
import os
import sys
import urllib.request

import asyncmy  # type: ignore[import-untyped]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Registration/statistics HTTP URL")
    parser.add_argument("--database", required=True, help="Database name and default user")
    parser.add_argument("--user", default=None, help="Database user (defaults to --database)")
    parser.add_argument("--timeout", type=float, default=4.0, help="Overall timeout in seconds")
    return parser


async def _check(args: argparse.Namespace) -> None:
    urllib.request.urlopen(args.url, timeout=2).close()
    user = args.user or args.database
    connection = await asyncmy.connect(
        host=os.environ["FIVESERVER_DB_HOST"],
        port=int(os.getenv("FIVESERVER_DB_PORT", "3306")),
        user=user,
        password=os.environ["FIVESERVER_DB_PASSWORD"],
        db=args.database,
    )
    try:
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT 1")
            row = await cursor.fetchone()
            if row is None or row[0] != 1:
                raise RuntimeError("Database connectivity check returned an unexpected result")
    finally:
        connection.close()


def main() -> None:
    """Run both checks and exit nonzero when either dependency is unavailable."""
    args = _parser().parse_args()
    try:
        asyncio.run(asyncio.wait_for(_check(args), timeout=args.timeout))
    except Exception as exc:  # Docker only uses the exit status; keep logs concise.
        print(f"health check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
