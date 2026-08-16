"""Application entrypoint. Wires modules together and runs the Qt event loop."""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from datetime import datetime

from PySide6.QtCore import QLockFile, QObject, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import colors, glass, hero_data, statsapi_ini
from .applog import capture_console, mmr_log, set_hotkey_log_enabled
from .config import load_config, save_config
from .hotkey import HotkeyManager, MenuHotkeyListener, capture_next_input, is_rl_focused
from .mmr import MMR_CATEGORIES, MMRClient, RANKED_PLAYLISTS, append_mmr_history, load_mmr_history
from .overlay import Overlay
from .paths import (
    DATA_DIR, MATCHES_PATH, MMR_HISTORY_PATH, MY_MMR_LOG_PATH, PLAYERS_PATH,
    now_iso,
)
from .glass_h2h import render_idle_pixmap
from .glass_hero import render_h2h_pixmap
from .glass_screens import (
    render_menu_pixmap,
    render_session_pixmap,
    render_summary_pixmap,
)
from .session_stats import MatchStats, SessionStats
from .stats_client import StatsClient
from .storage import (
    append_match,
    load_matches,
    load_players,
    playlist_from_player_count,
    save_players,
    update_players_cache,
)
from .tray import make_tray_icon
from .graph import render_graph_pixmap


# One label for every surface that reports the disabled Stats API (overlay,
# tray tooltip, tray menu) so a wording change can't go stale in one of them.
# The full diagnosis — file paths and the exact cause — goes to stderr and the
# tray balloon instead; it's far too long for a status line.
STATS_API_DISABLED = "Stats API disabled in Rocket League"
# Overlay variant: the painted status card takes the fix as parts so the
# setting renders as a chip. The tray tooltip and menu use the bare label above.
STATS_API_DISABLED_DETAIL = [("text", "Set"), ("code", "PacketSendRate=2"),
                             ("text", "— README step 4")]


def main():
    # Before anything prints: under pythonw both streams are None and every
    # print() is silently dropped, which is why the app's diagnostics have
    # never reached anyone launching from start.bat.
    capture_console()
    if hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # Single-instance guard. Two processes racing on data/*.json.tmp produces
    # WinError 32 (file in use) on every save — keep one launch authoritative.
    # QLockFile auto-cleans stale locks if the previous process crashed.
    instance_lock = QLockFile(str(DATA_DIR / ".rl-h2h.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(0):
        print("[singleton] another rl-h2h instance is already running; exiting.",
              file=sys.stderr)
        return

    cfg = load_config()
    colors.apply_overrides(cfg)
    # Flip the hotkey diagnostic log before any HotkeyManager logs its bindings.
    set_hotkey_log_enabled(bool(cfg.get("hotkey_debug_log", False)))
    players_db = load_players()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # A game patch can reset PacketSendRate to 0, which kills the stats socket
    # and leaves the overlay permanently blank with nothing to explain why.
    # Say so up front rather than waiting forever for a connection.
    ini_problem = statsapi_ini.diagnose()
    if ini_problem:
        print(f"[statsapi] {ini_problem}", file=sys.stderr)

    startup_status = ((STATS_API_DISABLED, STATS_API_DISABLED_DETAIL, True)
                      if ini_problem
                      else ("Waiting for Rocket League…", None, False))

    overlay = Overlay(cfg)
    stats = StatsClient(cfg["host"], cfg["port"],
                        api_dump_enabled=bool(cfg.get("api_debug_dump", False)))
    session = SessionStats(recent_size=cfg.get("recent_size", 5))
    match_stats = MatchStats()
    mmr_client = MMRClient(enabled=bool(cfg.get("mmr_enabled", False)))
    mmr_client.start()
    hotkey_h2h = HotkeyManager(cfg["hotkeys"])
    hotkey_session = HotkeyManager(cfg.get("session_hotkeys") or [])
    hotkey_expand = HotkeyManager(cfg.get("expand_hotkeys") or [])
    hotkey_cycle = HotkeyManager(cfg.get("cycle_hotkeys") or [])
    # In-game settings menu: F5 toggles, arrows navigate, Enter selects.
    # Esc only matters during rebind capture (handled inside capture_next_input).
    # MenuHotkeyListener suppresses these keys (Windows) so they don't also
    # reach Rocket League's menu while ours is open.
    hotkey_menu = MenuHotkeyListener(
        menu_key_cb=lambda: cfg.get("menu_hotkey") or "f5",
        is_visible_cb=lambda: state["menu_visible"],
        is_capturing_cb=lambda: state["menu_capture"] is not None,
        # Same focus rule as the overlay: the menu key is Rocket League's while
        # the game is up, and everyone else's when it isn't.
        is_allowed_cb=lambda: (not cfg.get("require_rl_focus", True)
                               or is_rl_focused()),
    )

    # Sanitize the persisted category once at startup — guards against a hand-edited
    # config setting (e.g. "1V1" instead of "1v1"). Falls back to "best".
    if cfg.get("mmr_category") not in MMR_CATEGORIES:
        cfg["mmr_category"] = "best"

    state = {
        "in_match": False,
        "stats_connected": False,
        "h2h_held": False,
        "session_held": False,
        "summary_visible": False,
        "summary_payload": None,
        # Self MMR either side of a match, for the summary's arrow. Only
        # shown once they differ — the post-match poll lands minutes later.
        "mmr_before": None,
        "mmr_after": None,
        # (title, detail parts, offline) for the painted status card.
        "h2h_status": startup_status,
        "mmr_db": {},
        "self_id": None,
        "h2h_expanded": bool(cfg.get("h2h_default_expanded", False)),
        "session_view": cfg.get("session_view", "session"),
        "graph_playlist": cfg.get("graph_playlist", "2v2"),
        "roster": [],
        "my_team": 0,
        "arena": "",
        "team_colors": {},
        # In-game settings menu state.
        "menu_visible": False,
        "menu_index": 0,
        "menu_capture": None,  # cfg key being rebound, or None
    }
    if state["session_view"] not in ("session", "graph"):
        state["session_view"] = "session"
    if state["graph_playlist"] not in RANKED_PLAYLISTS:
        state["graph_playlist"] = "2v2"

    def _any_visible() -> bool:
        return (state["h2h_held"] or state["session_held"]
                or state["summary_visible"])

    def update_overlay():
        # Settings menu wins over everything and bypasses the focus check —
        # the user opened it deliberately and may want to reach it from the
        # desktop.
        if state["menu_visible"]:
            overlay.set_pixmap(render_menu_pixmap(
                _menu_rows(), state["menu_index"], state["menu_capture"] is not None,
                cfg, menu_key=cfg.get("menu_hotkey") or "f5",
                status=_menu_status(),
                width=cfg["width"] - glass.CARD_PAD_X * 2,
                dpr=overlay.devicePixelRatioF(),
            ))
            overlay.show()
            overlay.raise_()
            return
        if not _any_visible():
            focus_timer.stop()
            overlay.hide()
            return
        if cfg.get("require_rl_focus", True) and not is_rl_focused():
            overlay.hide()
            return
        # Priority: held keys win over the auto-popup. Session > H2H > summary.
        if state["session_held"]:
            if state["session_view"] == "graph":
                _ensure_graph_data_loaded()
                pix = render_graph_pixmap(
                    state["graph_playlist"],
                    mmr_history_cache["snapshots"],
                    mmr_history_cache["matches"],
                    cfg,
                    # Glass chrome pads 18px each side, and the pixmap is
                    # painted at the overlay's own device ratio so text stays
                    # sharp at 125%/150% Windows scaling.
                    canvas_width=cfg["width"] - glass.CARD_PAD_X * 2,
                    dpr=overlay.devicePixelRatioF(),
                )
                overlay.set_pixmap(pix)
            else:
                if mmr_client.is_enabled():
                    _ensure_graph_data_loaded()
                overlay.set_pixmap(render_session_pixmap(
                    session, cfg,
                    mmr_delta=_session_mmr_delta(),
                    width=cfg["width"] - glass.CARD_PAD_X * 2,
                    dpr=overlay.devicePixelRatioF(),
                ))
        elif state["h2h_held"] and state["in_match"]:
            # Expanded H2H adds current-match stats (saves/shots/demos/etc.).
            # Session aggregates live behind the session-hotkey view instead —
            # they aren't actionable mid-match.
            # The hero block needs the MMR history for its sparkline and
            # session delta — the same cache the graph view reads, so this
            # shares its mtime-checked load rather than re-parsing.
            if mmr_client.is_enabled():
                _ensure_graph_data_loaded()
            overlay.set_pixmap(render_h2h_pixmap(
                state["roster"], state["my_team"], state["arena"],
                players_db, state["team_colors"], cfg,
                self_id=state.get("self_id") or cfg.get("self_player_id"),
                mmr_db=state.get("mmr_db"),
                mmr_category=cfg.get("mmr_category", "best"),
                mmr_enabled=mmr_client.is_enabled(),
                match_stats=match_stats if state["h2h_expanded"] else None,
                expanded=state["h2h_expanded"],
                session=session,
                snapshots=mmr_history_cache["snapshots"],
                matches=mmr_history_cache["matches"],
                session_started_iso=session.started_at.isoformat(),
                width=cfg["width"] - glass.CARD_PAD_X * 2,
                dpr=overlay.devicePixelRatioF(),
            ))
        elif state["h2h_held"] and not state["stats_connected"]:
            # Held while the feed is down: explain why, since h2h_status holds the
            # reason and used to be rendered only in the in_match branch above —
            # unreachable exactly when disconnected, which is what made the key
            # look dead. Deliberately NOT shown while connected: "waiting for a
            # match" is normal, and a panel on every menu press is just noise.
            title, detail, offline = state["h2h_status"]
            overlay.set_pixmap(render_idle_pixmap(
                cfg, title, detail=detail, offline=offline,
                width=cfg["width"] - glass.CARD_PAD_X * 2,
                dpr=overlay.devicePixelRatioF(),
            ))
        elif state["summary_visible"] and state["summary_payload"]:
            payload, ms = state["summary_payload"]
            won = payload.get("winner") == payload.get("myTeam")
            overlay.set_pixmap(
                render_summary_pixmap(
                    payload, ms, cfg, players_db=players_db,
                    mmr_before=state.get("mmr_before"),
                    mmr_after=state.get("mmr_after"),
                    width=cfg["width"] - glass.CARD_PAD_X * 2,
                    dpr=overlay.devicePixelRatioF(),
                ),
                tint=glass.WIN if won else glass.LOSS,
            )
        else:
            overlay.hide()
            return
        overlay.show()
        overlay.raise_()

    focus_timer = QTimer()
    focus_timer.setInterval(250)
    focus_timer.timeout.connect(update_overlay)

    def on_h2h_pressed():
        state["h2h_held"] = True
        focus_timer.start()
        update_overlay()

    def on_h2h_released():
        state["h2h_held"] = False
        if not _any_visible():
            focus_timer.stop()
        update_overlay()

    def on_session_pressed():
        state["session_held"] = True
        focus_timer.start()
        update_overlay()

    def on_session_released():
        state["session_held"] = False
        if not _any_visible():
            focus_timer.stop()
        update_overlay()

    summary_timer = QTimer()
    summary_timer.setSingleShot(True)

    def hide_summary():
        summary_timer.stop()
        state["summary_visible"] = False
        if not _any_visible():
            focus_timer.stop()
        update_overlay()

    summary_timer.timeout.connect(hide_summary)

    def _self_playlist() -> str:
        """The playlist the hero block is talking about — the category when it
        names one, otherwise whichever your best rank is in."""
        cat = cfg.get("mmr_category", "best")
        if cat not in ("best", "peak"):
            return cat
        entry = (state.get("mmr_db") or {}).get(
            state.get("self_id") or cfg.get("self_player_id"))
        return ((entry or {}).get("best") or {}).get("playlist") or ""

    def _session_mmr_delta():
        if not mmr_client.is_enabled():
            return None
        pl = _self_playlist()
        if not pl:
            return None
        return hero_data.session_mmr_delta(
            mmr_history_cache["snapshots"], pl, session.started_at.isoformat())

    def _menu_status() -> str:
        """Link state, so the menu also answers 'is it working?'.

        Deliberately not the VERSION file: the updater stores a commit SHA
        there, which reads as a meaningless UUID in a settings header."""
        return "connected" if state["stats_connected"] else "not connected"

    def rerender_h2h() -> None:
        """Re-run render_html against the saved roster — used both when a match
        starts and whenever fresh MMR data lands or the user cycles category."""
        if not state["roster"]:
            mmr_log("rerender_h2h: skip (no roster)")
            return
        self_id = cfg.get("self_player_id")
        # Snapshot the cache once per render so all rows see a consistent view
        # even if the worker writes mid-build.
        mmr_db = {p["key"]: mmr_client.get(p["key"]) for p in state["roster"]}
        if mmr_client.is_enabled():
            summary_parts = []
            for p in state["roster"]:
                e = mmr_db.get(p["key"])
                if e is None:
                    summary_parts.append(f"{p['name']}=…")
                elif e.get("not_found"):
                    summary_parts.append(f"{p['name']}=NF")
                else:
                    best = (e.get("best") or {})
                    summary_parts.append(
                        f"{p['name']}={best.get('mmr')}@{best.get('playlist')}"
                    )
            mmr_log(f"rerender_h2h: cat={cfg.get('mmr_category','best')!r} "
                    f"rows=[{', '.join(summary_parts)}]")
        # The card is painted at draw time now, so this only has to publish a
        # consistent MMR snapshot for the painter to read. h2h_status is left to
        # the status messages (waiting / disconnected / Stats API disabled).
        state["mmr_db"] = mmr_db
        state["self_id"] = self_id

    def _ensure_graph_data_loaded() -> None:
        """First call (or after `dirty` is set) parses the on-disk files into
        the in-memory cache. Subsequent calls re-stat both files and reparse
        only when their mtime changed — cheap, and means polling-loop writes
        appear in the next graph render without extra plumbing."""
        try:
            hist_mtime = MMR_HISTORY_PATH.stat().st_mtime if MMR_HISTORY_PATH.exists() else 0.0
        except OSError:
            hist_mtime = 0.0
        try:
            match_mtime = MATCHES_PATH.stat().st_mtime if MATCHES_PATH.exists() else 0.0
        except OSError:
            match_mtime = 0.0
        need_load = (
            not mmr_history_cache["loaded"]
            or mmr_history_cache["dirty"]
            or hist_mtime != mmr_history_cache["mtime_history"]
            or match_mtime != mmr_history_cache["mtime_matches"]
        )
        if not need_load:
            return
        mmr_history_cache["snapshots"] = load_mmr_history()
        mmr_history_cache["matches"] = load_matches()
        mmr_history_cache["mtime_history"] = hist_mtime
        mmr_history_cache["mtime_matches"] = match_mtime
        mmr_history_cache["loaded"] = True
        mmr_history_cache["dirty"] = False

    # Post-match self-MMR polling state. The token is bumped on each new poll
    # so callbacks scheduled by an earlier poll can self-cancel — important
    # because back-to-back matches would otherwise produce overlapping polls
    # whose snapshots interleave in the attribution algorithm.
    poll_state = {"token": 0, "baseline": None}

    def start_post_match_mmr_poll(self_player: dict) -> None:
        pid = self_player.get("primaryId") or self_player["key"]
        name = self_player.get("name") or ""
        self_id = cfg.get("self_player_id")
        baseline_entry = mmr_client.get(self_id) if self_id else None
        baseline = (baseline_entry or {}).get("lastUpdated") or ""

        poll_state["token"] += 1
        poll_state["baseline"] = baseline
        my_token = poll_state["token"]
        delays_ms = [0, 120_000, 240_000, 360_000, 480_000, 600_000]
        mmr_log(f"poll: scheduled for self={self_id!r} "
                f"baseline_lastUpdated={baseline!r}")

        def attempt(i: int):
            if poll_state["token"] != my_token:
                mmr_log(f"poll: superseded (token {my_token} != "
                        f"{poll_state['token']}); aborting")
                return
            cur = mmr_client.get(self_id) if self_id else None
            cur_last = (cur or {}).get("lastUpdated") or ""
            if cur_last and baseline and cur_last > baseline:
                mmr_log(f"poll: TRN advanced to {cur_last!r} after {i} attempt(s); stopping")
                return
            if i >= len(delays_ms):
                mmr_log("poll: budget exhausted (10 min, 6 attempts)")
                return
            mmr_log(f"poll: attempt #{i+1}/{len(delays_ms)} (force-refresh)")
            mmr_client.enqueue(pid, name, force=True)
            QTimer.singleShot(120_000, lambda: attempt(i + 1))

        attempt(0)

    def on_initialized(payload: dict):
        state["in_match"] = True
        state["mmr_before"] = hero_data.playlist_mmr(
            (state.get("mmr_db") or {}).get(
                state.get("self_id") or cfg.get("self_player_id")),
            cfg.get("mmr_category", "best"))
        state["mmr_after"] = None
        hide_summary()  # next match starting → drop any in-flight post-match popup
        # Auto-detect self in 1v1: only one teammate on my side = me. Persist to config.
        if not cfg.get("self_player_id"):
            mt = payload["myTeam"]
            same_side = [p for p in payload["players"] if p["team"] == mt]
            if len(same_side) == 1:
                cfg["self_player_id"] = same_side[0]["key"]
                print(f"[self] detected self={same_side[0]['name']!r} "
                      f"({same_side[0]['key']}) — saved to config", file=sys.stderr)
                save_config(cfg)
        # Resolve self_name for session-stat attribution (events only carry Name).
        self_id = cfg.get("self_player_id")
        if self_id:
            for p in payload["players"]:
                if p["key"] == self_id:
                    session.self_name = p["name"]
                    break
        # Reset per-match aggregator using the now-known self_name.
        match_stats.reset(self_name=session.self_name)
        # Persist roster bits we need to re-render asynchronously when MMR
        # data trickles in (or when the user toggles category via the cycle key).
        state["roster"] = payload["players"]
        state["my_team"] = payload["myTeam"]
        state["arena"] = payload["arena"]
        state["team_colors"] = payload.get("teamColors") or {}
        # Self IS included now — we want to see our own MMR in the YOU row,
        # and the post-match refresh in on_ended only works if self has been
        # enqueued at least once. Cached entries serve instantly, fresh ones
        # arrive over the next ~1s per player.
        if mmr_client.is_enabled():
            mmr_log(f"on_initialized: enqueueing {len(payload['players'])} player(s) "
                    f"(including self={self_id!r})")
            mmr_client.enqueue_roster(payload["players"])
        else:
            mmr_log(f"on_initialized: MMR disabled, skipping enqueue "
                    f"(enabled_flag={cfg.get('mmr_enabled', False)}, "
                    f"curl_cffi_loaded={mmr_client._requests is not None})")
        rerender_h2h()
        update_overlay()
        print(f"[match] initialized arena={payload['arena']} myTeam={payload['myTeam']}")

    def on_ended(payload: dict):
        state["in_match"] = False
        session.on_match_ended(payload)
        i_won = payload["winner"] == payload["myTeam"]
        record = {
            "matchGuid": payload.get("matchGuid"),
            "endedAt": now_iso(),
            "arena": payload["arena"],
            "myTeam": payload["myTeam"],
            "winner": payload["winner"],
            "result": "W" if i_won else "L",
            "score": payload.get("score"),
            "playlist": playlist_from_player_count(len(payload["players"])),
            "players": payload["players"],
        }
        # players.json is the sole source of truth for the H2H record;
        # matches.jsonl only feeds the MMR graph. Persist the record first so a
        # failed append can never cost us the head-to-head history.
        update_players_cache(players_db, record)
        save_players(players_db)
        append_match(record)
        score_str = ""
        if isinstance(record.get("score"), list) and len(record["score"]) == 2:
            mt = payload["myTeam"]
            score_str = f" ({record['score'][mt]}–{record['score'][1 - mt]})"
        print(f"[match] ended {'WIN' if i_won else 'LOSS'}{score_str}")
        # Force-refresh self MMR after the match — TRN's edge cache is sticky
        # (we observed ~8 min of staleness in practice), so we poll every 2 min
        # for up to 10 min and stop early once TRN's lastUpdated actually rolls
        # past where we started. See start_post_match_mmr_poll() below.
        self_id = cfg.get("self_player_id")
        if mmr_client.is_enabled() and self_id:
            self_player = next((p for p in payload["players"]
                                if p["key"] == self_id), None)
            if self_player:
                start_post_match_mmr_poll(self_player)
        # Auto-popup the post-match summary card. Stays up until the next match
        # starts (match_initialized) or the user leaves to the menu (match_destroyed),
        # with a 30s safety net for cases where neither event fires.
        if cfg.get("show_match_summary", True):
            state["summary_payload"] = (payload, match_stats)
            state["mmr_after"] = hero_data.playlist_mmr(
                (state.get("mmr_db") or {}).get(
                    state.get("self_id") or cfg.get("self_player_id")),
                cfg.get("mmr_category", "best"))
            state["summary_visible"] = True
            focus_timer.start()
            update_overlay()
            summary_timer.start(int(cfg.get("match_summary_seconds", 30)) * 1000)

    def on_destroyed():
        state["in_match"] = False
        hide_summary()  # leaving the match → drop the post-match popup
        state["h2h_status"] = ("Waiting for next match…", None, False)
        update_overlay()

    def on_status(connected: bool):
        # Recorded before the in-match early return, or the flag goes stale for
        # the whole match and update_overlay would misjudge the idle panel.
        state["stats_connected"] = connected
        if state["in_match"]:
            return
        if connected:
            state["h2h_status"] = ("Connected — waiting for match…", None, False)
        else:
            # Name the cause when we know it — a generic "is it enabled?" is
            # unhelpful once we've read the .ini and found it switched off.
            state["h2h_status"] = (
                (STATS_API_DISABLED, STATS_API_DISABLED_DETAIL, True) if ini_problem
                else ("Disconnected — is RL running with the Stats API enabled?",
                      None, True))
        update_overlay()

    def on_event_for_session(event: str, data: dict):
        # Only count events while a real match is in progress (between match_initialized
        # and match_ended/match_destroyed). Excludes free practice / training / menus.
        if state["in_match"]:
            session.on_event(event, data)
            match_stats.on_event(event, data)

    stats.match_initialized.connect(on_initialized)
    stats.match_ended.connect(on_ended)
    stats.match_destroyed.connect(on_destroyed)
    stats.connection_status.connect(on_status)
    stats.event_seen.connect(on_event_for_session)
    hotkey_h2h.pressed.connect(on_h2h_pressed)
    hotkey_h2h.released.connect(on_h2h_released)
    hotkey_session.pressed.connect(on_session_pressed)
    hotkey_session.released.connect(on_session_released)

    def toggle_expand():
        # Context-sensitive: when the session key is held, the expand key swaps
        # the session sub-view between session card and graph. Otherwise (Tab
        # held or nothing held), it keeps the existing H2H-expand toggle behavior.
        if state["session_held"]:
            nxt = "graph" if state["session_view"] == "session" else "session"
            state["session_view"] = nxt
            cfg["session_view"] = nxt
            save_config(cfg)
            print(f"[overlay] session_view={nxt}", file=sys.stderr)
        else:
            state["h2h_expanded"] = not state["h2h_expanded"]
            cfg["h2h_default_expanded"] = state["h2h_expanded"]
            save_config(cfg)
            print(f"[overlay] expanded={state['h2h_expanded']}", file=sys.stderr)
        update_overlay()

    hotkey_expand.pressed.connect(toggle_expand)

    def cycle_mmr_category():
        # Context-sensitive: while the graph view is showing (session-key held
        # + session_view=="graph"), the cycle key cycles the graph's playlist
        # instead of the H2H MMR category. Same key, different role per context
        # — same idea as the expand key (expand H2H vs swap session subview).
        if state["session_held"] and state["session_view"] == "graph":
            cur_pl = state["graph_playlist"]
            i = RANKED_PLAYLISTS.index(cur_pl) if cur_pl in RANKED_PLAYLISTS else -1
            nxt_pl = RANKED_PLAYLISTS[(i + 1) % len(RANKED_PLAYLISTS)]
            state["graph_playlist"] = nxt_pl
            cfg["graph_playlist"] = nxt_pl
            save_config(cfg)
            mmr_log(f"cycle_graph_playlist: {cur_pl!r} -> {nxt_pl!r}")
            update_overlay()
            return
        cur = cfg.get("mmr_category", "best")
        try:
            i = MMR_CATEGORIES.index(cur)
        except ValueError:
            i = -1
        nxt = MMR_CATEGORIES[(i + 1) % len(MMR_CATEGORIES)]
        cfg["mmr_category"] = nxt
        save_config(cfg)
        mmr_log(f"cycle_category: {cur!r} -> {nxt!r}")
        rerender_h2h()
        update_overlay()

    hotkey_cycle.pressed.connect(cycle_mmr_category)

    # ── Settings menu ────────────────────────────────────────────────────────
    # Actions that can be rebound from the menu, in display order. Each entry
    # is (cfg_key, display_label, manager). The cfg_key is also the row id
    # used in capture state.
    rebindable = (
        ("hotkeys",         "H2H hold",  hotkey_h2h),
        ("session_hotkeys", "Session",   hotkey_session),
        ("expand_hotkeys",  "Expand",    hotkey_expand),
        ("cycle_hotkeys",   "Cycle MMR", hotkey_cycle),
    )

    def _split_bindings(bindings: list) -> tuple:
        kb = next((b for b in bindings if not b.startswith("pad_")), None)
        pad = next((b for b in bindings if b.startswith("pad_")), None)
        return kb, pad

    def _replace_binding_slot(current: list, new_name: str) -> list:
        """Replace either the kb slot or the pad slot, keeping the other.
        Order: keyboard first, gamepad second (matches default config)."""
        is_pad = new_name.startswith("pad_")
        kept = [b for b in current if b.startswith("pad_") != is_pad]
        return (kept + [new_name]) if is_pad else ([new_name] + kept)

    def _sync_tray_action(action, value: bool) -> None:
        """Mirror the value onto a QAction without re-firing its toggled handler."""
        if action is None or action.isChecked() == bool(value):
            return
        blocked = action.blockSignals(True)
        action.setChecked(bool(value))
        action.blockSignals(blocked)

    def apply_toggle(cfg_key: str, value: bool, on_applied=None) -> None:
        """Persist a bool config flag and run optional side effects."""
        cfg[cfg_key] = bool(value)
        save_config(cfg)
        if on_applied is not None:
            on_applied(bool(value))

    def apply_mmr_toggle(value: bool) -> None:
        def _side_effects(v: bool) -> None:
            mmr_client.set_enabled(v)
            mmr_log(f"toggle: enabled={v} in_match={state['in_match']} "
                    f"roster_size={len(state.get('roster') or [])}")
            if v and state["in_match"] and state["roster"]:
                mmr_log(f"  in-match enqueue {len(state['roster'])} player(s)")
                mmr_client.enqueue_roster(state["roster"])
            rerender_h2h()
            _sync_tray_action(mmr_action, v)
        apply_toggle("mmr_enabled", value, _side_effects)

    def apply_show_match_summary(value: bool) -> None:
        apply_toggle("show_match_summary", value)

    def apply_auto_update(value: bool) -> None:
        def _side_effects(v: bool) -> None:
            print(f"[update] auto_update={v}", file=sys.stderr)
            _sync_tray_action(auto_update_action, v)
        apply_toggle("auto_update", value, _side_effects)

    # Forward refs filled when the tray is built (may stay None if no tray).
    mmr_action = None
    auto_update_action = None

    # (label, cfg_key, default, apply_fn) for each toggle, in display order.
    toggleable = (
        ("MMR enabled",           "mmr_enabled",        False, apply_mmr_toggle),
        ("Auto match summary",    "show_match_summary", True,  apply_show_match_summary),
        ("Auto-update on launch", "auto_update",        False, apply_auto_update),
    )

    def _menu_rows() -> list:
        rows: list = [{"type": "header", "label": "TOGGLES"}]
        for label, cfg_key, default, apply_fn in toggleable:
            rows.append({"type": "toggle", "label": label,
                         "value": bool(cfg.get(cfg_key, default)),
                         "apply": apply_fn})
        rows.append({"type": "spacer"})
        rows.append({"type": "header", "label": "BINDINGS"})
        # Menu key first so it's findable even if someone rebinds it to
        # something obscure and then forgets.
        rows.append({"type": "binding", "label": "Menu (this)",
                     "kb": cfg.get("menu_hotkey") or "f5", "pad": None,
                     "action_key": "menu_hotkey"})
        for cfg_key, label, _mgr in rebindable:
            kb, pad = _split_bindings(cfg.get(cfg_key) or [])
            rows.append({"type": "binding", "label": label,
                         "kb": kb, "pad": pad, "action_key": cfg_key})
        return rows

    def _selectable_indices() -> list:
        return [i for i, r in enumerate(_menu_rows())
                if r["type"] in ("toggle", "binding")]

    def on_menu_toggle():
        # While capturing, F5 is just another key — let the capture listener
        # consume it. We short-circuit so we don't close the menu.
        if state["menu_capture"] is not None:
            return
        state["menu_visible"] = not state["menu_visible"]
        if state["menu_visible"]:
            state["menu_index"] = _selectable_indices()[0] if _selectable_indices() else 0
        update_overlay()

    def on_menu_navigate(direction: int) -> None:
        if not state["menu_visible"] or state["menu_capture"] is not None:
            return
        sel = _selectable_indices()
        if not sel:
            return
        try:
            i = sel.index(state["menu_index"])
        except ValueError:
            i = 0
        state["menu_index"] = sel[(i + direction) % len(sel)]
        update_overlay()

    # Bridge for the rebind-capture callback. capture_next_input invokes its
    # callback from a worker thread; we re-emit through a Qt signal so the
    # config write + UI refresh happen on the main thread.
    class _CaptureBridge(QObject):
        captured = Signal(object)
    capture_bridge = _CaptureBridge()

    def _on_captured(name):
        action_key = state["menu_capture"]
        state["menu_capture"] = None
        if action_key is None:
            update_overlay()
            return
        if name is None:
            mmr_log(f"menu rebind cancelled for {action_key!r}")
            update_overlay()
            return
        # Rebinding the menu hotkey itself: stored as a single string and
        # keyboard-only (a gamepad button would open the menu mid-game by
        # accident way too often).
        if action_key == "menu_hotkey":
            if name.startswith("pad_"):
                mmr_log(f"menu rebind ignored: {name!r} (menu key must be keyboard)")
                update_overlay()
                return
            cfg["menu_hotkey"] = name
            save_config(cfg)
            # MenuHotkeyListener reads cfg via its menu_key_cb on each event,
            # so the new key is picked up on the next press without a restart.
            mmr_log(f"menu rebind: menu_hotkey = {name!r}")
            update_overlay()
            return
        # Other actions: don't let them shadow the menu hotkey, or you lose
        # the only way back into the menu.
        if name == (cfg.get("menu_hotkey") or "f5"):
            mmr_log(f"menu rebind ignored: {name!r} is the menu hotkey")
            update_overlay()
            return
        current = list(cfg.get(action_key) or [])
        new_bindings = _replace_binding_slot(current, name)
        cfg[action_key] = new_bindings
        save_config(cfg)
        for cfg_key, _label, mgr in rebindable:
            if cfg_key == action_key:
                mgr.set_bindings(new_bindings)
                break
        mmr_log(f"menu rebind: {action_key} = {new_bindings}")
        update_overlay()

    capture_bridge.captured.connect(_on_captured)

    def on_menu_enter():
        if not state["menu_visible"] or state["menu_capture"] is not None:
            return
        rows = _menu_rows()
        if not (0 <= state["menu_index"] < len(rows)):
            return
        row = rows[state["menu_index"]]
        if row["type"] == "toggle":
            row["apply"](not row["value"])
            update_overlay()
        elif row["type"] == "binding":
            state["menu_capture"] = row["action_key"]
            update_overlay()  # show the "press…" hint
            capture_next_input(lambda n: capture_bridge.captured.emit(n))

    hotkey_menu.toggle.connect(on_menu_toggle)
    hotkey_menu.up.connect(lambda: on_menu_navigate(-1))
    hotkey_menu.down.connect(lambda: on_menu_navigate(+1))
    hotkey_menu.enter.connect(on_menu_enter)
    # ── End settings menu ────────────────────────────────────────────────────

    # Tracks the last self entry we logged so we can compute deltas (and skip
    # writes when nothing has changed). Seeded from disk cache below.
    last_self_log = {"playlists": {}, "lastUpdated": None}

    # Lazy cache for mmr_history.jsonl. We don't load at startup — the graph
    # view is opened by maybe 1% of users on any given session, so we pay the
    # parse cost only on first expand-from-session. The `dirty` flag is set by
    # _log_my_mmr after a history append, telling the graph render to reparse
    # before drawing. mtime-based invalidation also guards against external
    # edits.
    mmr_history_cache = {
        "loaded": False, "snapshots": [], "matches": [],
        "mtime_history": 0.0, "mtime_matches": 0.0, "dirty": False,
    }

    def _log_my_mmr(entry: dict):
        """Append one line per *meaningful* refresh to my_mmr.log: per-playlist
        MMR, deltas, and TRN's lastUpdated. Skips the write when no playlist
        moved AND TRN's snapshot hasn't rolled — those entries were just our
        force-refreshes hitting TRN's static edge cache and add no signal."""
        prev = last_self_log["playlists"]
        cur = entry.get("playlists") or {}
        last_updated = entry.get("lastUpdated") or "?"

        any_change = any(
            (cur.get(lbl) or {}).get("mmr") != (prev.get(lbl) or {}).get("mmr")
            for lbl in RANKED_PLAYLISTS
        )
        trn_rolled = last_updated != last_self_log["lastUpdated"]
        first_entry = not last_self_log["lastUpdated"]
        if not (any_change or trn_rolled or first_entry):
            return  # static cache hit; saying so over and over is just noise

        parts = []
        for label in RANKED_PLAYLISTS:
            cv = (cur.get(label) or {}).get("mmr")
            pv = (prev.get(label) or {}).get("mmr")
            if cv is None:
                parts.append(f"{label}=—")
            elif pv is None:
                parts.append(f"{label}={cv}")
            elif cv == pv:
                parts.append(f"{label}={cv} (·)")
            else:
                parts.append(f"{label}={cv} ({cv - pv:+d})")
        best = entry.get("best") or {}
        best_part = (
            f"best={best.get('mmr')}@{best.get('playlist')}"
            if best else "best=—"
        )
        line = (
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            f"{'  '.join(parts)}  {best_part}  "
            f"trn_lastUpdated={last_updated}"
        )
        try:
            with MY_MMR_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            mmr_log(f"my_mmr.log write failed: {e}")
        # Persist a structured snapshot to mmr_history.jsonl for the graph
        # view. Stricter dedupe than the log: only write when TRN's
        # lastUpdated actually advanced (or first entry ever) — otherwise
        # the attribution algorithm could double-count an interval.
        if trn_rolled or first_entry:
            snap = {
                "ts": now_iso(),
                "trn_lastUpdated": last_updated,
                "playlists": {
                    lbl: (cur.get(lbl) or {}).get("mmr")
                    for lbl in RANKED_PLAYLISTS
                    if (cur.get(lbl) or {}).get("mmr") is not None
                },
            }
            append_mmr_history(snap)
            mmr_history_cache["dirty"] = True
        last_self_log["playlists"] = cur
        last_self_log["lastUpdated"] = last_updated

    # Seed last_self_log from disk cache so deltas span restarts AND so the
    # first refresh after launch doesn't write a noise line if nothing moved.
    self_id = cfg.get("self_player_id")
    if self_id:
        existing_self = mmr_client.get(self_id)
        if existing_self and not existing_self.get("not_found"):
            last_self_log["playlists"] = existing_self.get("playlists") or {}
            last_self_log["lastUpdated"] = existing_self.get("lastUpdated")
            mmr_log(f"seed last_self_log from cache: "
                    f"{list(last_self_log['playlists'].keys())} "
                    f"trn_lastUpdated={last_self_log['lastUpdated']}")

    def on_mmr_updated(key: str):
        # Self MMR refresh? Mirror the snapshot to my_mmr.log for tracking.
        sid = cfg.get("self_player_id")
        if sid and key == sid:
            entry = mmr_client.get(key)
            if entry and not entry.get("not_found"):
                _log_my_mmr(entry)
        # Coalesce repaints — many opponents resolving in quick succession would
        # otherwise re-render once per arrival. The 200ms timer is single-shot
        # and rearmed on every signal, so we only repaint after the queue lulls.
        mmr_repaint_timer.start(200)

    mmr_repaint_timer = QTimer()
    mmr_repaint_timer.setSingleShot(True)

    def _mmr_repaint():
        if state["in_match"]:
            rerender_h2h()
            update_overlay()

    mmr_repaint_timer.timeout.connect(_mmr_repaint)
    mmr_client.updated.connect(on_mmr_updated)

    # System tray icon — gives the user a way to quit when launched via start.bat
    # (which uses pythonw and so has no console window to Ctrl+C).
    tray = None
    status_action = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(make_tray_icon())
        # Balloons are easy to miss and Windows suppresses them outright while a
        # game is fullscreen or Do Not Disturb is on — so the disabled-API state
        # also lives in the tooltip and the menu, which are always inspectable.
        starting_label = STATS_API_DISABLED if ini_problem else "starting…"
        tray.setToolTip(f"Rocket League H2H — {starting_label}")

        menu = QMenu()
        title_action = QAction("Rocket League H2H")
        title_action.setEnabled(False)
        menu.addAction(title_action)
        status_action = QAction(f"Status: {starting_label}")
        status_action.setEnabled(False)
        menu.addAction(status_action)
        menu.addSeparator()

        open_action = QAction("Open data folder")
        def _open_folder():
            try:
                if sys.platform == "win32":
                    os.startfile(str(DATA_DIR))  # type: ignore[attr-defined]
                else:
                    opener = "open" if sys.platform == "darwin" else "xdg-open"
                    subprocess.Popen([opener, str(DATA_DIR)])
            except Exception as e:
                print(f"[tray] open folder failed: {e}", file=sys.stderr)
        open_action.triggered.connect(_open_folder)
        menu.addAction(open_action)

        reset_session_action = QAction("Reset session stats")
        def _reset_session():
            session.reset()
            match_stats.reset(self_name=session.self_name)
            print("[reset] session stats cleared", file=sys.stderr)
            update_overlay()
        reset_session_action.triggered.connect(_reset_session)
        menu.addAction(reset_session_action)

        wipe_history_action = QAction("Wipe match history…")
        def _wipe_history():
            # Drain any queued match_ended slot first — otherwise a match that
            # ended just before the user clicked Wipe would write its record
            # *after* we've shown the dialog, and then we'd silently delete it.
            QApplication.processEvents()
            reply = QMessageBox.question(
                None,
                "Wipe match history",
                "Permanently delete matches.jsonl and players.json?\n"
                "Your current session stats are kept.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            for path in (MATCHES_PATH, PLAYERS_PATH):
                try:
                    path.unlink(missing_ok=True)
                except OSError as e:
                    print(f"[reset] failed to delete {path.name}: {e}", file=sys.stderr)
            players_db.clear()
            # The cached H2H card was rendered against the now-wiped opponent
            # records — refresh so a held Tab during this match doesn't show
            # stale W/L counts. (Idle text until the next match starts.)
            state["h2h_status"] = ("History wiped — fresh start.", None, False)
            print("[reset] match history wiped", file=sys.stderr)
            update_overlay()
        wipe_history_action.triggered.connect(_wipe_history)
        menu.addAction(wipe_history_action)
        menu.addSeparator()

        mmr_action = QAction("Show MMR (sends opponent IDs to tracker.gg)")
        mmr_action.setCheckable(True)
        mmr_action.setChecked(bool(cfg.get("mmr_enabled", False)))
        def _toggle_mmr(checked: bool):
            apply_mmr_toggle(bool(checked))
            update_overlay()
        mmr_action.toggled.connect(_toggle_mmr)
        menu.addAction(mmr_action)
        menu.addSeparator()

        auto_update_action = QAction("Auto-update on launch")
        auto_update_action.setCheckable(True)
        auto_update_action.setChecked(bool(cfg.get("auto_update", False)))
        def _toggle_auto_update(checked: bool):
            apply_auto_update(bool(checked))
        auto_update_action.toggled.connect(_toggle_auto_update)
        menu.addAction(auto_update_action)
        menu.addSeparator()

        quit_action = QAction("Quit")
        quit_action.triggered.connect(app.quit)
        menu.addAction(quit_action)

        tray.setContextMenu(menu)
        tray.show()

        # The only channel that reaches a user launched via start.bat: pythonw
        # has no console, and the idle overlay text never renders because the
        # H2H view also requires an in-progress match — which needs the very
        # socket that's disabled.
        # Deferred: showMessage() before the event loop runs is dropped on
        # Windows, because the shell hasn't registered the tray icon yet.
        # singleShot fires once app.exec() is up and the icon really exists.
        if ini_problem:
            QTimer.singleShot(3000, lambda: tray.showMessage(
                "Rocket League Stats API is disabled",
                f"{ini_problem}\n\nThe overlay cannot show anything until this is fixed.",
                QSystemTrayIcon.MessageIcon.Warning,
                30_000,
            ))

        # Skip the Qt setText/setToolTip churn when the connection state hasn't
        # changed — connection_status emits on every reconnect attempt failure
        # during a backoff storm, and Qt does compare strings, but building the
        # f-string and crossing the C++ boundary is wasted work.
        last_tray_state = [None]  # boxed for closure assignment

        def update_tray_status(connected: bool):
            if last_tray_state[0] == connected:
                return
            last_tray_state[0] = connected
            if connected:
                label = "Connected"
            else:
                # "Disconnected" is technically true but useless here: the
                # socket will never come up until the .ini is fixed.
                label = STATS_API_DISABLED if ini_problem else "Disconnected"
            if status_action is not None:
                status_action.setText(f"Status: {label}")
            tray.setToolTip(f"Rocket League H2H — {label}")
        stats.connection_status.connect(update_tray_status)
    else:
        print("[tray] system tray not available; quit via Task Manager / Ctrl+C",
              file=sys.stderr)

    stats.start()
    hotkey_h2h.start()
    hotkey_session.start()
    hotkey_expand.start()
    hotkey_cycle.start()
    hotkey_menu.start()

    print(f"[ready] h2h={cfg['hotkeys']} session={cfg.get('session_hotkeys') or []} "
          f"expand={cfg.get('expand_hotkeys') or []} "
          f"cycle={cfg.get('cycle_hotkeys') or []} "
          f"menu={cfg.get('menu_hotkey') or 'f5'} "
          f"position={cfg['position']} tcp://{cfg['host']}:{cfg['port']}")
    print(f"        require_rl_focus={cfg.get('require_rl_focus', True)} "
          f"expanded={state['h2h_expanded']} "
          f"mmr={cfg.get('mmr_enabled', False)}/{cfg.get('mmr_category', 'best')} "
          f"self={cfg.get('self_player_id') or '(auto-detect on first 1v1)'}")
    print(f"        matches → {MATCHES_PATH}")
    print(f"        players → {PLAYERS_PATH}")

    rc = app.exec()
    stats.stop()
    hotkey_h2h.stop()
    hotkey_session.stop()
    hotkey_expand.stop()
    hotkey_cycle.stop()
    hotkey_menu.stop()
    mmr_client.stop()
    sys.exit(rc)
