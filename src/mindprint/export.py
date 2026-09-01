"""Output layer: write the profile as JSON (machine) and Markdown (human)."""

from __future__ import annotations

import json
import re
from pathlib import Path


def _md_escape(text: str) -> str:
    """Escape Markdown-significant chars in untrusted strings (titles, terms).

    Titles come straight from user prompts full of *, _, backticks, and raw
    HTML; unescaped they break rendering or inject links/tags.
    """
    text = str(text)
    text = text.replace("\\", "\\\\")
    for ch in r"`*_{}[]()#+-.!|<>":
        text = text.replace(ch, "\\" + ch)
    return text


def write_json(profile: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def to_markdown(profile: dict) -> str:
    """Render the statistical profile as a readable, copyable Markdown file."""
    if "error" in profile:
        return f"# Mindprint\n\n⚠️ {profile['error']}\n"
    s = profile.get("summary", {})
    lines: list[str] = []
    lines.append("# 🧠 Mindprint — Self-Profile")
    lines.append("")
    lines.append(f"*Generated locally on {profile.get('generated_at', '?')} — no data left this machine.*")
    lines.append("")

    lines.append("## Overview")
    lines.append("")
    lines.append(f"- **Conversations:** {s.get('conversations', 0)}")
    lines.append(f"- **Your messages:** {s.get('user_messages', 0)}")
    lines.append(f"- **Assistant replies:** {s.get('assistant_messages', 0)}")
    first, last = s.get("first_activity"), s.get("last_activity")
    if first or last:
        lines.append(f"- **Activity span:** {first or '?'} → {last or '?'}")
    lines.append("")

    per_source = profile.get("per_source", {})
    if per_source:
        lines.append("### By source")
        lines.append("")
        for source, st in per_source.items():
            lines.append(
                f"- **{source}**: {st['conversations']} conversations, "
                f"{st['user_messages']} user messages"
            )
        lines.append("")

    activity = profile.get("activity", {})
    per_month = activity.get("conversations_per_month", {})
    if per_month:
        lines.append("### Activity per month")
        lines.append("")
        lines.append("```")
        max_count = max(per_month.values())
        for month, n in per_month.items():
            bar = "█" * max(1, round(n / max_count * 30))
            lines.append(f"{month}  {bar} {n}")
        lines.append("```")
        hours = activity.get("busiest_hours_utc")
        if hours:
            lines.append("")
            lines.append(f"*Most active hours (UTC): {', '.join(map(str, hours))}*")
        lines.append("")

    topics = profile.get("topics", {})
    unigrams = topics.get("unigrams", [])
    if unigrams:
        lines.append("## Top topics (your words)")
        lines.append("")
        lines.append(" | ".join(f"**{t['term']}** ({t['count']})" for t in unigrams[:15]))
        lines.append("")
        bigrams = topics.get("bigrams", [])
        if bigrams:
            lines.append("**Recurring phrases:** " + ", ".join(f"“{b['term']}”" for b in bigrams[:8]))
            lines.append("")

    projects = profile.get("projects", [])
    if projects:
        lines.append("## 🚀 Project signals")
        lines.append("")
        for p in projects:
            label = _md_escape(p["title"])
            extra = f" · {_md_escape(p['project'])}" if p.get("project") else ""
            when = f" · last touched {p['last_touched']}" if p.get("last_touched") else ""
            lines.append(f"- **{label}** ({_md_escape(p['source'])}{extra}) — evidence {p['evidence_hits']}{when}")
        lines.append("")

    style = profile.get("style", {})
    if style:
        lines.append("## ✍️ How you write")
        lines.append("")
        lines.append(f"- Average message length: **{style.get('avg_user_message_chars')} chars** "
                     f"(median {style.get('median_user_message_chars')})")
        lines.append(f"- Question ratio: **{style.get('question_ratio')}**")
        lines.append(f"- Tu/vous usage: **{style.get('tutoiement_vs_vouvoiement')}**")
        lines.append("")

    langs = profile.get("languages", {})
    if langs:
        lines.append("## 🌍 Languages (heuristic)")
        lines.append("")
        lines.append(f"- English ≈ {langs.get('english_share', 0):.0%} · French ≈ {langs.get('french_share', 0):.0%}")
        lines.append("")

    lines.append("---")
    lines.append("*This profile was computed 100% locally by [mindprint](https://github.com/Med34000/mindprint). "
                 "Nothing was uploaded. It describes you — you own it.*")
    return "\n".join(lines) + "\n"


def write_markdown(profile: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_markdown(profile), encoding="utf-8")
    return path
