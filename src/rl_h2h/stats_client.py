"""Local TCP NDJSON client for the Rocket League Stats API."""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import threading
from typing import Optional

from PySide6.QtCore import QObject, Signal

from .applog import api_dump
from .constants import (
    EVT_GOAL_SCORED,
    EVT_MATCH_CREATED,
    EVT_MATCH_DESTROYED,
    EVT_MATCH_ENDED,
    EVT_MATCH_INITIALIZED,
    EVT_REPLAY_CREATED,
    EVT_ROUND_STARTED,
    EVT_UPDATE_STATE,
)
from .storage import last_touch_player, player_key


SPECTATOR_FIELDS = ("Boost", "bBoosting", "bOnGround", "bOnWall", "bSupersonic")

# If the stream ever delivers bytes that don't decode as JSON, nothing is
# consumed from the buffer and it would grow without bound for the rest of
# the session. Real envelopes are a few KB; past this, the connection is
# garbage — drop it and let the reconnect loop start clean.
_MAX_PENDING_BUFFER = 1_000_000


class StatsClient(QObject):
    """Reads the Rocket League Stats API local TCP socket and emits match-lifecycle signals."""

    match_initialized = Signal(dict)
    match_ended = Signal(dict)
    match_destroyed = Signal()
    connection_status = Signal(bool)
    event_seen = Signal(str, dict)  # raw event name + decoded data, for downstream consumers

    def __init__(self, host: str, port: int, api_dump_enabled: bool = False):
        super().__init__()
        self.host = host
        self.port = port
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Task] = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="StatsClient")
        self._api_dump_enabled = bool(api_dump_enabled)
        self._reset()

    def _reset(self):
        self._roster: dict[str, dict] = {}
        self._my_team: Optional[int] = None
        self._arena: str = ""
        self._match_guid: Optional[str] = None
        self._initialized_emitted = False
        self._last_emitted_roster_size = 0
        self._round_started = False  # locks the roster after kickoff
        self._ended_emitted = False
        self._spectator_warned = False
        self._score: list[int] = [0, 0]
        self._team_colors: dict[int, str] = {}
        self._in_replay = False
        self._update_state_count_this_match = 0

    def start(self):
        self._thread.start()

    def stop(self):
        loop, task = self._loop, self._task
        if loop is not None and task is not None and not task.done():
            loop.call_soon_threadsafe(task.cancel)
        self._thread.join(timeout=3)

    def _run(self):
        try:
            asyncio.run(self._main())
        except Exception as e:
            print(f"[stats] event loop crashed: {e}", file=sys.stderr)

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        try:
            await self._connect_loop()
        except asyncio.CancelledError:
            pass
        finally:
            self.connection_status.emit(False)

    async def _connect_loop(self):
        # Local Stats API speaks raw TCP NDJSON despite the doc calling it a "web socket".
        backoff = 1.0
        while True:
            try:
                print(f"[stats] connecting tcp://{self.host}:{self.port}", file=sys.stderr)
                await self._run_tcp()
                print("[stats] disconnected; reconnecting", file=sys.stderr)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except OSError as e:
                self.connection_status.emit(False)
                print(f"[stats] connect failed ({type(e).__name__}: {e}); "
                      f"retry in {backoff:.0f}s", file=sys.stderr)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _run_tcp(self):
        reader, writer = await asyncio.open_connection(self.host, self.port)
        self.connection_status.emit(True)
        try:
            decoder = json.JSONDecoder()
            buf = ""
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    return
                buf += chunk.decode("utf-8", errors="replace")
                while True:
                    stripped = buf.lstrip()
                    if not stripped:
                        buf = ""
                        break
                    try:
                        obj, idx = decoder.raw_decode(stripped)
                    except json.JSONDecodeError:
                        buf = stripped
                        if len(buf) > _MAX_PENDING_BUFFER:
                            print(f"[stats] {len(buf)} bytes without a decodable "
                                  f"message — dropping connection", file=sys.stderr)
                            return
                        break
                    buf = stripped[idx:]
                    self._safe_handle(obj)
        finally:
            writer.close()
            # wait_closed can hang on Windows when the peer socket is half-open;
            # cap it so stop()'s 3s thread.join() always wins the race.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), timeout=1.0)

    def _safe_handle(self, msg) -> None:
        if not isinstance(msg, dict):
            return
        event = msg.get("Event", "?")
        if self._api_dump_enabled:
            # UpdateState fires at 2 Hz; sample to keep api_dump.log small
            # enough that a full session fits under the 2 MB cap.
            should_dump = event != EVT_UPDATE_STATE
            if not should_dump and self._update_state_count_this_match < 3:
                should_dump = True
                self._update_state_count_this_match += 1
            if should_dump:
                raw_data = msg.get("Data")
                if isinstance(raw_data, str):
                    try:
                        decoded = json.loads(raw_data) if raw_data else {}
                    except json.JSONDecodeError:
                        decoded = raw_data
                else:
                    decoded = raw_data if raw_data is not None else {}
                api_dump(event, decoded)
        try:
            self._handle(msg)
        except Exception as e:
            import traceback
            print(f"[stats] handler error on {event}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    def _handle(self, msg: dict):
        event = msg.get("Event")
        # Wire format double-encodes: Data arrives as a JSON-encoded string.
        data = msg.get("Data")
        if isinstance(data, str):
            try:
                data = json.loads(data) if data else {}
            except json.JSONDecodeError:
                data = {}
        if not isinstance(data, dict):
            data = {}
        if event == EVT_GOAL_SCORED:
            if self._classify_goal_scored(data):
                self.event_seen.emit(event, data)
        elif event:
            self.event_seen.emit(event, data)
        if event == EVT_UPDATE_STATE:
            self._on_update_state(data)
        elif event == EVT_MATCH_CREATED:
            self._reset()
            self._match_guid = data.get("MatchGuid")
        elif event == EVT_MATCH_INITIALIZED:
            self._maybe_emit_initialized()
        elif event == EVT_ROUND_STARTED:
            # Lock the roster at kickoff. One last emit if anyone joined since the
            # previous emit; from this point we stop accumulating players.
            self._maybe_emit_initialized()
            if self._initialized_emitted and len(self._roster) > self._last_emitted_roster_size:
                self._emit_match_initialized()
            self._round_started = True
        elif event == EVT_MATCH_ENDED:
            self._on_match_ended(data)
        elif event == EVT_MATCH_DESTROYED:
            # A match that was initialised but never ended is never counted in
            # the session or written to players.json. Forfeits are the suspected
            # case: the teardown may not send MatchEnded at all, or may send it
            # after this. Say so, since the symptom is a silently empty session.
            if self._initialized_emitted and not self._ended_emitted:
                print("[stats] match destroyed without MatchEnded — not counted "
                      "(forfeit or disconnect?)", file=sys.stderr)
            self.match_destroyed.emit()
            self._reset()
        elif event == EVT_REPLAY_CREATED:
            self._in_replay = True

    def _on_update_state(self, data: dict):
        if not isinstance(data, dict) or self._in_replay:
            return
        game = data.get("Game")
        if isinstance(game, dict):
            arena = game.get("Arena")
            if isinstance(arena, str) and arena and arena != self._arena:
                self._arena = arena
            teams = game.get("Teams")
            if isinstance(teams, list):
                for t in teams:
                    if not isinstance(t, dict):
                        continue
                    tn = t.get("TeamNum")
                    if tn not in (0, 1):
                        continue
                    sc = t.get("Score")
                    if isinstance(sc, int):
                        self._score[int(tn)] = sc
                    if len(self._team_colors) < 2:
                        cp = t.get("ColorPrimary")
                        if (int(tn) not in self._team_colors and isinstance(cp, str)
                                and len(cp) == 6):
                            self._team_colors[int(tn)] = "#" + cp.upper()
        # Once the round has actually started (kickoff fired), the roster is locked.
        # Until then we keep accumulating — players load asynchronously in 2v2/3v3.
        if self._round_started:
            return
        players = data.get("Players")
        if not isinstance(players, list):
            return
        spectator_team_hits: set[int] = set()
        for p in players:
            if not isinstance(p, dict):
                continue
            pid = p.get("PrimaryId")
            team = p.get("TeamNum")
            if not isinstance(pid, str) or team not in (0, 1):
                continue
            key = player_key(pid)
            name_raw = p.get("Name")
            name = name_raw if isinstance(name_raw, str) else "?"
            self._roster[key] = {
                "key": key,
                "primaryId": pid,
                "name": name,
                "team": int(team),
            }
            if any(k in p for k in SPECTATOR_FIELDS):
                spectator_team_hits.add(int(team))
        # Spectator-only fields appear iff the local client is on this player's team.
        # If both teams report them, the user is spectating — leave my_team unset.
        if self._my_team is None and len(spectator_team_hits) == 1:
            (self._my_team,) = spectator_team_hits
        elif self._my_team is None and len(spectator_team_hits) > 1 and not self._spectator_warned:
            self._spectator_warned = True
            print(f"[state] spectator mode? both teams report spectator fields: "
                  f"{spectator_team_hits}", file=sys.stderr)
        # First emit when both teams have a player; re-emit on every roster growth so
        # late joiners (typical in 3v3) appear in the H2H card before kickoff.
        if not self._initialized_emitted:
            self._maybe_emit_initialized()
        elif len(self._roster) > self._last_emitted_roster_size:
            self._emit_match_initialized()

    def _on_match_ended(self, data: dict):
        if self._in_replay:
            return
        winner = data.get("WinnerTeamNum")
        if winner is None or self._my_team is None or not self._roster:
            print(f"[stats] MatchEnded ignored: winner={winner} "
                  f"my_team={self._my_team} roster={len(self._roster)}",
                  file=sys.stderr)
            return
        # A match that never kicked off is not a match. When Rocket League
        # cancels one — a player fails to connect, the lobby dissolves — it
        # still reports a WinnerTeamNum, which was being recorded as a real
        # win against opponents who never played. RoundStarted is the only
        # signal that a ball was ever put in play.
        if not self._round_started:
            print("[stats] MatchEnded before any kickoff — not counted "
                  "(cancelled match?)", file=sys.stderr)
            return
        self._ended_emitted = True
        self.match_ended.emit({
            "winner": int(winner),
            "myTeam": self._my_team,
            "arena": self._arena,
            "matchGuid": self._match_guid,
            "players": list(self._roster.values()),
            "score": list(self._score),
            "teamColors": dict(self._team_colors),
        })

    def _classify_goal_scored(self, data: dict) -> bool:
        """Set ``bOwnGoal`` on data; return False for placeholder events.

        See ``docs/rl_api_text.md`` for the placeholder-event quirk and own-goal
        derivation rules.
        """
        scorer = data.get("Scorer")
        if not isinstance(scorer, dict):
            return False
        scorer_name = scorer.get("Name")
        if not scorer_name:
            return False
        scorer_team = scorer.get("TeamNum")
        _, last_team = last_touch_player(data)
        data["bOwnGoal"] = (
            scorer_team in (0, 1)
            and last_team in (0, 1)
            and scorer_team != last_team
        )
        return True

    def _maybe_emit_initialized(self):
        if (self._initialized_emitted or self._in_replay
                or self._my_team is None or not self._roster):
            return
        teams = {p["team"] for p in self._roster.values()}
        if len(teams) < 2:
            return
        self._initialized_emitted = True
        self._emit_match_initialized()

    def _emit_match_initialized(self):
        self._last_emitted_roster_size = len(self._roster)
        self.match_initialized.emit({
            "teamColors": dict(self._team_colors),
            "arena": self._arena,
            "myTeam": self._my_team,
            "players": list(self._roster.values()),
        })
