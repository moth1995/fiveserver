"""Pure response formatters for the PES2008 PC web endpoints."""

from datetime import datetime, timedelta


def resolveRankingType(rankingType, now=None):
    """Map the PES2008 ranking selector to a division or date window."""
    rankingType = int(rankingType)
    if now is None:
        now = datetime.now()

    # Values observed from the PC client:
    # 0..4 = Division 3C, 3B, 3A, 2 and 1; 5 = overall;
    # 6 = today; 7 = yesterday.
    if 0 <= rankingType <= 4:
        return rankingType, None, None
    if rankingType == 5:
        return None, None, None

    today = datetime(now.year, now.month, now.day)
    if rankingType == 6:
        return None, today, today + timedelta(days=1)
    if rankingType == 7:
        return None, today - timedelta(days=1), today
    raise ValueError("unsupported PES2008 ranking type: %d" % rankingType)


def formatEmptyRankingResponse(now=None):
    """Build a syntactically valid PES2008 ranking response with no rows."""
    if now is None:
        now = datetime.now()

    # The client splits the body on LF. Lines 1 and 2 are parsed as
    # status/record-count and timestamp; lines 0 and 3-8 are reserved.
    lines = [
        "PES2008",
        "1/0",
        now.strftime("%Y-%m-%d %H:%M:%S"),
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


def _formatRankingRow(entry):
    name = entry.get("name", "")
    if isinstance(name, bytes):
        name = name.decode("utf-8", "replace")
    name = name.replace(",", " ").replace("\r", " ").replace("\n", " ")
    name = name.encode("ascii", "replace")[:47].decode("ascii")
    division_wire = 4 - max(0, min(4, int(entry.get("division", 0))))
    fields = (
        entry.get("rank", 0),
        entry.get("profileId", 0),
        name,
        entry.get("games", 0),
        entry.get("wins", 0),
        entry.get("losses", 0),
        entry.get("draws", 0),
        entry.get("strikeRecord", 0),
        entry.get("points", 0),
        division_wire,
    )
    return "%d,%d,%s,%d,%d,%d,%d,%d,%d,%d" % fields


def formatRankingResponse(entries, total, playerEntry=None, now=None):
    """Build the line-oriented ranking response parsed by PES2008 PC."""
    if now is None:
        now = datetime.now()
    if playerEntry is None:
        playerEntry = {}

    lines = [
        "PES2008",
        "1/%d" % max(0, int(total)),
        now.strftime("%Y-%m-%d %H:%M:%S"),
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        # The client always reserves the first post-header row for the
        # profile identified by pid. It does not count this as a page row.
        _formatRankingRow(playerEntry),
    ]
    lines.extend(_formatRankingRow(entry) for entry in entries[:20])
    return ("\n".join(lines) + "\n").encode("ascii")


def formatRankingPage():
    """Build the human-readable page returned when the ranking URL is opened."""
    return b"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>PES2008 Rankings</title>
</head>
<body>
  <h1>PES2008 Rankings</h1>
  <p>The ranking service is online.</p>
  <p>Ranking records are available from the in-game ranking menu.</p>
</body>
</html>
"""
