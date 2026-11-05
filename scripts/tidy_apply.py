"""Carry out the tidy plan saved by scripts/tidy_dry_run.py, exactly as approved.

Re-checks every file is still there, then moves them with the same undo log Blip uses, so
saying "undo that" to Blip (or scripts/tidy_apply.py --undo) puts everything back.
usage: python scripts/tidy_apply.py [--undo]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from blip import tidy

LOGS = Path(".blip/tidy")

if "--undo" in sys.argv:
    print(tidy.undo_last(LOGS))
    raise SystemExit

saved = json.loads(Path(".blip/tidy_plan.json").read_text())
folder = Path(saved["folder"])
groups: dict[str, list[str]] = {}
for name, into in saved["moves"]:
    groups.setdefault(into, []).append(name)
plan = tidy.make_plan(folder, [{"folder": k, "files": v} for k, v in groups.items()])
if plan.problems:
    print("Changed since the plan:", plan.problems)
moved, failed, log = tidy.apply(plan, LOGS)
print(f"Moved {moved} files into {len(groups)} folders. Undo log: {log}")
for f in failed:
    print("  couldn't move:", f)
