"""Memory-file output: distill the statistical profile into an AI-ready context file.

The memory file is designed to be pasted into a system prompt, CLAUDE.md,
AGENTS.md, or any assistant's custom instructions. It is deliberately compact
(≈ a few hundred tokens), deterministic (pure statistics, no model needed),
and written so an AI can *use* it: how to address the user, what they are
working on, what matters to them — with dates so staleness is visible.
"""

from __future__ import annotations

from datetime import datetime, timezone

MAX_PROJECTS = 7
MAX_TOPICS = 10
MAX_BIGRAMS = 5


def _fmt_date(iso: str | None) -> str:
    return iso or "unknown"


def build_memory_file(profile: dict) -> str:
    """Render the profile as a compact system-context Markdown file."""
    if "error" in profile:
        return f"# User memory file\n\n(profile unavailable: {profile['error']})\n"

    s = profile.get("summary", {})
    generated = profile.get("generated_at", "")
    lines: list[str] = []
    lines.append("# User memory file")
    lines.append("")
    lines.append(
        f"Statistical self-profile distilled from the user's own AI exports "
        f"({s.get('conversations', 0)} conversations, {s.get('user_messages', 0)} of their messages, "
        f"{_fmt_date(s.get('first_activity'))} → {_fmt_date(s.get('last_activity'))}). "
        f"Generated locally by mindprint on {generated}. Facts may be stale after that date — "
        f"verify time-sensitive details with the user."
    )
    lines.append("")

    # --- How to communicate with the user -------------------------------
    style = profile.get("style", {})
    langs = profile.get("languages", {})
    lines.append("## Communication")
    lines.append("")
    comm = []
    fr = langs.get("french_share")
    if fr is not None and fr >= 0.5:
        comm.append(f"Primary language: French (~{fr:.0%}); English {1 - fr:.0%}")
    elif "verdict" not in langs:
        comm.append(f"Primary language: English (~{(langs.get('english_share') or 0):.0%})")
    tu_vous = style.get("tutoiement_vs_vouvoiement", "")
    if tu_vous and tu_vous != "0/0":
        tu, vous = tu_vous.split("/")
        if int(tu) > int(vous) * 2:
            comm.append("Uses informal 'tu' — address them informally")
        elif int(vous) > int(tu) * 2:
            comm.append("Uses formal 'vous' — keep address formal")
    med = style.get("median_user_message_chars")
    if med:
        tone = "short, directive messages" if med < 150 else "detailed messages with context"
        comm.append(f"Writes {tone} (median {med} chars)")
    if style.get("question_ratio") is not None and style["question_ratio"] < 0.15:
        comm.append("Gives directives and context more than asking questions")
    lines.extend(f"- {c}" for c in comm)
    lines.append("")

    # --- Active projects -------------------------------------------------
    projects = profile.get("projects", [])[:MAX_PROJECTS]
    if projects:
        lines.append("## Active projects (most recent first)")
        lines.append("")
        by_recency = sorted(projects, key=lambda p: p.get("last_touched") or "", reverse=True)
        for p in by_recency:
            label = p["title"] or "(untitled)"
            origin = f" via {p['project']}" if p.get("project") else ""
            lines.append(f"- **{label}**{origin} — last active {_fmt_date(p.get('last_touched'))}")
        lines.append("")

    # --- Recurring topics -------------------------------------------------
    topics = profile.get("topics", {})
    unigrams = [t["term"] for t in topics.get("unigrams", [])[:MAX_TOPICS]]
    bigrams = [t["term"] for t in topics.get("bigrams", [])[:MAX_BIGRAMS]]
    if unigrams or bigrams:
        lines.append("## Recurring themes")
        lines.append("")
        if unigrams:
            lines.append("Frequently discussed: " + ", ".join(unigrams) + ".")
        if bigrams:
            lines.append("Recurring phrases: " + "; ".join(f"“{b}”" for b in bigrams) + ".")
        lines.append("")

    # --- Work rhythm -------------------------------------------------------
    activity = profile.get("activity", {})
    hours = activity.get("busiest_hours_utc")
    months = activity.get("conversations_per_month", {})
    rhythm = []
    if hours:
        pretty = ", ".join(f"{h}:00" for h in hours)
        rhythm.append(f"Most active around {pretty} UTC")
    if months:
        recent = sorted(months.items())[-3:]
        avg = sum(n for _, n in recent) / len(recent)
        rhythm.append(f"~{avg:.0f} conversations/month recently")
    if rhythm:
        lines.append("## Work rhythm")
        lines.append("")
        lines.extend(f"- {r}" for r in rhythm)
        lines.append("")

    lines.append("---")
    lines.append(
        "*Computed offline from the user's own data — no upload, no model. "
        "Regenerate with `mindprint` when your activity shifts.*"
    )
    return "\n".join(lines) + "\n"


def write_memory_file(profile: dict, path: str) -> str:
    from pathlib import Path

    text = build_memory_file(profile)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")
    return text
