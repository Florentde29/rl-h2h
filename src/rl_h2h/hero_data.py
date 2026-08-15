"""Values the hero card shows that nothing computed before.

Kept free of Qt so each one can be tested on its own — they are small, but
several are easy to get subtly wrong (a session delta that counts snapshots
from yesterday, a rank distance that reads off the wrong end of a band).
"""
from __future__ import annotations

from typing import Optional

from .mmr import MMR_RANK_ZONES, attribute_mmr_points
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


def next_rank_distance(mmr: Optional[int]) -> Optional[tuple[int, str]]:
    """(points, next band name) — how far to the next rank band.

    Deliberately band-level, not division-level: MMR_RANK_ZONES only knows
    Champion, not Champion II, and the wire gives us no division thresholds.
    Callers should phrase it softly for that reason. None at the top band."""
    if mmr is None:
        return None
    for i, (lo, hi, name, _color) in enumerate(MMR_RANK_ZONES):
        if lo <= mmr < hi:
            if i + 1 >= len(MMR_RANK_ZONES):
                return None
            return (max(0, hi - int(mmr)), MMR_RANK_ZONES[i + 1][2])
    return None


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


def peak_worth_showing(entry: Optional[dict], current: Optional[int],
                       margin: int = 40) -> Optional[int]:
    """Peak MMR, but only when it's meaningfully above the current value.

    Showing a peak that equals where you already are is noise; the margin is
    what makes it a fact worth the pixels."""
    if not entry or current is None:
        return None
    peak = (entry.get("peak_all_time") or {}).get("mmr")
    if not isinstance(peak, (int, float)):
        return None
    peak = int(peak)
    return peak if peak - current >= margin else None
