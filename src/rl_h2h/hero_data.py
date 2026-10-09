"""Values the hero card shows that nothing computed before.

Kept free of Qt so each one can be tested on its own — they are small, but
several are easy to get subtly wrong (a session delta that counts snapshots
from yesterday, a sparkline that disagrees with the graph it summarises).

Nothing here invents a rank threshold. Rocket League's tier boundaries differ
per playlist and move between seasons, so anything derived from a fixed table
is wrong somewhere — rank and division come from TRN, which reports both.
"""
from __future__ import annotations

from typing import Optional

from .mmr import attribute_mmr_points
from .paths import parse_iso


def playlist_mmr(entry: Optional[dict], category: str) -> Optional[int]:
    """The MMR a player's chip is showing, for gap arithmetic."""
    if not entry or entry.get("not_found"):
        return None
    if category == "best":
        pick = entry.get("best")
    elif category == "peak":
        pick = entry.get("peak_all_time")
    else:
        pick = (entry.get("playlists") or {}).get(category)
    if not pick:
        return None
    mmr = pick.get("mmr")
    return int(mmr) if isinstance(mmr, (int, float)) else None


def session_mmr_delta(snapshots: list[dict], playlist: str,
                      since_iso: Optional[str]) -> Optional[int]:
    """MMR moved this session: newest snapshot minus the oldest since `since_iso`.

    Returns None with fewer than two snapshots in the window — a single
    snapshot says where you are, not how far you've come."""
    if not snapshots or not since_iso:
        return None
    since = parse_iso(since_iso)
    if since is None:
        return None
    vals = []
    for s in snapshots:
        mmr = (s.get("playlists") or {}).get(playlist)
        ts = parse_iso(s.get("ts"))
        if mmr is None or ts is None:
            continue
        if ts >= since:
            vals.append((ts, int(mmr)))
    if len(vals) < 2:
        return None
    vals.sort(key=lambda v: v[0])
    return vals[-1][1] - vals[0][1]


def sparkline(playlist: str, snapshots: list[dict], matches: list[dict],
              cfg: dict, points: int = 11) -> list[int]:
    """The graph's own attributed series, thinned to `points` for the sparkline.

    Reuses attribute_mmr_points so the sparkline and the full graph can never
    tell different stories. Keeps the newest value — the endpoint is the one
    the eye lands on."""
    attributed = attribute_mmr_points(
        playlist, snapshots, matches,
        grace_seconds=int(cfg.get("graph_match_grace_seconds", 120)),
        window=int(cfg.get("graph_match_window", 30)),
    )
    vals = [int(p["mmr"]) for p in attributed
            if isinstance(p.get("mmr"), (int, float))]
    if len(vals) <= points:
        return vals
    step = (len(vals) - 1) / (points - 1)
    thinned = [vals[round(i * step)] for i in range(points)]
    thinned[-1] = vals[-1]
    return thinned


def together_record(rec: Optional[dict], bucket: str) -> Optional[tuple[int, int, int]]:
    """(wins, losses, win %) from a players.json bucket, or None if never met."""
    if not rec:
        return None
    b = rec.get(bucket) or {}
    wins, losses = int(b.get("wins", 0)), int(b.get("losses", 0))
    total = wins + losses
    if not total:
        return None
    return (wins, losses, round(wins * 100 / total))


def last_meeting_phrase(rec: Optional[dict], bucket: str,
                        humanize) -> Optional[str]:
    """'won 3–1, 2h ago' — the design's sentence form rather than glyphs.

    Reads as a memory of the match instead of a row of codes, which is the
    point of the redesign's tile."""
    if not rec:
        return None
    b = rec.get(bucket) or {}
    when = humanize(b.get("lastSeenAt"))
    result = b.get("lastResult")
    if not when or not result:
        return None
    verb = "won" if result == "W" else "lost"
    score = b.get("lastScore")
    if isinstance(score, list) and len(score) == 2:
        return f"{verb} {score[0]}–{score[1]}, {when}"
    return f"{verb}, {when}"


# Rank ladder positions, as TRN numbers them. Three rungs per group, so the
# index alone names any rank — including the one above you — without needing
# a single MMR boundary. Verified against a live payload (Gold III=9,
# Platinum III=12, Diamond I..III=13..15, Champion I=16, Champion II=17).
_RANK_GROUPS = ("Bronze", "Silver", "Gold", "Platinum", "Diamond",
                "Champion", "Grand Champion")
TIER_LADDER = {0: "Unranked"}
for _g, _i in ((g, 1 + n * 3) for n, g in enumerate(_RANK_GROUPS)):
    for _off, _num in enumerate(("I", "II", "III")):
        TIER_LADDER[_i + _off] = f"{_g} {_num}"
TIER_LADDER[len(TIER_LADDER)] = "Supersonic Legend"

DIVISIONS_PER_TIER = 4


def next_tier_target(pick: Optional[dict]) -> Optional[tuple[int, str, bool]]:
    """(points, next rank name, exact) — how far to the next RANK, not division.

    TRN's deltaUp is the distance to the next division, which is not what
    anyone is chasing: Champion I Division II is still Champion I. The rank
    boundary sits at the top of the fourth division.

    From the fourth division the two coincide, so deltaUp is the answer and
    `exact` is True. Below that the remaining divisions are added at the width
    of the current one, which Rocket League splits evenly inside a rank — that
    part is derived, so `exact` is False and callers should mark it.

    Returns None rather than a guess whenever TRN omits any of the inputs.
    """
    if not pick:
        return None
    up = pick.get("delta_up")
    down = pick.get("delta_down")
    tier_idx = pick.get("tier_index")
    div_idx = pick.get("division_index")
    if up is None or tier_idx is None or div_idx is None:
        return None
    next_name = TIER_LADDER.get(int(tier_idx) + 1)
    if not next_name:
        return None  # already at the top of the ladder
    if div_idx >= DIVISIONS_PER_TIER - 1:
        return (int(up), next_name, True)
    if down is None:
        return None
    width = int(up) + int(down)
    remaining = (DIVISIONS_PER_TIER - 1 - int(div_idx)) * width
    return (int(up) + remaining, next_name, False)
