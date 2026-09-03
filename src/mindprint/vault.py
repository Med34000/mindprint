"""Vault output: generate an Obsidian-compatible personal dossier.

Structure (all plain Markdown, no Obsidian dependency):
    Dashboard.md            year overview, links, top themes
    Professionnel/<Project>.md / Personnel/<Project>.md
    Timeline/<YYYY-MM>.md   what moved that month
    Memory-file.md          the AI-injectable block (from memoryfile.py)
    Open-loops.md           intentions stated but never followed up

Level-1 rules (no LLM): every claim links back to its source conversation
(title + provider + date). Conservative heuristics only — when in doubt,
the note says so instead of inventing.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .memoryfile import build_memory_file
from .schema import UnifiedConversation

MAX_PROJECT_NOTES = 15
MAX_TIMELINE_ITEMS = 25

# Business-signal words → a project note lands under Professionnel/.
_PRO_RE = re.compile(
    r"\b(client|devis|pricing|tarif|mvp|roadmap|deploy|déploiement|crm|entreprise|"
    r"société|contrat|facture|hébergement|next\.?js|odoo|erp|pme|saas|seo|"
    r"projet professionnel|business|chf|infomaniak|claude code|agent)\b",
    re.IGNORECASE,
)

# Decision markers in user messages (FR-dominant, EN fallback).
_DECISION_RE = re.compile(
    r"^\s*(?:ok|bon|parfait|très bien|d'accord|daccord|g[oe]|allons[- ]y|)[\s,]*"
    r"(?:on (?:part|va|reste) (?:sur|avec|pour)|je (?:prends|choisis|pars) (?:sur|avec)|"
    r"décision(?:nel)?(?:ment)?|validé[e]?|c'est (?:validé|choisi)|final(?:ement|lement)|"
    r"let's go with|decided|we'll go with|going with)\b",
    re.IGNORECASE,
)

# Intention markers → open-loop candidates.
_INTENT_RE = re.compile(
    r"\b(?:je (?:vais|devrais|dois|compte)|il faudrait que je|(?:à|a) faire|"
    r"prochaine (?:étape|semaine)|prochainement|je prévois)\b"
    r"[^!?\n]{5,140}",
    re.IGNORECASE,
)
# BNoise: chitchat/positional phrases that look like intentions but never are.
_INTENT_NOISE_RE = re.compile(
    r"^je vais (au lit|au dodo|me coucher|manger|boire|tester)"
    r"|^je vais (où|ou)\b|^\s*\W|^il faudrait (que le|que la|que les) ", re.IGNORECASE,
)

_STATUS_DROP = re.compile(r"\s+")


def _esc(text: str) -> str:
    """Escape wiki-link breaking chars in note titles."""
    return re.sub(r'[\[\]|#^:"]', "", str(text)).strip() or "(untitled)"


def _date(iso: str | None) -> str:
    return (iso or "date inconnue")


def _probe_projects(convs: list[UnifiedConversation]) -> list[dict]:
    """Group high-signal conversations into project candidates."""
    from .analyze import _PROJECT_HINTS  # reuse the same evidence regex

    scored = []
    for c in convs:
        if not c.messages:
            continue
        user_text = " ".join(c.iter_user_text())
        hits = len(_PROJECT_HINTS.findall(c.title)) * 3 + len(_PROJECT_HINTS.findall(user_text))
        if c.project:
            hits += 4
        if hits >= 3:
            scored.append(
                {
                    "conv": c,
                    "title": c.title or "(untitled)",
                    "hits": hits,
                    "last": c.updated_at or c.created_at or 0,
                    "is_pro": bool(_PRO_RE.search(c.title) or _PRO_RE.search(user_text[:2000])),
                }
            )
    scored.sort(key=lambda x: -x["last"])
    return scored[:MAX_PROJECT_NOTES]


def _decisions(conv: UnifiedConversation) -> list[str]:
    """User messages that look like decisions, with dates. Conservative."""
    out = []
    for m in conv.user_messages():
        if _DECISION_RE.search(m.text[:200]):
            ts = m.timestamp
            when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "—"
            snippet = _STATUS_DROP.sub(" ", m.text[:180]).strip()
            out.append(f"- **{when}** — “{snippet}”")
    return out[:5]


def _intentions(conv: UnifiedConversation) -> list[str]:
    out = []
    for m in conv.user_messages():
        match = _INTENT_RE.search(m.text)
        if match:
            snippet = _STATUS_DROP.sub(" ", match.group(0)[:160]).strip()
            when = datetime.fromtimestamp(m.timestamp, tz=timezone.utc).strftime("%Y-%m-%d") if m.timestamp else "—"
            out.append(f"- **{when}** — “{snippet}…” · [source]({_source_link(conv)})")
    return out[:4]


def _source_link(conv: UnifiedConversation) -> str:
    label = _esc(conv.title)[:40]
    return f"{label} ({conv.source}, {_date(_iso(conv.updated_at or conv.created_at))})"


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _category_counts(convs: list[UnifiedConversation]) -> dict:
    per_source: Counter = Counter(c.source for c in convs)
    return dict(per_source)


def build_vault(convs: list[UnifiedConversation], profile: dict, outdir: Path) -> dict:
    """Write the vault; returns a manifest of generated files."""
    outdir = Path(outdir)
    (outdir / "Professionnel").mkdir(parents=True, exist_ok=True)
    (outdir / "Personnel").mkdir(parents=True, exist_ok=True)
    (outdir / "Timeline").mkdir(parents=True, exist_ok=True)

    files: list[str] = []
    live = [c for c in convs if c.messages]

    # Project notes first: the Dashboard indexes what exists on disk.
    files.extend(_write_project_notes(outdir, live))
    files.append(_write_dashboard(outdir, live, profile))
    files.extend(_write_timeline(outdir, live))
    (outdir / "Memory-file.md").write_text(build_memory_file(profile), encoding="utf-8")
    files.append("Memory-file.md")
    files.append(_write_open_loops(outdir, live))

    return {"files": files, "path": str(outdir)}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def _write_dashboard(outdir: Path, convs: list[UnifiedConversation], profile: dict) -> str:
    s = profile.get("summary", {})
    lines = [
        "# 📔 Dashboard — mon dossier IA",
        "",
        f"*Régénéré le {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M')} UTC · "
        f"{s.get('conversations', len(convs))} conversations · "
        f"{' + '.join(sorted(_category_counts(convs)))}*",
        "",
        "> Dossier 100% local, régénérable avec `mindprint`. Chaque affirmation renvoie vers sa source.",
        "",
    ]
    span = f"{s.get('first_activity', '?')} → {s.get('last_activity', '?')}"
    lines += ["## 📊 En un coup d'œil", "", f"- **Période couverte :** {span}",
              f"- **Messages de moi :** {s.get('user_messages', '?')} · "
              f"**réponses IA :** {s.get('assistant_messages', '?')}"]
    per_source = profile.get("per_source", {})
    if per_source:
        lines.append("- **Sources :** " + " · ".join(
            f"{k} ({v['conversations']})" for k, v in sorted(per_source.items())))
    months = profile.get("activity", {}).get("conversations_per_month", {})
    if months:
        last6 = sorted(months.items())[-6:]
        peak = max(months.values())
        lines += ["", "### 6 derniers mois", "", "```", *[
            f"{m} {'█' * max(1, round(n / peak * 24)):24s} {n}" for m, n in last6
        ], "```"]
    topics = [t["term"] for t in profile.get("topics", {}).get("unigrams", [])[:12]]
    if topics:
        lines += ["", "**Thèmes dominants :** " + " · ".join(topics)]
    pro, perso = _split_project_files(outdir)
    lines += ["", "## 💼 Professionnel", ""]
    lines += [f"- [[{_esc(t)}]]" for t in pro] or ["*(aucun projet détecté)*"]
    lines += ["", "## 🏠 Personnel", ""]
    lines += [f"- [[{_esc(t)}]]" for t in perso] or ["*(aucun)*"]
    lines += ["", "## 🔗 Pages", "", "- [[Open-loops]] — promesses non suivies d'effet",
              "- [[Memory-file]] — bloc à injecter dans une IA", "- Timeline/ — mois par mois"]
    (outdir / "Dashboard.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "Dashboard.md"


def _split_project_files(outdir: Path) -> tuple[list[str], list[str]]:
    pro = sorted(p.stem for p in (outdir / "Professionnel").glob("*.md"))
    perso = sorted(p.stem for p in (outdir / "Personnel").glob("*.md"))
    return pro, perso


# ---------------------------------------------------------------------------
# Project notes
# ---------------------------------------------------------------------------

def _write_project_notes(outdir: Path, convs: list[UnifiedConversation]) -> list[str]:
    written = []
    projects = _probe_projects(convs)
    for p in projects:
        conv = p["conv"]
        folder = "Professionnel" if p["is_pro"] else "Personnel"
        sources = [
            f"- {_source_link(c)}"
            for c in convs
            if (c.title == conv.title and c.source == conv.source) or _same_topic(c, conv)
        ][:8]
        lines = [
            f"# {_esc(p['title'])}",
            "",
            f"**Statut :** 🟢 actif · **Dernière activité :** {_date(_iso(p['last']))} · "
            f"**Source principale :** {conv.source}",
            "",
            "## Où j'en étais (niveau 1 — signaux bruts)",
            "",
            f"*Ce résumé est statistique, pas une synthèse IA. {len(conv.messages)} messages, "
            f"dont {len(conv.user_messages())} de moi.*",
            "",
        ]
        dec = _decisions(conv)
        lines += ["## 🎯 Décisions détectées", "", *(dec or ["*(aucune phrase de décision détectée — heuristique volontairement conservatrice)*"]), ""]
        lines += ["## 📚 Conversations sources", "", *sources, ""]
        rel = outdir / folder / f"{_esc(p['title'])}.md"
        rel.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(f"{folder}/{_esc(p['title'])}.md")
    return written


def _same_topic(a: UnifiedConversation, b: UnifiedConversation) -> bool:
    """Crude title-overline overlap to bundle related chats under one note."""
    ta = {w for w in re.findall(r"[a-zà-ÿ]{5,}", (a.title or "").lower())}
    tb = {w for w in re.findall(r"[a-zà-ÿ]{5,}", (b.title or "").lower())}
    return bool(ta and tb and len(ta & tb) / len(ta | tb) >= 0.5)


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

def _write_timeline(outdir: Path, convs: list[UnifiedConversation]) -> list[str]:
    by_month: dict[str, list[UnifiedConversation]] = defaultdict(list)
    for c in convs:
        ts = c.updated_at or c.created_at
        if ts:
            month = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")
            by_month[month].append(c)
    written = []
    for month in sorted(by_month):
        items = sorted(
            by_month[month], key=lambda c: c.updated_at or c.created_at or 0, reverse=True
        )[:MAX_TIMELINE_ITEMS]
        lines = [f"# 📅 {month}", "", f"*{len(by_month[month])} conversations ce mois-là*", ""]
        lines += [f"- {_date(_iso(c.updated_at or c.created_at))} · **{_esc(c.title)[:60]}** · `{c.source}`"
                  for c in items]
        (outdir / "Timeline" / f"{month}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(f"Timeline/{month}.md")
    return written


# ---------------------------------------------------------------------------
# Open loops
# ---------------------------------------------------------------------------

def _write_open_loops(outdir: Path, convs: list[UnifiedConversation]) -> str:
    """Intentions stated by the user with no later mention of the same subject.

    Level-1 honesty: this flags candidates, it does not prove abandonment.
    """
    candidates: list[dict] = []
    events: list[tuple[float, str, set, UnifiedConversation]] = []
    for c in convs:
        for m in c.user_messages():
            match = _INTENT_RE.search(m.text)
            if not match or _INTENT_NOISE_RE.search(match.group(0)):
                continue
            ts = m.timestamp or 0
            subject = {w for w in re.findall(r"[a-zà-ÿ]{5,}", match.group(0).lower())}
            events.append((ts, _STATUS_DROP.sub(" ", match.group(0)[:150]), subject, c))
    events.sort(key=lambda e: -e[0])
    all_text_by_month: list[tuple[float, set]] = []
    for c in convs:
        ts = c.updated_at or c.created_at or 0
        words = {w for w in re.findall(r"[a-zà-ÿ]{5,}", " ".join(c.iter_user_text()).lower())}
        all_text_by_month.append((ts, words))
    for ts, snippet, subject, conv in events:
        later = [w for t, w in all_text_by_month if t > ts + 86400 * 7 and subject & w]
        if not later:
            when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "—"
            candidates.append({"when": when, "snippet": snippet, "source": _source_link(conv)})
    lines = [
        "# 🔁 Open loops — promesses non suivies d'effet",
        "",
        "*Intentions que tu as énoncées à une IA, sans aucune mention ultérieure du même sujet "
        "(fenêtre de 7 jours min). Détection heuristique niveau 1 : à vérifier avant d'agir.*",
        "",
    ]
    for c in candidates[:20]:
        lines.append(f"- **{c['when']}** — “{c['snippet']}…” · via {c['source']}")
    if not candidates:
        lines.append("*(aucun open loop détecté)*")
    (outdir / "Open-loops.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "Open-loops.md"
