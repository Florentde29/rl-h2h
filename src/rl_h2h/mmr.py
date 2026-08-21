"""tracker.network MMR fetch + per-game attribution for the graph view.

The official Stats API doesn't expose MMR or rank. We optionally pull it from
tracker.network's public JSON endpoint, which serves real-time data behind a
4-minute server-side cache. ``curl_cffi`` impersonates Chrome's TLS fingerprint
so Cloudflare lets us through without a browser.

Lookup quirk: TRN indexes Epic/PSN/Xbox profiles by display name only —
their numeric IDs (32-hex Epic UUID, PSN/XBL ints) return 400/404. Steam
is the exception: SteamID64 works and is preferred (Steam display names
aren't unique, so name lookups 404 for plenty of valid accounts). Cache
key stays the stable Platform|Uid regardless, so renames don't pollute
our cache.
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from PySide6.QtCore import QObject, Signal

from . import colors
from .applog import mmr_log
from .paths import (
    MMR_CACHE_PATH,
    MMR_HISTORY_PATH,
    TRN_PAYLOAD_PATH,
    load_jsonl,
    now_iso,
    parse_iso,
    safe_atomic_write_text,
)
from .storage import match_playlist, player_key


MMR_PLATFORM_TO_TRN = {
    "Epic": "epic",
    "Steam": "steam",
    "PS4": "psn",
    "XboxOne": "xbl",
    "Switch": "switch",
}

# TRN playlist IDs we care about. Casual / extra-modes are intentionally
# excluded from "best" — they don't reflect competitive skill.
MMR_PLAYLIST_IDS = {
    10: "1v1",
    11: "2v2",
    13: "3v3",
}
MMR_CATEGORIES = ("best", "1v1", "2v2", "3v3", "peak")
RANKED_PLAYLISTS = ("1v1", "2v2", "3v3")  # cycled by the cycle hotkey in graph view; iterated for self-MMR logging

# Approximate tier MMR ranges, for the graph's background bands ONLY.
#
# Do NOT compute anything from these. Rocket League's real boundaries differ
# per playlist — 1v1 sits well below 3v3 for the same tier — and Psyonix moves
# them between seasons, so one fixed table is wrong for at least two of the
# three playlists at any time. A "points to next rank" figure derived from it
# was off by hundreds. Tier and division come from TRN, which reports both
# per playlist and per season; render those instead.
MMR_RANK_ZONES = [
    (0,    195,  "Bronze",            "#B87333"),
    (195,  395,  "Silver",             "#C0C5CD"),
    (395,  595,  "Gold",               "#F0C674"),
    (595,  795,  "Platinum",           "#6FC8D6"),
    (795,  995,  "Diamond",            "#7FA9F2"),
    (995,  1195, "Champion",           "#B59CEE"),
    (1195, 1565, "Grand Champion",     "#EC4F50"),
    (1565, 2500, "Supersonic Legend",  "#DB2C70"),
]

# Standard RL rank colors. Tier strings come from TRN as "Bronze I", "Diamond III",
# "Grand Champion II", etc. — we match on the prefix word. Derived from
# MMR_RANK_ZONES with an explicit Unranked entry (zones cover ranked play only).
MMR_TIER_COLORS = {"Unranked": "#8E9379", **{name: color for _lo, _hi, name, color in MMR_RANK_ZONES}}

MMR_TTL_SECONDS = 600   # local cache freshness — TRN's own TTL is 4 min
# Min seconds between outbound TRN requests. TRN's median latency is ~500ms,
# so this floor only kicks in on fast responses — effectively caps us at 2
# req/sec without ever burst-blasting. A 6-player roster resolves in ~3s.
MMR_FETCH_INTERVAL = 0.5
# Transient failures (curl timeout, 5xx, 429) get retried automatically.
# Without this, a single 15s timeout used to strand a player as "…" for the
# whole match — on_initialized only re-enqueues on a new match boundary.
# Linear backoff (3s, 6s, 9s) keeps the worker free for other players in the
# same roster batch while giving TRN a moment to recover.
MMR_MAX_RETRIES = 3
MMR_RETRY_BACKOFF_S = 3.0


def mmr_lookup_handle(primary_id: str, name: str) -> Optional[tuple[str, str]]:
    """(trn_platform_slug, lookup_token) for a wire identity, or None if
    unsupported.

    Steam: use the SteamID64 from the wire — Steam display names are
    non-unique and TRN's name lookup returns 404 for plenty of valid
    accounts (observed live for handles like 'kllr'). The numeric
    SteamID64 always resolves when the player has any tracker history.

    Epic / PSN / Xbox: TRN only accepts the display name on these.
    Their numeric IDs (32-hex Epic UUID, PSN/XBL ints) return 400/404.
    """
    parts = primary_id.split("|")
    if not parts:
        return None
    plat = MMR_PLATFORM_TO_TRN.get(parts[0])
    if not plat:
        return None
    if plat == "steam" and len(parts) >= 2 and parts[1]:
        return (plat, parts[1])
    if not name:
        return None
    return (plat, name)


def tier_color(tier: Optional[str]) -> str:
    if not tier:
        return MMR_TIER_COLORS["Unranked"]
    for prefix, color in MMR_TIER_COLORS.items():
        if tier.startswith(prefix):
            return color
    return colors.C_TEXT


def parse_trn_payload(data: dict) -> dict:
    """Distill a TRN profile response into our compact cache shape."""
    info = (data or {}).get("platformInfo") or {}
    meta = (data or {}).get("metadata") or {}
    last_updated = (meta.get("lastUpdated") or {}).get("value")

    playlists: dict[str, dict] = {}
    for seg in (data or {}).get("segments") or []:
        if seg.get("type") != "playlist":
            continue
        attrs = seg.get("attributes") or {}
        pid = attrs.get("playlistId")
        label = MMR_PLAYLIST_IDS.get(pid)
        if not label:
            continue
        stats = seg.get("stats") or {}
        rating = (stats.get("rating") or {}).get("value")
        tier_stat = stats.get("tier") or {}
        tier = (tier_stat.get("metadata") or {}).get("name")
        # tier.value is a position on the rank ladder, not a match count despite
        # its displayName. Verified against a live payload: Gold III=9,
        # Platinum III=12, Diamond I/II/III=13/14/15, Champion I=16,
        # Champion II=17, Unranked=0 — three rungs per rank group. It lets us
        # name the next rank without knowing any MMR boundary.
        tier_idx = tier_stat.get("value")
        div_meta = (stats.get("division") or {}).get("metadata") or {}
        div = div_meta.get("name")
        # TRN reports how far this player is from ranking up, per playlist and
        # per season. That is the only trustworthy source for it: Rocket
        # League's thresholds differ between playlists and move between
        # seasons, so nothing computed from a local table can be right for
        # long. Absent on some payloads, hence the guarded read.
        up = div_meta.get("deltaUp")
        down = div_meta.get("deltaDown")
        if rating is None:
            continue
        playlists[label] = {
            "mmr": int(rating),
            "tier": tier or "Unranked",
            "division": div or "",
            "delta_up": int(up) if isinstance(up, (int, float)) else None,
            "delta_down": int(down) if isinstance(down, (int, float)) else None,
            "tier_index": int(tier_idx) if isinstance(tier_idx, (int, float)) else None,
            # 0-based: Division I is 0. Fourth division borders the next rank.
            "division_index": (int(div_val)
                               if isinstance(div_val := (stats.get("division") or {}).get("value"),
                                             (int, float)) else None),
        }

    best = None
    for label, p in playlists.items():
        if best is None or p["mmr"] > best["mmr"]:
            best = {**p, "playlist": label}

    # All-time peak across every playlist TRN reports a peak-rating segment
    # for (ranked, casual, extra modes — TRN scopes the peak per playlist
    # but tags each with the season it was set). We just want the single
    # highest number the player has ever held.
    peak_all_time = None
    for seg in (data or {}).get("segments") or []:
        if seg.get("type") != "peak-rating":
            continue
        pr = (seg.get("stats") or {}).get("peakRating") or {}
        val = pr.get("value")
        if val is None:
            continue
        tier_name = (pr.get("metadata") or {}).get("name") or "Unranked"
        if peak_all_time is None or val > peak_all_time["mmr"]:
            peak_all_time = {"mmr": int(val), "tier": tier_name}

    return {
        "fetched_at": now_iso(),
        "lastUpdated": last_updated,
        "handle": info.get("platformUserHandle"),
        "playlists": playlists,
        "best": best,
        "peak_all_time": peak_all_time,
    }


def load_mmr_cache() -> dict:
    if not MMR_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(MMR_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[mmr] failed to read cache, starting fresh: {e}", file=sys.stderr)
        return {}


def save_mmr_cache(cache: dict) -> None:
    safe_atomic_write_text(MMR_CACHE_PATH, json.dumps(cache, indent=2, sort_keys=True), "mmr")


def append_mmr_history(entry: dict) -> None:
    """One JSON line per snapshot of self MMR. Append-only."""
    try:
        with MMR_HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        print(f"[mmr-history] write failed: {e}", file=sys.stderr)


def load_mmr_history() -> list[dict]:
    return load_jsonl(MMR_HISTORY_PATH, "mmr-history")


class MMRClient(QObject):
    """Background fetcher for player MMR/rank data from tracker.network.

    Fully off-thread: a single worker thread drains a queue, hits the TRN
    public API, and emits ``updated(player_key)`` when fresh data lands. The Qt
    main loop only ever touches the cache via ``get(key)``. Soft-fails on every
    error path — the overlay never breaks because TRN is down.

    Disabled (``enabled=False``) means: never enqueue, never network. Toggleable
    at runtime via ``set_enabled()`` so the tray menu can flip it without a
    restart.
    """

    updated = Signal(str)  # cache key (Platform|Uid)

    _BASE_URL = "https://api.tracker.gg/api/v2/rocket-league/standard/profile/{plat}/{ident}"
    _HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://rocketleague.tracker.network",
        "Referer": "https://rocketleague.tracker.network/",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self, enabled: bool = False, ttl_seconds: int = MMR_TTL_SECONDS,
                 debug_dump: bool = False):
        super().__init__()
        # Writes the next raw TRN payload to logs/ once, then clears itself.
        self._debug_dump = bool(debug_dump)
        self._enabled = bool(enabled)
        self._ttl = max(60, int(ttl_seconds))
        self._cache: dict = load_mmr_cache()
        # Steam negative-cache entries from before the SteamID lookup fix were
        # likely false negatives (display-name 404s). Drop them once at startup
        # so we re-fetch with the SteamID instead of waiting out the 10 min TTL.
        steam_nf = [k for k, v in self._cache.items()
                    if isinstance(v, dict) and v.get("not_found") and k.startswith("Steam|")]
        if steam_nf:
            for k in steam_nf:
                del self._cache[k]
            save_mmr_cache(self._cache)
            mmr_log(f"purged {len(steam_nf)} stale Steam not_found entries (re-fetch with SteamID)")
        self._cache_lock = threading.Lock()
        self._queue: "queue.Queue[tuple[str, str, str]]" = queue.Queue()
        self._inflight: set[str] = set()
        # Per-key retry counter for transient failures. Cleared on success or
        # once MMR_MAX_RETRIES is exceeded. Keys stay in _inflight while a
        # retry is pending so concurrent enqueues dedupe correctly.
        self._retry_count: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="MMRFetcher",
        )
        # Defer curl_cffi import: the module is optional. If it's missing, we
        # disable network access but still serve cached data.
        try:
            from curl_cffi import requests as _curl_requests  # noqa
            self._requests = _curl_requests
            mmr_log(f"init enabled={self._enabled} ttl={self._ttl}s "
                    f"cache_entries={len(self._cache)} curl_cffi=ok")
        except ImportError as e:
            self._requests = None
            mmr_log(f"init curl_cffi MISSING ({e}). Run: "
                    f"python -m pip install -r requirements.txt")

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()
            mmr_log("worker thread started")

    def stop(self) -> None:
        self._stop.set()

    def set_enabled(self, on: bool) -> None:
        prev = self._enabled
        self._enabled = bool(on)
        if prev != self._enabled:
            mmr_log(f"set_enabled {prev} -> {self._enabled} "
                    f"(curl_cffi_loaded={self._requests is not None})")

    def is_enabled(self) -> bool:
        return self._enabled and self._requests is not None

    def get(self, key: str) -> Optional[dict]:
        with self._cache_lock:
            entry = self._cache.get(key)
            return dict(entry) if entry else None

    def _is_stale(self, entry: Optional[dict]) -> bool:
        if not entry:
            return True
        ts = entry.get("fetched_at")
        if not isinstance(ts, str):
            return True
        try:
            t = datetime.fromisoformat(ts)
        except ValueError:
            return True
        age = (datetime.now(timezone.utc) - t).total_seconds()
        return age >= self._ttl

    def enqueue(self, primary_id: str, name: str, force: bool = False) -> None:
        """Queue a refresh for this opponent. Skips if disabled or already
        in-flight; also skips on cache-fresh unless ``force=True``. Use force
        for after-match self refresh, where we want the absolute latest TRN
        has even if our local cache is technically still warm."""
        key = player_key(primary_id)
        if not self._enabled:
            mmr_log(f"enqueue skip {key!r} ({name!r}): disabled")
            return
        if self._requests is None:
            mmr_log(f"enqueue skip {key!r} ({name!r}): curl_cffi missing")
            return
        if key in self._inflight:
            mmr_log(f"enqueue skip {key!r} ({name!r}): already in-flight")
            return
        if not force:
            with self._cache_lock:
                entry = self._cache.get(key)
            if not self._is_stale(entry):
                mmr_log(f"enqueue skip {key!r} ({name!r}): cache fresh "
                        f"(fetched_at={entry.get('fetched_at')!r})")
                return
        handle = mmr_lookup_handle(primary_id, name)
        if handle is None:
            mmr_log(f"enqueue skip {key!r} ({name!r}): unsupported platform "
                    f"or missing name")
            return
        plat, ident = handle
        self._inflight.add(key)
        self._queue.put((key, plat, ident))
        mmr_log(f"enqueue{' [forced]' if force else ''} {key!r} -> "
                f"{plat}/{ident!r} (queue size={self._queue.qsize()})")

    def enqueue_roster(self, roster: list[dict]) -> None:
        """Convenience: queue every player in a match roster."""
        mmr_log(f"enqueue_roster: {len(roster)} player(s) "
                f"(enabled={self._enabled}, curl_cffi={self._requests is not None})")
        for p in roster:
            pid = p.get("primaryId") or p.get("key")
            if not pid:
                mmr_log(f"  skip: no primaryId/key for {p.get('name')!r}")
                continue
            self.enqueue(pid, p.get("name") or "")

    def _worker(self) -> None:
        last_request = 0.0
        while not self._stop.is_set():
            try:
                key, plat, ident = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            since = time.monotonic() - last_request
            if since < MMR_FETCH_INTERVAL:
                wait = MMR_FETCH_INTERVAL - since
                mmr_log(f"throttle wait {wait:.2f}s before {key!r}")
                self._stop.wait(wait)
                if self._stop.is_set():
                    break
            try:
                self._fetch_one(key, plat, ident)
                self._retry_count.pop(key, None)
                self._inflight.discard(key)
            except Exception as e:
                mmr_log(f"{key!r} fetch FAILED: {type(e).__name__}: {e}")
                n = self._retry_count.get(key, 0) + 1
                if n <= MMR_MAX_RETRIES:
                    self._retry_count[key] = n
                    delay = MMR_RETRY_BACKOFF_S * n
                    mmr_log(f"  retry {n}/{MMR_MAX_RETRIES} scheduled in {delay:.1f}s")
                    # Keep _inflight set so concurrent enqueues from another
                    # on_initialized batch dedupe instead of double-fetching.
                    t = threading.Timer(
                        delay,
                        lambda k=key, p=plat, i=ident: self._queue.put((k, p, i)),
                    )
                    t.daemon = True
                    t.start()
                else:
                    self._retry_count.pop(key, None)
                    self._inflight.discard(key)
                    mmr_log(f"  giving up on {key!r} after {MMR_MAX_RETRIES} retries")
            last_request = time.monotonic()

    def _fetch_one(self, key: str, plat: str, ident: str) -> None:  # noqa: C901
        if self._requests is None:
            mmr_log(f"{key!r} fetch aborted: curl_cffi missing")
            return
        url = self._BASE_URL.format(plat=plat, ident=ident)
        mmr_log(f"GET {url}")
        t0 = time.monotonic()
        r = self._requests.get(url, headers=self._HEADERS,
                               impersonate="chrome120", timeout=15)
        dt = (time.monotonic() - t0) * 1000
        mmr_log(f"  -> HTTP {r.status_code} in {dt:.0f}ms ({len(r.content)} bytes)")
        if r.status_code == 404:
            with self._cache_lock:
                self._cache[key] = {
                    "fetched_at": now_iso(),
                    "not_found": True,
                    "handle": ident,
                }
                save_mmr_cache(self._cache)
            mmr_log(f"  {key!r} NOT FOUND (cached negative)")
            self.updated.emit(key)
            return
        if r.status_code != 200:
            mmr_log(f"  {key!r} HTTP {r.status_code}: {r.text[:200]!r}")
            # Server-side hiccups (TRN overloaded, Cloudflare rate-limit) are
            # often transient — raise so the worker retries with backoff.
            if r.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {r.status_code}")
            return
        try:
            payload = r.json()
        except ValueError as e:
            mmr_log(f"  {key!r} bad JSON: {e}")
            return
        data = (payload or {}).get("data")
        if not isinstance(data, dict):
            mmr_log(f"  {key!r} no .data in response")
            return
        # One raw payload on request, so what TRN actually sends can be read
        # rather than assumed — the fields we rely on are undocumented.
        if self._debug_dump:
            self._debug_dump = False
            try:
                TRN_PAYLOAD_PATH.write_text(
                    json.dumps(payload, indent=2)[:2_000_000], encoding="utf-8")
                mmr_log(f"  raw payload written to {TRN_PAYLOAD_PATH.name}")
            except OSError as e:
                mmr_log(f"  could not write raw payload: {e}")
        entry = parse_trn_payload(data)
        with self._cache_lock:
            self._cache[key] = entry
            save_mmr_cache(self._cache)
        best = entry.get("best") or {}
        pl = entry.get("playlists") or {}
        pl_str = " ".join(
            f"{lbl}={(pl.get(lbl) or {}).get('mmr', '—')}"
            for lbl in RANKED_PLAYLISTS
        )
        mmr_log(f"  {key!r} OK handle={entry.get('handle')!r} {pl_str} "
                f"best={best.get('mmr')}@{best.get('playlist')} "
                f"trn_lastUpdated={entry.get('lastUpdated')}")
        self.updated.emit(key)


def attribute_mmr_points(playlist: str, snapshots: list[dict],
                         matches: list[dict],
                         grace_seconds: int = 120,
                         window: int = 30) -> list[dict]:
    """Walk consecutive snapshot pairs and attribute the cumulative MMR delta
    in each interval to the matches that ended within it.

    Per-game step is *derived* from the data:
      - When W != L:  step = abs(D) / abs(W - L)  (signed by outcome)
      - When W == L:  use the rolling median of past intervals' steps; if no
                      such history exists yet, bootstrap to 10
      - When W + L == 0:  the user played outside our session — plot the
                          snapshot transition as a single "snap" point and
                          move on, no per-game attribution

    Output: list of {"x": iso_ts, "mmr": int, "marker": "W"|"L"|"snap"} in
    chronological order, capped to the last ``window`` items.
    """
    points: list[dict] = []
    if not snapshots:
        return points
    relevant_snaps = [s for s in snapshots if (s.get("playlists") or {}).get(playlist) is not None]
    if not relevant_snaps:
        return points

    pl_matches_sorted = sorted(
        [m for m in matches if match_playlist(m) == playlist
         and isinstance(m.get("endedAt"), str)],
        key=lambda m: m["endedAt"],
    )

    s0 = relevant_snaps[0]
    cumulative = (s0.get("playlists") or {}).get(playlist)
    points.append({"x": s0.get("ts"), "mmr": cumulative, "marker": "snap"})

    past_steps: list[float] = []
    grace = grace_seconds

    for s_prev, s_cur in zip(relevant_snaps, relevant_snaps[1:]):
        mmr_prev = (s_prev.get("playlists") or {}).get(playlist)
        mmr_cur = (s_cur.get("playlists") or {}).get(playlist)
        if mmr_prev is None or mmr_cur is None:
            cumulative = mmr_cur if mmr_cur is not None else cumulative
            points.append({"x": s_cur.get("ts"), "mmr": cumulative, "marker": "snap"})
            continue
        delta = mmr_cur - mmr_prev

        t_prev = parse_iso(s_prev.get("ts"))
        t_cur = parse_iso(s_cur.get("ts"))
        if t_prev is None or t_cur is None:
            cumulative = mmr_cur
            points.append({"x": s_cur.get("ts"), "mmr": cumulative, "marker": "snap"})
            continue
        cutoff = t_cur + timedelta(seconds=grace)
        interval_matches = []
        for m in pl_matches_sorted:
            t_m = parse_iso(m.get("endedAt"))
            if t_m is None:
                continue
            if t_prev <= t_m <= cutoff:
                interval_matches.append(m)

        if not interval_matches:
            if delta == 0:
                continue
            cumulative = mmr_cur
            points.append({"x": s_cur.get("ts"), "mmr": cumulative, "marker": "snap"})
            continue

        wins = sum(1 for m in interval_matches if m.get("result") == "W")
        losses = len(interval_matches) - wins

        if wins != losses:
            step = abs(delta) / abs(wins - losses)
            past_steps.append(step)
        else:
            if past_steps:
                sorted_steps = sorted(past_steps)
                step = sorted_steps[len(sorted_steps) // 2]
            else:
                step = 10.0

        for m in interval_matches:
            sign = 1 if m.get("result") == "W" else -1
            cumulative = cumulative + sign * step
            points.append({
                "x": m.get("endedAt"),
                "mmr": int(round(cumulative)),
                "marker": m.get("result") or "snap",
            })

        if points[-1]["mmr"] != mmr_cur:
            points[-1] = {**points[-1], "mmr": mmr_cur}
        cumulative = mmr_cur

    return points[-window:] if window > 0 else points
