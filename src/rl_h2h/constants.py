"""Wire-protocol event names and bucket keys shared across modules."""

EVT_MATCH_CREATED = "MatchCreated"
EVT_MATCH_INITIALIZED = "MatchInitialized"
EVT_ROUND_STARTED = "RoundStarted"
EVT_UPDATE_STATE = "UpdateState"
EVT_MATCH_ENDED = "MatchEnded"
EVT_MATCH_DESTROYED = "MatchDestroyed"
EVT_REPLAY_CREATED = "ReplayCreated"
EVT_GOAL_SCORED = "GoalScored"
EVT_BALL_HIT = "BallHit"
EVT_CROSSBAR_HIT = "CrossbarHit"
EVT_STATFEED = "StatfeedEvent"
EVT_CLOCK_UPDATED = "ClockUpdatedSeconds"

# StatfeedEvent.EventName values we explicitly track.
SF_SAVE = "Save"
SF_SHOT = "Shot"
SF_DEMOLISH = "Demolish"

BUCKET_VS = "vs"
BUCKET_WITH = "with"
