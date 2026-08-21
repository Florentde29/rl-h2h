"""Persisted JSON config: defaults, load, save."""
from __future__ import annotations

import json
import sys
from typing import Optional

from .paths import CONFIG_PATH, safe_atomic_write_text


DEFAULT_CONFIG = {
    "_comments": [
        "hotkeys:         held to show the head-to-head overlay (per-opponent W/L).",
        "session_hotkeys: held to show the current-session stats overlay.",
        "Keyboard names:  'tab', 'f1'..'f12', 'caps_lock', 'shift', 'esc', 'space',",
        "                 or a single character like 'h'.",
        "Gamepad (Xbox / PlayStation), prefix with 'pad_':",
        "  Buttons:  pad_a (Xbox A / PS X), pad_b (B / Circle), pad_x (X / Square), pad_y (Y / Triangle)",
        "  Bumpers:  pad_lb (LB / L1 — default RL scoreboard button on console), pad_rb (RB / R1)",
        "  Triggers: pad_lt (LT / L2), pad_rt (RT / R2)",
        "  D-pad:    pad_dpad_up, pad_dpad_down, pad_dpad_left, pad_dpad_right",
        "  Other:    pad_back (Xbox View / PS Share),",
        "            pad_start (Menu / Options), pad_lstick, pad_rstick",
        "Gamepad bindings require: pip install inputs",
        "Note: stock RL maps D-pad directions to quickchat. To avoid sending a quickchat",
        "      every time you check the overlay, prefer 'pad_lb' (default scoreboard).",
        "require_rl_focus:      only show the overlay when Rocket League has focus (Windows).",
        "show_match_summary:    flash a result + per-match stats card when a match ends.",
        "match_summary_seconds: max time the popup stays visible (auto-hides earlier when",
        "                       the next match starts or you leave to the menu).",
        "self_player_id:        auto-filled after your first 1v1 — used to hide your own W/L row.",
        "                       Set manually if you only play 2v2/3v3.",
        "recent_size:           number of recent W/L pips shown in the session card (default 5).",
        "name_max_length:       player name truncation length in the H2H card (default 16).",
        "expand_hotkeys:        press to toggle the H2H overlay between compact and expanded.",
        "                       Expanded mode appends the current-match stats below the H2H card.",
        "menu_hotkey:           press to open/close the in-game settings menu (single key,",
        "                       not a list). Navigate with arrow keys, Enter selects/toggles,",
        "                       Esc cancels capture or closes the menu.",
        "cycle_hotkeys:         press to cycle. Context-sensitive:",
        "                         - in H2H view: cycle MMR category",
        "                           (best -> 1v1 -> 2v2 -> 3v3 -> best)",
        "                         - in graph view (session_hotkeys held + graph open):",
        "                           cycle plotted playlist (1v1 -> 2v2 -> 3v3)",
        "                       Both choices persist independently.",
        "h2h_default_expanded:  initial expanded state at script launch. Re-saved on every",
        "                       toggle so your last choice persists across restarts.",
        "mmr_enabled:           when true, fetches each opponent's rank/MMR from",
        "                       rocketleague.tracker.network (one HTTP request per new",
        "                       opponent, cached for 10 minutes). Off by default — flipping it",
        "                       on sends opponents' Platform|Uid (Epic) or display names",
        "                       (other platforms) to tracker.network. Toggle via the tray menu.",
        "mmr_category:          which MMR to show: 'best' | '1v1' | '2v2' | '3v3' | 'peak'.",
        "                       Cycle live with the cycle_hotkeys key.",
        "session_view:          which sub-view session_hotkeys shows: 'session' (stats card) |",
        "                       'graph' (your MMR over time). Toggle with the expand_hotkeys",
        "                       key while session_hotkeys is held.",
        "graph_playlist:        which playlist the graph plots: '1v1' | '2v2' | '3v3'.",
        "graph_match_window:    how many recent matches to plot in the graph view.",
        "graph_match_grace_seconds: tolerance when joining matches to MMR snapshots —",
        "                       Psyonix->TRN propagation can lag behind a match's end",
        "                       by a minute or two; this grace catches matches that",
        "                       ended just before a snapshot rolled.",
        "auto_update:           when true, start.bat checks GitHub for a newer version and",
        "                       updates silently before launching the app. Off by default;",
        "                       enable via the tray menu (right-click the H icon).",
        "api_debug_dump:        when true, append every received Stats API envelope to",
        "                       api_dump.log (capped at 2 MB, truncate-rotates). Diagnostic",
        "                       only — leave off in normal use. Edit this file and restart",
        "                       to toggle (no tray UI; intentionally not hot-pluggable).",
        "mmr_debug_dump:        when true, write the next raw tracker.network payload to",
        "                       logs/trn_payload.json. Use it to see exactly which fields",
        "                       TRN sends (rank-up distance, divisions) before relying on them.",
        "hotkey_debug_log:      when true, write keyboard and gamepad listener diagnostics",
        "                       to logs/hotkey.log (capped at 256 KB). Use this when",
        "                       controller bindings don't seem to register — the log shows",
        "                       whether the gamepad thread started, whether 'inputs' is",
        "                       installed, and which events arrive. Off by default.",
        "position: top-left | top-center | top-right | bottom-left | bottom-right",
        "colors: override any overlay color (hex strings). All keys are optional.",
        "  win:   positive accent (wins, +diffs, your YOU tag, recent W pips)",
        "  loss:  negative accent (losses, recent L pips)",
        "  dim:   secondary text (sub-rows, last-played hint)",
        "  muted: tertiary text (separators, score em-dashes)",
        "  faint: dividers and the NEW pill border",
        "  team_blue_fallback / team_orange_fallback: used only when the wire reports gray",
        "         (private/training matches don't supply real ColorPrimary values)."
    ],
    "host": "127.0.0.1",
    "port": 49123,
    "hotkeys": ["tab", "pad_lb"],
    "session_hotkeys": ["f8"],
    "expand_hotkeys": ["f7"],
    "cycle_hotkeys": ["f6"],
    "menu_hotkey": "f5",
    "h2h_default_expanded": False,
    "mmr_enabled": False,
    "mmr_category": "best",
    "session_view": "session",
    "graph_playlist": "2v2",
    "graph_match_window": 30,
    "graph_match_grace_seconds": 120,
    "auto_update": False,
    "api_debug_dump": False,
    "hotkey_debug_log": False,
    "mmr_debug_dump": False,
    "require_rl_focus": True,
    "show_match_summary": True,
    "match_summary_seconds": 30,
    "self_player_id": None,
    "recent_size": 5,
    "name_max_length": 16,
    "position": "top-right",
    "margin": 24,
    "width": 380,
    "background_rgba": [16, 20, 21, 200],
    "border_radius_px": 4,
    "border_rgba": [255, 255, 255, 28],
    "text_color": "#E0E3E5",
    "font_family": "Segoe UI",
    "font_size": 11,
    "colors": {
        "win":                  "#CCFF00",
        "loss":                 "#FF6467",
        "dim":                  "#C4C9AC",
        "muted":                "#8E9379",
        "faint":                "#444933",
        "team_blue_fallback":   "#3B9EFF",
        "team_orange_fallback": "#FF7A29",
    },
}


def save_config(cfg: dict) -> None:
    out = {k: v for k, v in cfg.items() if k != "hotkey"}
    safe_atomic_write_text(CONFIG_PATH, json.dumps(out, indent=2), "config")


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    loaded: Optional[dict] = None
    if CONFIG_PATH.exists():
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg.update(loaded)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[config] failed to parse {CONFIG_PATH}: {e}", file=sys.stderr)
    # Backward compat: 'hotkey' (single str) → 'hotkeys' (list).
    if not cfg.get("hotkeys"):
        legacy = cfg.get("hotkey")
        cfg["hotkeys"] = [legacy] if isinstance(legacy, str) and legacy else ["tab"]
    elif isinstance(cfg["hotkeys"], str):
        cfg["hotkeys"] = [cfg["hotkeys"]]
    # Rewrite the file if it's missing any new default keys (e.g. _comments, hotkeys),
    # so users see the latest hints next time they open it.
    needs_rewrite = (loaded is None) or any(k not in loaded for k in DEFAULT_CONFIG)
    if needs_rewrite:
        out = {k: cfg[k] for k in cfg if k != "hotkey"}
        safe_atomic_write_text(CONFIG_PATH, json.dumps(out, indent=2), "config")
    return cfg
