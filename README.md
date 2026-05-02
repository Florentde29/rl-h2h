# Rocket League Head-to-Head Overlay

A small Python overlay that connects to Rocket League's local Stats API, tracks your match history, and shows a transparent always-on-top card with per-opponent W/L records and live session stats.

![Head-to-head overlay with MMR enabled](docs/screenshots/mmr_readme.png)

> Runs on **Windows**. The Rocket League Stats API is Windows-only.

## Setup

### Quick install (recommended)

Two clicks:

1. **Download** [`install.bat`](https://raw.githubusercontent.com/Florentde29/rl-h2h/main/install.bat) (right-click the link → *Save link as…*) and put it anywhere — Downloads is fine.
2. **Double-click** the file. A console window opens and walks through everything: Python (if missing), Git (if missing), cloning the app to `Documents\rl-h2h\`, installing dependencies, finding your Rocket League install (Steam *or* Epic), and patching its Stats API config. ~2 minutes on a fresh machine, ~10 seconds on a re-run.

When it finishes you'll be asked whether to launch the app and whether to add a Desktop shortcut. There's only one thing the installer can't do for you:

- **Set Rocket League to Borderless** in *Settings → Video → Display Mode*. The overlay can't render over true-fullscreen DirectX.

The installer is **safe to re-run any time** — it detects what's already there and just updates the missing pieces. If your Rocket League is on a non-standard install (Microsoft Store, WeGame, custom location), a folder picker pops up so you can point at it manually.

### Manual install (advanced / for troubleshooting)

If you'd rather see every step yourself — or the quick install hit a snag — these are the same steps the installer runs.

This is done entirely in PowerShell. Press **Win + X** and click **Terminal** (Windows 11) or **Windows PowerShell** (Windows 10) to open it. Every command block below is something you copy and paste — in order, top to bottom.

#### 1. Install Python and Git

```powershell
winget install --id Python.Python.3.12 -e
winget install --id Git.Git -e
```

After both finish, **close and reopen PowerShell** so the new tools land on your PATH.

> If `winget` isn't recognised (older Windows 10 builds), install the **App Installer** package from the Microsoft Store first — that ships winget. As a manual alternative, download Python from <https://www.python.org/downloads/> (tick *"Add python.exe to PATH"*) and Git from <https://git-scm.com/download/win>.

#### 2. Download this project

```powershell
cd $env:USERPROFILE\Documents
git clone https://github.com/Florentde29/rl-h2h.git
cd rl-h2h
```

That clones the project into `Documents\rl-h2h\` and `cd`s into it. Stay in this folder for the next command.

#### 3. Install the Python dependencies

```powershell
python -m pip install -r requirements.txt
```

About 30 seconds. You only do this once.

#### 4. Enable the Rocket League Stats API

Open this file in Notepad (Rocket League ships it):

```
<Rocket League install folder>\TAGame\Config\DefaultStatsAPI.ini
```

Make sure it contains these two lines:

```
PacketSendRate=2
Port=49123
```

Save it, then **fully exit Rocket League** if it's running. RL only reads this `.ini` at launch — saving while the game is open does nothing.

#### 5. Set Rocket League to Borderless

In RL: **Settings → Video → Display Mode → Borderless**. Overlays can't render over true-fullscreen DirectX.

#### 6. First run

In the same PowerShell window:

```powershell
$env:PYTHONPATH = "$PWD\src"; python -m rl_h2h
```

The next section explains what to expect.

## Run

```powershell
$env:PYTHONPATH = "$PWD\src"; python -m rl_h2h
```

Or double-click `start.bat` in the project folder for a windowless launch (it sets `PYTHONPATH` for you). The script can be started before or after Rocket League — it auto-reconnects.

A small **Rocket League H2H icon appears in the Windows system tray** (notification area, bottom-right). Right-click it for a menu: connection status, "Open data folder", and **Quit**. If you don't see it, expand the chevron `^` next to the clock — Windows hides new tray icons by default. You can pin it: drag the icon onto the taskbar.

To launch automatically every time Windows boots, press **Win + R**, type `shell:startup`, press Enter, and drop a shortcut to `start.bat` into the folder that opens.

## Hotkeys (defaults)

The overlay is **held**, not toggled. Defaults match Rocket League's stock scoreboard binding so you can naturally peek at both at the same time.

| Action                                             | PC      | Xbox    | PlayStation |
|----------------------------------------------------|---------|---------|-------------|
| Head-to-head                                       | **Tab** | **LB**  | **L1**      |
| Session stats / MMR graph                          | **F8**  | —       | —           |
| Toggle expanded H2H *or* swap session ↔ graph      | **F7**  | —       | —           |
| Cycle MMR category *or* graph playlist             | **F6**  | —       | —           |
| In-game settings menu                              | **F5**  | —       | —           |

Hold to show; release to hide. Multiple bindings can be combined; the overlay shows while *any* of them is held.

**F7** is context-sensitive:
- While **Tab** is held (or nothing is held): toggles whether the H2H card also shows the session stats below. The choice persists across launches.
- While **F8** is held: swaps the session card to a **MMR graph** view of your last 30 ranked matches for the selected playlist, with W/L markers, faint rank-zone bands behind the line, and your net MMR delta over the visible window. Same key, second press → back to the session card.

**F6** is also context-sensitive:
- In the **H2H card**: cycles the MMR category shown next to each opponent — `best → 1v1 → 2v2 → 3v3 → best`. Active category appears in the H2H header (`MMR · BEST`) and the footer hint. Only does anything if MMR display is enabled — see below.
- In the **graph view** (F8 held + graph open): cycles the plotted playlist — `1v1 → 2v2 → 3v3`. Active playlist shows in the graph header (`MMR · 2V2`).

Both choices persist independently across launches.

## Settings menu (F5)

Press **F5** in-game to open a small settings panel inside the overlay. Press F5 again to close. Navigate with **↑/↓**, **Enter** to toggle a setting or rebind a key, **Esc** to cancel a rebind.

![Settings menu](docs/screenshots/settings_readme.png)

The menu lets you change two kinds of things without alt-tabbing out of Rocket League:

- **Toggles** — MMR display, auto-popup match summary, auto-update on launch. Same flags as the tray menu; the two surfaces stay in sync.
- **Bindings** — H2H hold, Session, Expand, Cycle MMR, and the menu key itself. Each action keeps **one keyboard slot + one gamepad slot**. Press a keyboard key during capture → the kb slot is replaced; press a gamepad button → the pad slot is replaced; the other slot is left alone. Changes apply live, no restart.

The menu key itself is keyboard-only (a gamepad button would too easily open it mid-game). While the menu is open, ↑/↓/Enter are intercepted at the OS level so they don't also reach Rocket League's UI.

## Post-match summary

When a match ends, a card flashes the result and your per-match stats — saves, shots, demos, and a few "fun" extras like fastest goal and hardest crossbar — alongside the opponent's numbers. Auto-hides when the next match starts.

![Post-match summary card](docs/screenshots/summary_readme.png)

Toggle it on/off from the **F5** menu (`Auto match summary`) or `show_match_summary` in `data/config.json`.

## MMR display

Right-click the tray icon and tick **"Show MMR (sends opponent IDs to tracker.gg)"**. Off by default. Once on, every opponent's rank tier and current MMR appear under their W/L line, color-coded by rank (bronze → SSL).

Defaults to "best" — the highest MMR across the three competitive playlists (1v1, 2v2, 3v3) — with a small playlist hint so you can see *which* playlist that came from. Cycle to a specific playlist with **F6**.

- **Source**: `rocketleague.tracker.network`'s public JSON endpoint. No API key required.
- **Privacy**: opponents' display names (and your own, until you exclude yourself) are sent over HTTPS to `api.tracker.gg`. The local match history, players, and config are never uploaded. Toggle off any time and the network calls stop immediately.
- **Freshness**: tracker.network refreshes from Psyonix on demand and caches each profile for ~4 minutes server-side. We cache for 10 minutes locally (`data/mmr_cache.json`). MMR updates between matches with up to ~4 min lag, typically near-realtime.
- **Throttle**: at most one outbound request every 0.5 seconds (≈ 2 req/sec). A full 3v3 lobby resolves in ~3s.
- **Lookup limit**: the tracker indexes by display name. If a console player has just renamed (or has a name that's not unique), they'll show as `—`. Epic, PSN, Xbox, Switch, Steam are all supported in principle, but coverage depends on whether the player is registered on tracker.network.

### Tracking your own MMR over time

When MMR display is enabled, the script also persists your own MMR snapshots to `data/mmr_history.jsonl` (one line per snapshot, only when TRN's data actually advances), and after every `MatchEnded` it polls TRN every 2 minutes for up to 10 min until your snapshot rolls. That's the data feeding the graph view (hold **F8**, then **F7** to swap from the session card to the graph).

![MMR graph view](docs/screenshots/graph_readme.png)

The graph approximates **per-game** MMR change from the cumulative snapshots. Algorithm: between two snapshots showing a delta of *D* MMR, with *W* wins and *L* losses recorded in that window, each win contributes `+|D|/|W−L|` and each loss `−|D|/|W−L|`. Examples:
- 2W + 1L net **+10** → step = 10 → `+10 / +10 / -10`
- 4W + 2L net **+36** → step = 18 → `+18 / +18 / +18 / +18 / -18 / -18`

When wins == losses (net zero), the step is the rolling median of past intervals' steps so deep-season MMR (which barely moves) still produces a sensible chart. The line is also reconciled to the actual snapshot value at every interval boundary, so rounding can't drift off truth.

> **Ranked vs casual**: per-game points are derived, not exact. The Rocket League Stats API doesn't expose queue type ([audited](docs/rl_api_text.md)), so if you mix ranked and casual matches in the same ~5-minute snapshot window, the MMR delta is split across all of them — a casual win in the middle of a ranked session will get credited a small "+MMR" it didn't actually earn. Snapshot boundaries always reconcile to the truth, so the totals stay correct.

To wipe the graph data: delete `data/mmr_history.jsonl`.

## Config

Settings live in `data/config.json` (created on first run). The `data/` folder is **gitignored**, so `git pull` never overwrites your local edits. When new options are added in a future version, your existing values are preserved and only the new keys are merged in. The top of the file has comments listing every supported keyboard and gamepad key name. Common tweaks:

- `hotkeys` / `session_hotkeys` / `expand_hotkeys` / `cycle_hotkeys` — lists of triggers, e.g. `["tab", "pad_lb"]`. Avoid D-pad bindings — stock RL maps them to quickchat. Easier: rebind from the in-game **F5** menu (Settings → Bindings).
- `menu_hotkey` — single key that opens/closes the in-game settings menu. Default `"f5"`. Keyboard-only.
- `position` — `top-right` (default), `top-left`, `top-center`, `bottom-right`, `bottom-left`
- `require_rl_focus` — set to `false` to also show the overlay on the desktop
- `self_player_id` — auto-filled after your first 1v1; set manually if you only play 2v2/3v3 (copy your `Platform|Uid` from `data/players.json`)
- `mmr_enabled` — `true` enables opponent MMR lookup against tracker.network. Off by default; flip via the tray menu so you see the privacy note.
- `mmr_category` — `"best" | "1v1" | "2v2" | "3v3"`. Cycled live with **F6**.

To wipe your match history, delete `data/matches.jsonl` and `data/players.json`. To wipe the MMR graph data, delete `data/mmr_history.jsonl`.

## Privacy

By default everything runs locally. Everything in `data/` (matches, players, MMR cache, config) and `logs/` stays on your machine. The script connects to `127.0.0.1:49123` only; no remote network calls happen unless **you opt into one** of the two features below.

- **MMR display** (off by default — tray menu toggle). When enabled, the script sends each opponent's display name to `api.tracker.gg` over HTTPS to look up their public Rocket League profile. Your own player record is excluded from the lookup. Caches are local. Toggle off and the calls stop immediately.
- **Auto-update** (off by default — tray menu toggle). When enabled, `start.bat` checks GitHub for a newer version of this script.

The data is yours.

## Updates

Auto-update is **opt-in**. To enable it: right-click the tray icon (bottom-right, the Rocket League H2H icon) and tick **Auto-update on launch**. From then on, every time you run `start.bat`, the launcher checks GitHub for a newer version and applies it silently before starting the app. Untick it any time to go back to manual updates (`git pull`).

When auto-update is enabled:

- **Network failures don't block launch.** Offline? The updater times out after 5 s and the app starts on whatever's on disk.
- **Local edits are preserved.** If you've modified anything under `src/rl_h2h/` (or any other tracked file), the updater detects it and skips that run, leaving your customisations alone. You'll fall behind upstream until you reset.
- **Your data is never touched.** Everything under `data/` and `logs/` is gitignored and ignored by the updater too.
- **Two modes, picked automatically:**
  - If you cloned with `git`, the updater runs `git pull --ff-only`.
  - If you downloaded the repo as a ZIP from GitHub (no git installed), the updater fetches the latest archive directly from GitHub.
- **Run logs** are written to `logs/update.log` if you want to see what happened.

The setting persists in `data/config.json` (`auto_update: true|false`). Launching with `python -m rl_h2h` instead of `start.bat` always skips the updater, regardless of the toggle.

## Troubleshooting

- **Overlay never shows / Stats API never connects.** Make sure you fully exited Rocket League before saving `DefaultStatsAPI.ini`. The .ini is read once at launch — if RL was already running, your edits are ignored. Confirm the file contains `PacketSendRate=2` (or any non-zero) and `Port=49123`.
- **Overlay invisible during a match.** Set RL to **Borderless** display mode (Settings → Video → Display Mode). True fullscreen owns the whole screen; no overlay can render over it.
- **Tray icon missing.** Windows hides newly-installed tray icons behind the chevron `^` next to the clock by default. Click the chevron, drag the Rocket League H2H icon onto the taskbar to pin it. Or: Settings → Personalization → Taskbar → Other system tray icons → enable `pythonw.exe`.
- **Gamepad bindings silently ignored.** The `inputs` package needs to be installed (`pip install -r requirements.txt`). The console will say `[hotkey] gamepad listener watching: [...]` on startup if it's working.
- **Port 49123 already in use.** Pick a different port in `DefaultStatsAPI.ini` *and* in `data/config.json` (`"port": 49123`) — they must match.
- **MMR shows `—` for an opponent you can see in-game.** tracker.network indexes by display name. Console players who renamed recently, or who have a non-unique handle that collides with someone else, may not resolve. There is no way around this short of TRN updating their database. Epic players' MMR usually works because Epic display names are unique on the platform.
- **MMR shows `…` and never resolves.** Either `curl_cffi` failed to install (re-run `pip install -r requirements.txt`) or `api.tracker.gg` is unreachable. Open `logs/mmr.log` — every fetch, skip, and error gets logged there with timestamps. (`start.bat` uses `pythonw` which has no console window, so the log file is the source of truth.)

## License

[MIT](LICENSE).
