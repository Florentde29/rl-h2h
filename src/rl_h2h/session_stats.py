"""Session-wide and per-match stat aggregators, plus the session card renderer."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Optional

from .constants import (
    EVT_BALL_HIT,
    EVT_CROSSBAR_HIT,
    EVT_GOAL_SCORED,
    EVT_STATFEED,
    SF_DEMOLISH,
    SF_SAVE,
    SF_SHOT,
)
from .storage import last_touch_player


class SessionStats:
    """Aggregates stats from script start until kill. Updated from StatsClient signals."""

    def __init__(self, recent_size: int = 5):
        self._recent_size = max(1, int(recent_size))
        self.self_name: Optional[str] = None
        self.reset()

    def reset(self) -> None:
        # Identity (self_name) is preserved across resets; counters start fresh.
        self.started_at = datetime.now(timezone.utc)
        self.matches = 0
        self.wins = 0
        self.losses = 0
        self.win_streak = 0
        self.loss_streak = 0
        self.best_win_streak = 0
        self.recent: deque = deque(maxlen=self._recent_size)  # session-only W/L
        self.goals_for = 0
        self.goals_against = 0
        self.crossbars = 0
        # Each "max" is a pair: session-wide (anyone) and self-only.
        self.max_goal_speed = 0.0
        self.max_goal_speed_self = 0.0
        self.max_ball_speed = 0.0
        self.max_ball_speed_self = 0.0
        self.max_impact_force = 0.0
        self.max_impact_force_self = 0.0
        self.fastest_goal_time: Optional[float] = None
        self.fastest_goal_time_self: Optional[float] = None
        self.own_goals = 0
        self.own_goals_self = 0
        self.statfeed_counts: dict[str, int] = {}
        self.statfeed_counts_self: dict[str, int] = {}

    def _is_self(self, name: Optional[str]) -> bool:
        return bool(name) and name == self.self_name

    def on_event(self, event: str, data: dict) -> None:
        if event == EVT_GOAL_SCORED:
            scorer = data.get("Scorer")
            scorer_name = scorer.get("Name") if isinstance(scorer, dict) else None
            if data.get("bOwnGoal"):
                self.own_goals += 1
                last_name, _ = last_touch_player(data)
                if self._is_self(last_name):
                    self.own_goals_self += 1
            sp = data.get("GoalSpeed")
            if isinstance(sp, (int, float)):
                if sp > self.max_goal_speed:
                    self.max_goal_speed = float(sp)
                if self._is_self(scorer_name) and sp > self.max_goal_speed_self:
                    self.max_goal_speed_self = float(sp)
            gt = data.get("GoalTime")
            # Filter out 0 — it's an API artifact (e.g. first goal of a session, or
            # instant goals) that would otherwise pin "fastest goal" to 0 forever.
            if isinstance(gt, (int, float)) and gt > 0:
                if self.fastest_goal_time is None or gt < self.fastest_goal_time:
                    self.fastest_goal_time = float(gt)
                if self._is_self(scorer_name):
                    if self.fastest_goal_time_self is None or gt < self.fastest_goal_time_self:
                        self.fastest_goal_time_self = float(gt)
        elif event == EVT_CROSSBAR_HIT:
            self.crossbars += 1
            toucher_name, _ = last_touch_player(data)
            ifo = data.get("ImpactForce")
            if isinstance(ifo, (int, float)):
                if ifo > self.max_impact_force:
                    self.max_impact_force = float(ifo)
                if self._is_self(toucher_name) and ifo > self.max_impact_force_self:
                    self.max_impact_force_self = float(ifo)
        elif event == EVT_BALL_HIT:
            ball = data.get("Ball")
            sp = ball.get("PostHitSpeed") if isinstance(ball, dict) else None
            if isinstance(sp, (int, float)):
                if sp > self.max_ball_speed:
                    self.max_ball_speed = float(sp)
                # BallHit can have multiple players in the same frame — count if any is self.
                hitters = data.get("Players")
                if isinstance(hitters, list):
                    if any(self._is_self(h.get("Name")) for h in hitters if isinstance(h, dict)):
                        if sp > self.max_ball_speed_self:
                            self.max_ball_speed_self = float(sp)
        elif event == EVT_STATFEED:
            ev_name = data.get("EventName")
            if isinstance(ev_name, str) and ev_name:
                self.statfeed_counts[ev_name] = self.statfeed_counts.get(ev_name, 0) + 1
                main = data.get("MainTarget")
                if isinstance(main, dict) and self._is_self(main.get("Name")):
                    self.statfeed_counts_self[ev_name] = self.statfeed_counts_self.get(ev_name, 0) + 1

    def on_match_ended(self, payload: dict) -> None:
        self.matches += 1
        i_won = payload["winner"] == payload["myTeam"]
        if i_won:
            self.wins += 1
            self.win_streak += 1
            self.loss_streak = 0
            self.best_win_streak = max(self.best_win_streak, self.win_streak)
        else:
            self.losses += 1
            self.loss_streak += 1
            self.win_streak = 0
        self.recent.append("W" if i_won else "L")
        score = payload.get("score")
        if isinstance(score, list) and len(score) == 2:
            mt = payload["myTeam"]
            self.goals_for += score[mt]
            self.goals_against += score[1 - mt]


class MatchStats:
    """Per-match aggregator. Reset on match_initialized, snapshot on match_ended.

    Every metric tracks both the match-wide value (anyone) and the self-only value,
    so the post-match summary can render 'match | yours' pairs.
    """

    def __init__(self, self_name: Optional[str] = None):
        self.reset(self_name)

    def reset(self, self_name: Optional[str] = None) -> None:
        self.self_name = self_name
        # Counters (statfeed-driven and crossbar event)
        self.saves = 0
        self.saves_self = 0
        self.shots = 0
        self.shots_self = 0
        self.demos = 0           # demolitions given, anyone
        self.demos_self = 0      # demolitions given by you
        self.demoed_self = 0     # times you got demolished (no match-wide pair — use total)
        self.crossbars = 0
        self.crossbars_self = 0
        # Maxes
        self.max_goal_speed = 0.0
        self.max_goal_speed_self = 0.0
        self.max_ball_speed = 0.0
        self.max_ball_speed_self = 0.0
        self.max_impact_force = 0.0
        self.max_impact_force_self = 0.0
        # Min (fastest goal time in seconds; None = no goal yet)
        self.fastest_goal_time: Optional[float] = None
        self.fastest_goal_time_self: Optional[float] = None
        self.own_goals = 0
        self.own_goals_self = 0

    def _is_self(self, name) -> bool:
        return bool(name) and name == self.self_name

    def on_event(self, event: str, data: dict) -> None:
        if event == EVT_GOAL_SCORED:
            scorer = data.get("Scorer")
            scorer_name = scorer.get("Name") if isinstance(scorer, dict) else None
            if data.get("bOwnGoal"):
                self.own_goals += 1
                last_name, _ = last_touch_player(data)
                if self._is_self(last_name):
                    self.own_goals_self += 1
            sp = data.get("GoalSpeed")
            if isinstance(sp, (int, float)):
                self.max_goal_speed = max(self.max_goal_speed, float(sp))
                if self._is_self(scorer_name):
                    self.max_goal_speed_self = max(self.max_goal_speed_self, float(sp))
            gt = data.get("GoalTime")
            if isinstance(gt, (int, float)) and gt > 0:
                if self.fastest_goal_time is None or gt < self.fastest_goal_time:
                    self.fastest_goal_time = float(gt)
                if self._is_self(scorer_name):
                    if self.fastest_goal_time_self is None or gt < self.fastest_goal_time_self:
                        self.fastest_goal_time_self = float(gt)
        elif event == EVT_BALL_HIT:
            ball = data.get("Ball")
            sp = ball.get("PostHitSpeed") if isinstance(ball, dict) else None
            if isinstance(sp, (int, float)):
                self.max_ball_speed = max(self.max_ball_speed, float(sp))
                hitters = data.get("Players")
                if isinstance(hitters, list) and any(
                    self._is_self(h.get("Name")) for h in hitters if isinstance(h, dict)
                ):
                    self.max_ball_speed_self = max(self.max_ball_speed_self, float(sp))
        elif event == EVT_CROSSBAR_HIT:
            self.crossbars += 1
            toucher, _ = last_touch_player(data)
            if self._is_self(toucher):
                self.crossbars_self += 1
            ifo = data.get("ImpactForce")
            if isinstance(ifo, (int, float)):
                self.max_impact_force = max(self.max_impact_force, float(ifo))
                if self._is_self(toucher):
                    self.max_impact_force_self = max(self.max_impact_force_self, float(ifo))
        elif event == EVT_STATFEED:
            ev = data.get("EventName")
            main = data.get("MainTarget")
            sec = data.get("SecondaryTarget")
            main_name = main.get("Name") if isinstance(main, dict) else None
            sec_name = sec.get("Name") if isinstance(sec, dict) else None
            if ev == SF_SAVE:
                self.saves += 1
                if self._is_self(main_name):
                    self.saves_self += 1
            elif ev == SF_SHOT:
                self.shots += 1
                if self._is_self(main_name):
                    self.shots_self += 1
            elif ev == SF_DEMOLISH:
                self.demos += 1
                if self._is_self(main_name):
                    self.demos_self += 1
                if self._is_self(sec_name):
                    self.demoed_self += 1


def session_has_split(s: SessionStats) -> bool:
    """True when at least one stat shows the session-vs-self split, so the
    'Format' legend is informative rather than noise."""
    return (
        s.max_goal_speed > s.max_goal_speed_self
        or s.max_ball_speed > s.max_ball_speed_self
        or s.max_impact_force > s.max_impact_force_self
        or s.own_goals > s.own_goals_self
        or s.statfeed_counts.get(SF_SAVE, 0) > s.statfeed_counts_self.get(SF_SAVE, 0)
        or s.statfeed_counts.get(SF_SHOT, 0) > s.statfeed_counts_self.get(SF_SHOT, 0)
        or s.statfeed_counts.get(SF_DEMOLISH, 0) > s.statfeed_counts_self.get(SF_DEMOLISH, 0)
    )


