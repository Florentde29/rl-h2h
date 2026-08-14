"""Players + matches I/O. JSONL append for matches, pretty JSON for players."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Optional

from .constants import BUCKET_VS, BUCKET_WITH
from .paths import MATCHES_PATH, PLAYERS_PATH, load_jsonl, safe_atomic_write_text


def player_key(primary_id: str) -> str:
    """The splitscreen suffix is unstable across sessions; collapse to Platform|Uid."""
    parts = primary_id.split("|")
    return f"{parts[0]}|{parts[1]}" if len(parts) >= 2 else primary_id


def load_players() -> dict:
    if not PLAYERS_PATH.exists():
        return {}
    try:
        return json.loads(PLAYERS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        ts = int(datetime.now(timezone.utc).timestamp())
        backup = PLAYERS_PATH.with_name(f"players.corrupt-{ts}.json")
        try:
            PLAYERS_PATH.rename(backup)
            print(f"[players] corrupt file backed up to {backup.name}: {e}", file=sys.stderr)
        except OSError:
            print(f"[players] corrupt and could not back up: {e}", file=sys.stderr)
        return {}


def save_players(players: dict) -> None:
    safe_atomic_write_text(PLAYERS_PATH, json.dumps(players, indent=2, sort_keys=True), "players")


def append_match(record: dict) -> bool:
    """Append one match to matches.jsonl. False on failure; never raises.

    This was the only unprotected write in the app, and it ran before the
    players-cache update — so a single OSError (OneDrive holding a lock on
    Documents is the common one on Windows) propagated out of the Qt slot and
    skipped the rest of the handler, silently costing the head-to-head record
    for that match. matches.jsonl only feeds the MMR graph, so losing a line
    here must never take players.json down with it.
    """
    try:
        with MATCHES_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return True
    except OSError as e:
        print(f"[matches] could not append match: {e}", file=sys.stderr)
        return False


_PLAYLIST_BY_PLAYER_COUNT = {2: "1v1", 4: "2v2", 6: "3v3"}


def playlist_from_player_count(n: int) -> str:
    return _PLAYLIST_BY_PLAYER_COUNT.get(n, "other")


def match_playlist(record: dict) -> str:
    """Field-or-derive: prefer the explicit playlist key, fall back to roster
    size for matches saved before that field existed. Every consumer should
    go through this — the raw record key is unreliable for legacy entries."""
    pl = record.get("playlist")
    if isinstance(pl, str) and pl:
        return pl
    players = record.get("players") or []
    return playlist_from_player_count(len(players))


def load_matches() -> list[dict]:
    return load_jsonl(MATCHES_PATH, "matches")


def update_players_cache(players: dict, match: dict) -> None:
    my_team = match["myTeam"]
    i_won = match["winner"] == my_team
    when = match["endedAt"]
    score = match.get("score")  # [blue, orange]
    if isinstance(score, list) and len(score) == 2:
        my_pov = [score[my_team], score[1 - my_team]]
    else:
        my_pov = None
    result_letter = "W" if i_won else "L"
    for p in match["players"]:
        rec = players.setdefault(p["key"], {
            "name": p["name"],
            "aliases": [p["name"]],
            BUCKET_VS:   {"wins": 0, "losses": 0, "lastSeenAt": None,
                          "lastResult": None, "lastScore": None},
            BUCKET_WITH: {"wins": 0, "losses": 0, "lastSeenAt": None,
                          "lastResult": None, "lastScore": None},
        })
        rec["name"] = p["name"]
        if p["name"] not in rec["aliases"]:
            rec["aliases"].append(p["name"])
        bucket = rec[BUCKET_WITH if p["team"] == my_team else BUCKET_VS]
        if i_won:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
        bucket["lastSeenAt"] = when
        bucket["lastResult"] = result_letter
        bucket["lastScore"] = my_pov


def last_touch_player(data: dict) -> tuple[Optional[str], Optional[int]]:
    """(Name, TeamNum) of BallLastTouch.Player, or (None, None) if absent/malformed."""
    last_touch = data.get("BallLastTouch")
    if not isinstance(last_touch, dict):
        return (None, None)
    player = last_touch.get("Player")
    if not isinstance(player, dict):
        return (None, None)
    name = player.get("Name")
    team = player.get("TeamNum")
    return (
        name if isinstance(name, str) else None,
        team if isinstance(team, int) and team in (0, 1) else None,
    )
