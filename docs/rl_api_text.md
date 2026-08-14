# Rocket League Stats API — Reference

Source: <https://www.rocketleague.com/en/developer/stats-api> (captured 2026-05-01).

The Stats API is a **local WebSocket** that the Rocket League client opens
while a match is in progress. It broadcasts JSON messages at a configurable
periodic rate plus event-driven messages. Third-party tools (broadcaster
HUDs, overlays, trackers, etc.) ingest these messages.

---

## 1. Enabling the API (player-side setup)

Two files hold this config, and the game reads the *user* one at launch:

| File | Role |
| ---- | ---- |
| `<Install Dir>\TAGame\Config\DefaultStatsAPI.ini` | Shipped template. Carries an `[IniVersion]` timestamp; when it's newer than the user file's, RL regenerates the user file from it. |
| `<Documents>\My Games\Rocket League\TAGame\Config\TAStatsAPI.ini` | What the game actually reads. Regenerated from the template — so edits here can be reverted by a patch. |

Set both, **before launching the client**. Changes made while the game is
running require a restart.

| Setting          | Type  | Default       | Description                                                                                  |
| ---------------- | ----- | ------------- | -------------------------------------------------------------------------------------------- |
| `PacketSendRate` | float | `0` (disabled)| Number of `UpdateState` packets per second. Must be > 0 to enable the socket. Capped at 120. |
| `Port`           | int   | `49123`       | Local TCP port. Set to 0 to disable. Must differ from `WebPort`.                              |
| `WebPort`        | int   | `49124`       | Added in v2.72 (Aug 2026): port for "web connections", i.e. the real WebSocket endpoint. Unused by this project. |

Connect to: `ws://127.0.0.1:<Port>` (default `ws://127.0.0.1:49123`).

> **Patches reset `PacketSendRate` to `0`.** Observed after v2.72 (Aug 2026):
> both files came back with `PacketSendRate=0`, silently killing the feed. The
> `[IniVersion]` timestamp in the user file records when it was regenerated,
> which is a quick way to confirm a patch was responsible.

---

## 2. Message envelope

Every message follows the same envelope:

```json
{
  "Event": "EventName",
  "Data":  { /* event-specific payload */ }
}
```

### Field-visibility conventions

- **`CONDITIONAL`** — only present when relevant (e.g. `Attacker` only when a
  player is demolished, `Assister` only when a goal had an assist).
- **`SPECTATOR`** — only present if the local client is spectating or on the
  same team as the player. Not visible for opponents during normal play.

### Gotcha: double-encoded `Data` payload (observed in practice)

In this project we found the `Data` payload arrives as a **JSON string**
inside the envelope rather than an inline object — i.e. it must be
`json.loads`'d a second time. See commit `f7d6344` ("Decode the
double-encoded Data payload") and the parser in `rl_h2h.py`. Always be
defensive: try parsing `Data` as a string first, fall back to using it
directly if it's already a dict.

### Connection gotcha

The websockets handshake probe sometimes fails or hangs; in this project we
skip the WS probe and connect via raw TCP (`c9c27ea`).

This still holds after v2.72. That patch added "WebSocket support" to the
Stats API, but on a *separate* port (`WebPort`, default 49124) — the original
`Port` (49123) still speaks raw NDJSON, so no client change is needed. Confirm
with `tools/diag_stats_api.py`, which reads the socket without sending, then
retries with an RFC 6455 `Upgrade` request to tell the two apart.

---

## 3. Tick message

### `UpdateState`

Sent `PacketSendRate` times per second. This is the "current state of the
match" snapshot used to drive HUDs/overlays.

Top-level shape:

```jsonc
{
  "Event": "UpdateState",
  "Data": {
    "MatchGuid": "…",            // online/LAN matches only
    "Players": [ /* per-player */ ],
    "Game":    { /* match-level state */ }
  }
}
```

#### `Players[]` fields

| Field            | Type   | Visibility   | Description                                                              |
| ---------------- | ------ | ------------ | ------------------------------------------------------------------------ |
| `Name`           | string |              | Display name.                                                            |
| `PrimaryId`      | string |              | `Platform\|Uid\|Splitscreen` (e.g. `Steam\|123\|0`, `Epic\|456\|0`).     |
| `Shortcut`       | int    |              | Spectator shortcut number.                                               |
| `TeamNum`        | int    |              | `0` = Blue, `1` = Orange.                                                |
| `Score`          | int    |              | Total match score.                                                       |
| `Goals`          | int    |              | Goals this match.                                                        |
| `Shots`          | int    |              | Shot attempts.                                                           |
| `Assists`        | int    |              | Assists.                                                                 |
| `Saves`          | int    |              | Saves.                                                                   |
| `Touches`        | int    |              | Total ball touches.                                                      |
| `CarTouches`     | int    |              | Touches by car body (not ball).                                          |
| `Demos`          | int    |              | Demolitions inflicted.                                                   |
| `bHasCar`        | bool   | SPECTATOR    | Player currently has a vehicle.                                          |
| `Speed`          | float  | SPECTATOR    | Vehicle speed in Unreal Units/second.                                    |
| `Boost`          | int    | SPECTATOR    | Boost amount 0–100.                                                      |
| `bBoosting`      | bool   | SPECTATOR    | Currently boosting.                                                      |
| `bOnGround`      | bool   | SPECTATOR    | At least 3 wheels touching the world.                                    |
| `bOnWall`        | bool   | SPECTATOR    | Vehicle is on a wall.                                                    |
| `bPowersliding`  | bool   | SPECTATOR    | Holding handbrake.                                                       |
| `bDemolished`    | bool   | SPECTATOR    | Vehicle currently destroyed.                                             |
| `bSupersonic`    | bool   | SPECTATOR    | At supersonic speed.                                                     |
| `Attacker`       | object | CONDITIONAL  | The demolisher; present only while `bDemolished`. `{Name, Shortcut, TeamNum}`. |

#### `Game` fields

| Field          | Type    | Visibility   | Description                                                                         |
| -------------- | ------- | ------------ | ----------------------------------------------------------------------------------- |
| `Teams[]`      | array   |              | One entry per team, ordered by `TeamNum`.                                           |
| ↳ `Name`       | string  |              | Team name.                                                                          |
| ↳ `TeamNum`    | int     |              | Team index.                                                                         |
| ↳ `Score`      | int     |              | Team goal count.                                                                    |
| ↳ `ColorPrimary`   | string |          | Hex color (no `#`) for primary color.                                               |
| ↳ `ColorSecondary` | string |          | Hex color for secondary color.                                                      |
| `TimeSeconds`  | int     |              | Seconds remaining in the match.                                                     |
| `bOvertime`    | bool    |              | Match is in overtime.                                                               |
| `Ball`         | object  |              | `{Speed: float, TeamNum: int}` — `TeamNum` of last team to touch (`255` if none).   |
| `bReplay`      | bool    |              | A goal replay or history replay is active.                                          |
| `bHasWinner`   | bool    |              | A team has won.                                                                     |
| `Winner`       | string  |              | Winning team name; empty string if no winner yet.                                   |
| `Arena`        | string  |              | Asset name of the current map (e.g. `Stadium_P`). Map to friendly names client-side.|
| `bHasTarget`   | bool    |              | Client is viewing a specific vehicle.                                               |
| `Target`       | object  | CONDITIONAL  | `{Name, Shortcut, TeamNum}` of viewed player; empty/0 fields if no target.          |
| `Frame`        | int     | CONDITIONAL  | Current frame number while a replay is active.                                      |
| `Elapsed`      | float   | CONDITIONAL  | Seconds elapsed since game start while a replay is active.                          |

---

## 4. Events

All events share `MatchGuid` (string, set only for **online or LAN** matches).
Fields below are the additional payload beyond `MatchGuid`.

### Match lifecycle

| Event              | When fired                                                       | Extra fields                  |
| ------------------ | ---------------------------------------------------------------- | ----------------------------- |
| `MatchCreated`     | All teams created and replicated.                                | —                             |
| `MatchInitialized` | First countdown starts.                                          | —                             |
| `CountdownBegin`   | Start of each round when countdown begins.                       | —                             |
| `RoundStarted`     | Game enters active state (after countdown finishes).             | —                             |
| `MatchPaused`      | Match admin pauses.                                              | —                             |
| `MatchUnpaused`    | Match admin unpauses.                                            | —                             |
| `MatchEnded`       | Match ends, winner chosen.                                       | `WinnerTeamNum: int`          |
| `PodiumStart`      | Podium state after match end.                                    | —                             |
| `MatchDestroyed`   | Leaving the game.                                                | —                             |
| `ReplayCreated`    | Replay loaded from Match History (NOT goal replays).             | —                             |

### Goal-replay lifecycle

| Event              | When fired                                                                              |
| ------------------ | --------------------------------------------------------------------------------------- |
| `GoalReplayStart`  | Goal replay starts.                                                                     |
| `GoalReplayWillEnd`| Ball explodes during the goal replay. **Not fired if the replay is skipped.**           |
| `GoalReplayEnd`    | Goal replay ends.                                                                       |

### Gameplay events

#### `BallHit`
Sent **one frame after** the ball is hit.

```jsonc
{
  "Players": [ { "Name": "...", "Shortcut": 1, "TeamNum": 0 } ],   // players who hit on that frame
  "Ball": {
    "PreHitSpeed":  0.0,
    "PostHitSpeed": 1450.2,
    "Location": { "X": -512, "Y": 100, "Z": 200 }
  }
}
```

#### `ClockUpdatedSeconds`
In-game clock changed.

```jsonc
{ "TimeSeconds": 180, "bOvertime": false }
```

#### `CrossbarHit`
Ball hit a crossbar.

```jsonc
{
  "BallLocation": { "X": 120, "Y": -2944, "Z": 320 },
  "BallSpeed":   870.3,
  "ImpactForce": 127.5,                 // relative to crossbar normal
  "BallLastTouch": {
    "Player": { "Name": "...", "Shortcut": 1, "TeamNum": 0 },
    "Speed":  120
  }
}
```

#### `GoalScored`

```jsonc
{
  "GoalSpeed":      87.3,               // ball speed when crossing line
  "GoalTime":      127.5,               // length of previous round, seconds
  "ImpactLocation": { "X": 0, "Y": -2944, "Z": 320 },
  "Scorer":   { "Name": "...", "Shortcut": 1, "TeamNum": 0 },
  "Assister": { "Name": "...", "Shortcut": 3, "TeamNum": 0 },   // CONDITIONAL
  "BallLastTouch": {
    "Player": { "Name": "...", "Shortcut": 1, "TeamNum": 0 },
    "Speed":  125
  }
}
```

##### Detecting own-goals (and the placeholder-event quirk)

The API has **no own-goal flag**, and RL's accounting model doesn't
treat own-goals as a category at all: every goal is credited to a
player on the team whose score went up. So when you put the ball in
your own net, `Scorer.TeamNum` is the *opposing* team's number, and
`Scorer.Name` is whichever opposing player RL picks (typically the last
opponent who touched the ball before you deflected it in). The
score-delta heuristic does **not** work for this reason: by the time
the score updates, RL has already rebadged the goal as a normal goal
for the other team.

The only reliable signal is **`BallLastTouch.Player.TeamNum`**:

- Compare it against `Scorer.TeamNum`.
- If they match → normal goal.
- If they differ → own-goal. The actual "scorer" (in the colloquial
  sense — the player who put the ball in their own net) is
  `BallLastTouch.Player.Name`, **not** `Scorer.Name`.

**Placeholder GoalScored events.** RL emits *two* `GoalScored` events
around each goal:

1. The real one, with full `Scorer` info.
2. A placeholder, with `Scorer.Name == ""` and `Scorer.TeamNum == 0`
   (Python's default int).

For a normal goal, the real event arrives first and the placeholder
follows after the kickoff. For an own-goal (where RL has to compute
credit during the replay), the placeholder arrives first and the real
event follows once credit is assigned. Either way, **drop placeholders
where `Scorer.Name` is empty** — they're not actionable.

This is what `rl_h2h.py` does in `StatsClient._classify_goal_scored`.

#### `StatfeedEvent`
Fires whenever a player earns a stat (demos, saves, etc).

```jsonc
{
  "EventName":    "Demolish",           // asset name (e.g. "Demolish", "Save")
  "Type":         "Demolition",         // localized display label
  "MainTarget":      { "Name": "PlayerA", "Shortcut": 1, "TeamNum": 0 },
  "SecondaryTarget": { "Name": "PlayerB", "Shortcut": 2, "TeamNum": 1 }   // CONDITIONAL
}
```

---

## 5. Common shapes

**Player ref** (used in `Scorer`, `Assister`, `MainTarget`, `Attacker`, etc.):
```jsonc
{ "Name": "string", "Shortcut": 1, "TeamNum": 0 }
```

**Vector**:
```jsonc
{ "X": 0.0, "Y": 0.0, "Z": 0.0 }
```

**`PrimaryId`** is `Platform|Uid|Splitscreen` — split on `|` to get the
platform (`Steam`, `Epic`, `PS4`, `XboxOne`, `Switch`, …) and persistent uid.
Only `UpdateState.Players[].PrimaryId` carries this; event payloads only
expose `Name`/`Shortcut`/`TeamNum`.

**Units**: speeds are in **Unreal Units/second** (~100 UU = 1 metre).
Supersonic threshold ≈ `2200` UU/s.

---

## 6. Practical notes for this project

- The "websocket" is local-only; no auth. Despite the official wording, **the
  wire format is raw TCP NDJSON** — RFC 6455 WebSocket handshake is not
  honored. Connect with `asyncio.open_connection`, parse with
  `json.JSONDecoder().raw_decode()` over a rolling buffer.
- `Data` is **double-encoded JSON** in practice — parse the envelope, then
  `json.loads` the `Data` field again. (`f7d6344`)
- Be defensive in the `UpdateState` parser: spectator-only fields and
  conditional objects (`Attacker`, `Target`, `Assister`) may be missing —
  type-check every nested access. (`6e38e7f`)
- One bad message must not kill the client — wrap per-message handling
  in a try/except and keep the loop alive. (`3b3776d`)
- Event order during a goal:
  `BallHit` → `GoalScored` → `GoalReplayStart` → `GoalReplayWillEnd` →
  `GoalReplayEnd` → `CountdownBegin` → `RoundStarted`.
- **Doc/wire mismatch**: the doc lists `GoalReplayWillEnd`, the wire emits
  `ReplayWillEnd` (no `Goal` prefix). We don't dispatch on it; flagging in
  case Psyonix fixes one or the other later.
- Match end signal for persisting H2H records: `MatchEnded` (carries
  `WinnerTeamNum`); `MatchDestroyed` is a fallback when the user quits early.
- `Arena` is the asset name (`Stadium_P`, `TrainStation_Night_P`, …). Pretty
  names live in `rl_h2h.py` (`ARENA_BASE` + variant suffixes).
- `MatchGuid` is **only present for online/LAN matches** — don't rely on it
  for offline/freeplay/exhibition.
- `Game.Teams[].Score` is the live team score; capture it on every
  `UpdateState` and snapshot at `MatchEnded` for the final tally.
- `Game.Teams[].ColorPrimary` is the in-game team color (no `#`). May be
  gray (`959595`) in private/training maps; fall back to defaults in that case.
- **Self detection.** The wire has no "this is me" flag. Heuristic:
  spectator-only fields appear only for the local client's team. In 1v1, the
  single player on `my_team` is "me" — record their `PrimaryId` once
  (`Platform|Uid` after dropping the splitscreen suffix) and reuse forever.
  In 2v2/3v3 we can't auto-detect from one match without prior data.
- **PrimaryId platforms observed**: `Epic|<32-char-hex>|0`, `PS4|<numeric>|0`,
  `XboxOne|<numeric>|0`, `Steam|<steamid64>|0`. The third segment is
  splitscreen index — strip it for cross-session identity.
- **What's NOT in the API**: MMR, rank, division, playlist/queue type, party
  composition, replay file path. For MMR you have to scrape Tracker Network
  by `PrimaryId`.
- **Audit (2026-05-02)**: dumped every envelope verbatim during a casual 1v1
  AND a ranked 1v1, then diffed. Every field matches between modes — no
  undocumented queue-type field exists. Verified absent across
  `MatchCreated`, `MatchInitialized`, `CountdownBegin`, `RoundStarted`,
  `UpdateState.{Game,Players,Teams}`, `BallHit`, `StatfeedEvent`,
  `GoalScored`, `CrossbarHit`, `ClockUpdatedSeconds`, `MatchEnded`,
  `MatchDestroyed`. Every event carries `MatchGuid` plus its event-specific
  payload — nothing else identifies playlist or ranked-vs-casual. Per-game
  MMR attribution therefore can't disambiguate ranked from casual matches
  played in the same TRN snapshot window from API data alone; either rely on
  a manual user toggle or infer from MMR-motion correlation.
