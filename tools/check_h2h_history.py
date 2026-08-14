"""Audit the head-to-head history: is it losing opponents, or are repeats just rare?

Answers two separate questions that feel identical from inside the game:

  1. How often do you ACTUALLY re-face the same opponent? Rocket League
     matchmaking rarely pairs you with the same people twice, so "everyone
     shows as NEW" is often correct rather than a bug. This prints the real
     rate from your own match log.

  2. Has players.json fallen behind matches.jsonl? save_players() swallows
     OSError and returns quietly, so a failed write silently drops a match
     from the H2H record while the match log keeps it. Replaying the log
     through the app's own update_players_cache() reveals the gap — and can
     rebuild the file from it.

    python tools\\check_h2h_history.py            # report only
    python tools\\check_h2h_history.py --repair   # rebuild players.json

Read-only unless --repair is passed, which backs up players.json first.
"""
from __future__ import annotations

import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from rl_h2h.constants import BUCKET_VS
    from rl_h2h.paths import MATCHES_PATH, PLAYERS_PATH, load_jsonl, parse_iso
    from rl_h2h.storage import update_players_cache
except ImportError as e:  # pragma: no cover - guidance beats a traceback
    print(f"Could not import rl_h2h from {ROOT / 'src'}: {e}")
    print("Run this from inside the repo checkout.")
    raise SystemExit(2)


def _fmt_day(iso: str | None) -> str:
    t = parse_iso(iso)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%d") if t else "?"


def main() -> int:
    repair = "--repair" in sys.argv

    matches = load_jsonl(MATCHES_PATH, "matches")
    stored = json.loads(PLAYERS_PATH.read_text(encoding="utf-8")) if PLAYERS_PATH.exists() else {}

    print("=" * 66)
    print("H2H history check")
    print("=" * 66)
    print(f"  matches.jsonl : {len(matches)} matches   {MATCHES_PATH}")
    print(f"  players.json  : {len(stored)} players    {PLAYERS_PATH}")
    if not matches:
        print("\nNo matches recorded yet — nothing to check.")
        return 0
    days = [m.get("endedAt") for m in matches]
    print(f"  date range    : {_fmt_day(min(days))} -> {_fmt_day(max(days))}")

    # --- 1. How often do opponents actually repeat? ---------------------
    print("\n" + "-" * 66)
    print("1. Do you actually re-face opponents?")
    print("-" * 66)
    faced = Counter()
    names: dict[str, str] = {}
    for m in matches:
        my_team = m.get("myTeam")
        for p in m.get("players") or []:
            if p.get("team") != my_team:            # opponents only
                faced[p["key"]] += 1
                names[p["key"]] = p.get("name", "?")
    repeats = {k: n for k, n in faced.items() if n > 1}
    total = len(faced)
    print(f"  distinct opponents faced : {total}")
    if total:
        pct = 100.0 * len(repeats) / total
        print(f"  faced 2+ times           : {len(repeats)}  ({pct:.1f}%)")
        print(f"  faced 3+ times           : {sum(1 for n in repeats.values() if n >= 3)}")
    if repeats:
        print("\n  Opponents you have met more than once:")
        for k, n in sorted(repeats.items(), key=lambda kv: -kv[1])[:15]:
            rec = stored.get(k, {}).get(BUCKET_VS, {})
            shown = f"{rec.get('wins', 0)}-{rec.get('losses', 0)}" if rec else "MISSING"
            print(f"    {n}x  {names[k][:24]:<24} {k:<28} H2H card shows {shown}")
        print("\n  These are the only players who can ever show anything but NEW.")
    else:
        print("\n  You have never faced the same opponent twice in this log.")
        print("  Every opponent showing NEW is therefore CORRECT, not a bug.")

    # --- 2. Has players.json fallen behind the match log? ---------------
    print("\n" + "-" * 66)
    print("2. Is players.json consistent with matches.jsonl?")
    print("-" * 66)
    replay: dict = {}
    skipped = 0
    for m in matches:
        try:
            update_players_cache(replay, m)
        except (KeyError, TypeError, IndexError):
            skipped += 1
    if skipped:
        print(f"  {skipped} malformed match record(s) skipped")

    def _wl(rec: dict, bucket: str) -> tuple:
        b = (rec or {}).get(bucket) or {}
        return (b.get("wins", 0), b.get("losses", 0))

    # (key, bucket, stored_wl, expected_wl); bucket is the one that actually
    # differs, so a teammate-only gap isn't reported against the 'vs' numbers.
    behind: list[tuple] = []
    for key, exp in replay.items():
        got = stored.get(key)
        for bucket in ("vs", "with"):
            if _wl(exp, bucket) != _wl(got, bucket):
                behind.append((key, bucket, None if got is None else _wl(got, bucket),
                               _wl(exp, bucket)))

    if not behind:
        print(f"  OK — all {len(replay)} players match the replay exactly.")
        print("  Nothing has been lost from players.json.")
    else:
        affected = {k for k, *_ in behind}
        print(f"  MISMATCH for {len(affected)} of {len(replay)} players.")
        print("  players.json is missing games that matches.jsonl still has,")
        print("  which means a save_players() write failed silently.\n")
        for key, bucket, got_wl, exp_wl in behind[:15]:
            who = (names.get(key) or replay[key].get("name") or "?")[:24]
            state = "absent" if got_wl is None else f"stored {got_wl[0]}-{got_wl[1]}"
            print(f"    {who:<24} {key:<28} [{bucket:<4}] {state}"
                  f"  should be {exp_wl[0]}-{exp_wl[1]}")
        print("\n  'vs' is the head-to-head record; 'with' counts games as teammates.")
        print("  Re-run with --repair to rebuild players.json from the match log.")

    # --- 3. Repair --------------------------------------------------------
    if repair:
        print("\n" + "-" * 66)
        print("3. Repair")
        print("-" * 66)
        if not behind:
            print("  Nothing to repair.")
            return 0
        ts = int(datetime.now(timezone.utc).timestamp())
        backup = PLAYERS_PATH.with_name(f"players.before-repair-{ts}.json")
        try:
            if PLAYERS_PATH.exists():
                shutil.copy2(PLAYERS_PATH, backup)
                print(f"  backed up existing file to {backup.name}")
            # Preserve any player the log can't account for (e.g. matches
            # appended before a field existed) rather than dropping them.
            merged = dict(stored)
            merged.update(replay)
            PLAYERS_PATH.write_text(
                json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
            print(f"  rebuilt players.json — {len(merged)} players")
            print("  Restart rl-h2h to load it.")
        except OSError as e:
            print(f"  FAILED: {e}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
