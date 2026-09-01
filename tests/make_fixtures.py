"""Generate synthetic export fixtures that mimic the real official formats.

Run: python tests/make_fixtures.py
Creates tests/fixtures/chatgpt_export.zip and tests/fixtures/claude_export.zip
plus the extracted variants used by the test-suite.
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _unix(days_ago: int, hour: int = 14) -> float:
    dt = datetime(2026, 8, 1, hour, tzinfo=timezone.utc) - timedelta(days=days_ago)
    return dt.timestamp()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_chatgpt_export() -> dict:
    """Tree-structured conversations.json as produced by current ChatGPT exports."""
    def node(msg_id, role, text, ts, parent=None, model=None):
        message = {
            "id": msg_id,
            "author": {"role": role, "metadata": ({"model_slug": model} if model else {})},
            "create_time": ts,
            "content": {"content_type": "text", "parts": [text]},
            "metadata": {},
        }
        return {"id": msg_id, "message": message, "parent": parent, "children": []}

    # Conversation 1: pricing/devis project talk (project signals), with a regenerated branch.
    t1 = _unix(30)
    u1 = node("u1", "user", "Je veux lancer un MVP pour mon projet de devis automatisé pour les garages, tu peux m'aider sur le pricing ?", t1)
    a1 = node("a1", "assistant", "Bien sûr. Pour le pricing d'un MVP de devis automatisé, partons du temps gagné…", _unix(30, 15), parent="u1", model="gpt-5")
    u2 = node("u2", "user", "OK et pour la roadmap, on commence par quoi ? Le repo ou la landing page ?", _unix(29), parent="a1")
    a2 = node("a2", "assistant", "Commencez par la landing page pour valider la demande, puis le repo.", _unix(29, 9), parent="u2", model="gpt-5")
    u1["children"] = ["a1"]
    a1["children"] = ["u2"]
    u2["children"] = ["a2"]
    # Regenerated answer branch (should be ignored in the active thread).
    a1_alt = node("a1-alt", "assistant", "Variante régénérée ignorée.", _unix(30, 16), parent="u1", model="gpt-4o")
    u1["children"].append("a1-alt")

    # Conversation 2: French small talk + question.
    t2 = _unix(3)
    u3 = node("u3", "user", "Comment tu dis en anglais : je viens de finir le diagnostic du véhicule ?", t2)
    a3 = node("a3", "assistant", "You would say: 'I've just finished diagnosing the vehicle.'", _unix(3, 10), parent="u3", model="gpt-5")
    u3["children"] = ["a3"]

    # Conversation 3: legacy flat-message format.
    legacy = {
        "conversation_id": "legacy-1",
        "title": "Recette tajine",
        "create_time": _unix(120),
        "update_time": _unix(120),
        "messages": [
            {"author": {"role": "user"}, "create_time": _unix(120), "content": {"parts": ["Donne-moi une recette de tajine marocain au poulet citron confit."]}},
            {"author": {"role": "assistant", "metadata": {"model_slug": "gpt-4"}}, "create_time": _unix(120), "content": {"parts": ["Voici une recette authentique…"]}},
        ],
    }

    return [
        {
            "conversation_id": "conv-1",
            "title": "Pricing MVP devis garages",
            "create_time": t1,
            "update_time": _unix(29),
            "current_node": "a2",
            "mapping": {"u1": u1, "a1": a1, "u2": u2, "a2": a2, "a1-alt": a1_alt},
        },
        {
            "conversation_id": "conv-2",
            "title": "Traduction véhicule",
            "create_time": t2,
            "update_time": t2,
            "current_node": "a3",
            "mapping": {"u3": u3, "a3": a3},
        },
        legacy,
    ]


def build_claude_export() -> tuple[dict, dict]:
    """Flat conversations.json + projects.json as produced by Claude exports."""
    t1 = _unix(10)
    t2 = _unix(2)
    conversations = [
        {
            "uuid": "cl-1",
            "name": "Atelier Odoo migration",
            "summary": "Plan a Odoo migration for a workshop",
            "project_uuid": "proj-1",
            "created_at": _iso(t1),
            "updated_at": _iso(_unix(9)),
            "chat_messages": [
                {"sender": "human", "text": "On prépare la migration Odoo de l'atelier pour le client, quel plan de déploiement ? Le livrable doit être prêt avant le deadline.", "created_at": _iso(t1)},
                {"sender": "assistant", "text": "Voici un plan de déploiement en trois phases…", "created_at": _iso(t1 + 60)},
                {"sender": "human", "text": "Et pour la facturation du devis client dans Odoo ?", "created_at": _iso(_unix(9))},
                {"sender": "assistant", "text": "Odoo permet de créer un devis directement depuis…", "created_at": _iso(_unix(9) + 60)},
            ],
        },
        {
            "uuid": "cl-2",
            "name": "Family trip Thailand",
            "created_at": _iso(t2),
            "updated_at": _iso(t2),
            "chat_messages": [
                {"sender": "human", "text": "Where should a family of four stay in Thailand for authentic markets?", "created_at": _iso(t2)},
                {"sender": "assistant", "text": "Chiang Mai is a great base for markets…", "created_at": _iso(t2 + 90)},
            ],
        },
    ]
    projects = [
        {"uuid": "proj-1", "name": "IAtelier Ops"},
        {"uuid": "proj-2", "name": "Firn Studio"},
    ]
    return conversations, projects


def write_zip(path: Path, entries: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            payload = json.dumps(data, ensure_ascii=False).encode() if not isinstance(data, str) else data.encode()
            zf.writestr(name, payload)


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)

    chatgpt = build_chatgpt_export()
    write_zip(
        FIXTURES / "chatgpt_export.zip",
        {"conversations.json": chatgpt, "user.json": {"name": "Test User", "email": "test@example.com"}},
    )

    claude_convs, projects = build_claude_export()
    write_zip(
        FIXTURES / "claude_export.zip",
        {
            "conversations.json": claude_convs,
            "projects.json": projects,
            "users.json": [{"uuid": "user-1", "email": "test@example.com"}],
        },
    )

    # Extracted-directory variants for dir-ingestion tests.
    import shutil

    for name, entries in (("chatgpt_dir", {"conversations.json": chatgpt, "user.json": {}}),
                          ("claude_dir", {"conversations.json": claude_convs, "projects.json": projects})):
        d = FIXTURES / name
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        for fname, data in entries.items():
            (d / fname).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    print(f"fixtures written to {FIXTURES}")


if __name__ == "__main__":
    main()
