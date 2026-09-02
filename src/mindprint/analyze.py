"""Analysis layer: compute a structured self-profile from unified conversations.

Default mode is pure local statistics — no model, no network. An optional LLM
pass (local Ollama or any OpenAI-compatible endpoint the user configures) can
enrich the statistical profile into narrative synthesis; it is opt-in and
documented as sending nothing anywhere except the endpoint the user chooses.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timezone

from .schema import UnifiedConversation

# Words too generic to say anything in a topic profile (top of English + French).
STOPWORDS = frozenset(
    """the a an and or but if then of to in on for with is are was were be been am i
    you he she it we they this that these those my your his her its our their me him
    them do does did doing have has had having will would can could should may might
    not no yes so as at by from up down out about over under again more most some any
    what which who whom when where why how all both each few other than too very just
    je tu il elle nous vous ils elles le la les un une des du de et ou mais donc or ni
    car ne pas plus tres très est sont était cest c'est que qui quoi dont ce cet cette
    ces mon ton son ma ta sa mes tes ses nos vos leurs pour dans sur avec sans chez
    comme tout tous toute toutes meme même aussi peu puis donc alors si non oui est
    fais faire fait peux peut voulez vouloir suis es sommes etes êtes sera serait""".split()
)

WORD_RE = re.compile(r"[a-zà-öø-ÿ0-9]['a-zà-öø-ÿ0-9-]*", re.IGNORECASE)

# Project-evidence signals, deliberately conservative (FR + EN).
_PROJECT_HINTS = re.compile(
    r"\b(mvp|roadmap|backlog|sprint|repo|repository|deploy|déploiement|release|"
    r"launch|lancer|lancement|client|projet|project|deadline|livrable|deliverable|"
    r"pricing|tarif|devis|quote|prototype|poc|beta|v\d)\b",
    re.IGNORECASE,
)

_QUESTION_RE = re.compile(r"^\s*(what|why|how|when|where|who|which|can|could|should|is|are|do|does|did|"
                          r"est-ce|pourquoi|comment|quand|quoi|quel|quelle|quels|quelles|peux|puis-je|"
                          r"pouvez|est|sont|faut)\b", re.IGNORECASE)


def analyze(conversations: list[UnifiedConversation]) -> dict:
    """Compute the statistical profile. Pure function: input -> JSON-safe dict."""
    convs = _dedupe([c for c in conversations if c.messages])
    if not convs:
        return {"error": "no parseable conversations found in export"}

    all_user_text = [t for c in convs for t in c.iter_user_text()]
    words = _word_counter(all_user_text)
    bigrams = _bigram_counter(all_user_text)

    by_source = _per_source(convs)
    activity = _activity(convs)
    topics = _top_terms(words, bigrams, limit=25)
    projects = _project_evidence(convs)
    style = _style_metrics(all_user_text)
    languages = _language_guess(all_user_text)
    span = _span(convs)

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "notes": {
            "method": "local statistics only; no model, no network",
            "language_heuristic": "function-word share EN vs FR (approximate, insufficient for short histories)",
        },
        "summary": {
            "conversations": len(convs),
            "user_messages": len(all_user_text),
            "assistant_messages": sum(len(c.assistant_messages()) for c in convs),
            "first_activity": span[0],
            "last_activity": span[1],
        },
        "per_source": by_source,
        "activity": activity,
        "topics": topics,
        "projects": projects,
        "style": style,
        "languages": languages,
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _dedupe(convs: list[UnifiedConversation]) -> list[UnifiedConversation]:
    """Merge duplicates across overlapping exports: same source+id keeps the
    richest copy (users often stack an old export and a newer one)."""
    best: dict[tuple, UnifiedConversation] = {}
    order: list[tuple] = []
    for c in convs:
        key = (c.source, c.id) if c.id else (c.source, c.title, c.created_at, len(c.messages))
        if key in best:
            if len(c.messages) > len(best[key].messages):
                best[key] = c
        else:
            best[key] = c
            order.append(key)
    return [best[k] for k in order]


def _word_counter(texts: list[str]) -> Counter:
    counter: Counter = Counter()
    for text in texts:
        counter.update(w.lower() for w in WORD_RE.findall(text))
    return counter


def _bigram_counter(texts: list[str]) -> Counter:
    counter: Counter = Counter()
    for text in texts:
        words = [w.lower() for w in WORD_RE.findall(text)]
        pairs = zip(words, words[1:])
        counter.update(f"{a} {b}" for a, b in pairs if a not in STOPWORDS and b not in STOPWORDS)
    return counter


def _top_terms(words: Counter, bigrams: Counter, limit: int) -> dict:
    topics = [
        {"term": term, "count": n}
        for term, n in words.most_common(200)
        if term not in STOPWORDS and len(term) > 2 and not term.isdigit()
    ][:limit]
    return {
        "unigrams": topics,
        "bigrams": [{"term": t, "count": n} for t, n in bigrams.most_common(15)],
    }


def _per_source(convs: list[UnifiedConversation]) -> dict:
    out: dict[str, dict] = {}
    for c in convs:
        slot = out.setdefault(c.source, {"conversations": 0, "user_messages": 0, "assistant_messages": 0})
        slot["conversations"] += 1
        slot["user_messages"] += len(c.user_messages())
        slot["assistant_messages"] += len(c.assistant_messages())
    return out


def _activity(convs: list[UnifiedConversation]) -> dict:
    """Monthly conversation + message counts and the busiest hour of day."""
    monthly: Counter = Counter()
    hourly: Counter = Counter()
    for c in convs:
        ts = c.updated_at or c.created_at
        if ts:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            monthly[dt.strftime("%Y-%m")] += 1
            hourly[dt.hour] += 1
        for m in c.messages:
            if m.timestamp:
                dt = datetime.fromtimestamp(m.timestamp, tz=timezone.utc)
                hourly[dt.hour] += 1
    return {
        "conversations_per_month": dict(sorted(monthly.items())),
        "busiest_hours_utc": [h for h, _ in hourly.most_common(3)],
    }


def _project_evidence(convs: list[UnifiedConversation]) -> list[dict]:
    """Conversations whose title or user text carries project signals."""
    scored = []
    for c in convs:
        text = " ".join(c.iter_user_text())
        hits = len(_PROJECT_HINTS.findall(c.title)) * 3 + len(_PROJECT_HINTS.findall(text))
        if c.project:
            hits += 4
        if hits >= 3:
            scored.append(
                {
                    "title": c.title or "(untitled)",
                    "source": c.source,
                    "project": c.project,
                    "evidence_hits": hits,
                    "last_touched": _iso(c.updated_at or c.created_at),
                }
            )
    scored.sort(key=lambda x: (-x["evidence_hits"], x["last_touched"] or ""), reverse=False)
    return scored[:15]


def _style_metrics(texts: list[str]) -> dict:
    if not texts:
        return {}
    lengths = [len(t) for t in texts]
    questions = sum(1 for t in texts if _QUESTION_RE.match(t))
    tutoi = sum(1 for t in texts if re.search(r"\b(tu|te|ton|ta|tes)\b", t, re.IGNORECASE))
    vouvoie = sum(1 for t in texts if re.search(r"\b(vous|votre|vos)\b", t, re.IGNORECASE))
    return {
        "avg_user_message_chars": round(sum(lengths) / len(lengths)),
        "median_user_message_chars": int(sorted(lengths)[len(lengths) // 2]),
        "question_ratio": round(questions / len(texts), 2),
        "tutoiement_vs_vouvoiement": f"{tutoi}/{vouvoie}",
        "longest_message_chars": max(lengths),
    }


def _language_guess(texts: list[str]) -> dict:
    """Rough EN/FR share based on frequent function words — labeled heuristic."""
    en_markers = Counter()
    fr_markers = Counter()
    en_words = {"the", "and", "you", "for", "with", "this", "that", "have", "not", "can"}
    fr_words = {"le", "la", "les", "un", "une", "des", "je", "tu", "pour", "avec", "est", "pas", "sur"}
    for text in texts:
        words = [w.lower() for w in WORD_RE.findall(text)]
        en_markers.update(w for w in words if w in en_words)
        fr_markers.update(w for w in words if w in fr_words)
    total = en_markers.total() + fr_markers.total()
    if not total:
        return {"method": "function-word heuristic (approximate)", "verdict": "insufficient evidence"}
    en = en_markers.total() / total
    return {
        "method": "function-word heuristic (approximate)",
        "english_share": round(en, 2),
        "french_share": round(1 - en, 2),
    }


def _span(convs: list[UnifiedConversation]) -> tuple[str | None, str | None]:
    times = [t for c in convs for t in (c.created_at, c.updated_at) if t]
    if not times:
        return None, None
    return _iso(min(times)), _iso(max(times))


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _entropy(counter: Counter) -> float:
    total = counter.total()
    if not total:
        return 0.0
    return -sum((n / total) * math.log2(n / total) for n in counter.values())
