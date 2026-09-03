"""Diff: what changed since the last run.

mindprint stores a compact snapshot of the previous profile (project titles,
counts, first/last activity) next to its output. On refresh it reports:
new projects, dropped projects, and activity growth — the "3 new decisions,
1 project abandoned" signal that turns a one-shot converter into a habit.

Level-1 diff is title/count based: honest about what it can and cannot see.
"""

from __future__ import annotations

import json
from pathlib import Path

SNAPSHOT_NAME = "previous-run.json"


def _snapshot(profile: dict) -> dict:
    projects = sorted(
        p["title"] for p in profile.get("projects", [])
    )
    return {
        "schema_version": 1,
        "summary": profile.get("summary", {}),
        "projects": projects,
        "per_source": profile.get("per_source", {}),
    }


def load_previous(outdir: Path) -> dict | None:
    path = Path(outdir) / SNAPSHOT_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_snapshot(profile: dict, outdir: Path) -> Path:
    path = Path(outdir) / SNAPSHOT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_snapshot(profile), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def diff_profiles(previous: dict | None, profile: dict) -> list[str]:
    """Human-readable change lines; empty list on first run."""
    if not previous:
        return []
    out: list[str] = []
    prev_projects = set(previous.get("projects") or [])
    curr_projects = set(p["title"] for p in profile.get("projects", []))
    new = sorted(curr_projects - prev_projects)
    dropped = sorted(prev_projects - curr_projects)
    for title in new[:8]:
        out.append(f"🆕 projet détecté : {title}")
    for title in dropped[:8]:
        out.append(f"🌙 projet retombé : {title}")

    prev_sum = previous.get("summary", {})
    curr_sum = profile.get("summary", {})
    prev_msgs = prev_sum.get("user_messages") or 0
    curr_msgs = curr_sum.get("user_messages") or 0
    if curr_msgs > prev_msgs:
        out.append(f"💬 +{curr_msgs - prev_msgs} de tes messages depuis la dernière exécution")

    prev_sources = previous.get("per_source", {})
    for source, st in profile.get("per_source", {}).items():
        prev_n = (prev_sources.get(source) or {}).get("conversations") or 0
        if st["conversations"] > prev_n > 0:
            out.append(f"📥 {source} : +{st['conversations'] - prev_n} conversations")
    return out
