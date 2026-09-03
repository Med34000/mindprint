"""Test-suite: ingestion detection, parsing, analysis, export rendering."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
FIXTURES = TESTS / "fixtures"

from mindprint.analyze import analyze
from mindprint.export import to_markdown, write_json, write_markdown
from mindprint.ingest import UnsupportedExportError, ingest, parse_chatgpt, parse_claude


@pytest.fixture(scope="session", autouse=True)
def _fixtures():
    subprocess.run([sys.executable, str(TESTS / "make_fixtures.py")], check=True)


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def test_detect_chatgpt_zip():
    convs = ingest(FIXTURES / "chatgpt_export.zip")
    assert len(convs) == 3
    assert {c.source for c in convs} == {"chatgpt"}
    titles = {c.title for c in convs}
    assert "Pricing MVP devis garages" in titles


def test_detect_claude_zip():
    convs = ingest(FIXTURES / "claude_export.zip")
    assert len(convs) == 2
    assert {c.source for c in convs} == {"claude"}
    by_title = {c.title: c for c in convs}
    assert by_title["Atelier Odoo migration"].project == "IAtelier Ops"


def test_detect_extracted_dirs():
    assert len(ingest(FIXTURES / "chatgpt_dir")) == 3
    assert len(ingest(FIXTURES / "claude_dir")) == 2


def test_reject_unknown():
    with pytest.raises(UnsupportedExportError):
        ingest(FIXTURES / "does_not_exist.zip")


# ---------------------------------------------------------------------------
# ChatGPT tree reconstruction
# ---------------------------------------------------------------------------

def test_chatgpt_active_thread_skips_regenerated_branch():
    convs = parse_chatgpt(json.loads((FIXTURES / "chatgpt_dir" / "conversations.json").read_text()))
    conv1 = next(c for c in convs if c.id == "conv-1")
    texts = [m.text for m in conv1.messages]
    assert any("roadmap" in t for t in texts)
    assert not any("régénérée" in t for t in texts)  # branch excluded
    roles = [m.role for m in conv1.messages]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_chatgpt_legacy_flat_format():
    convs = parse_chatgpt(json.loads((FIXTURES / "chatgpt_dir" / "conversations.json").read_text()))
    legacy = next(c for c in convs if c.id == "legacy-1")
    assert [m.role for m in legacy.messages] == ["user", "assistant"]
    assert "tajine" in legacy.messages[0].text


def test_chatgpt_model_extracted():
    convs = parse_chatgpt(json.loads((FIXTURES / "chatgpt_dir" / "conversations.json").read_text()))
    conv1 = next(c for c in convs if c.id == "conv-1")
    assert any(m.model == "gpt-5" for m in conv1.assistant_messages())


# ---------------------------------------------------------------------------
# Claude parsing
# ---------------------------------------------------------------------------

def test_claude_roles_and_projects():
    data = json.loads((FIXTURES / "claude_dir" / "conversations.json").read_text())
    projects = json.loads((FIXTURES / "claude_dir" / "projects.json").read_text())
    names = {p["uuid"]: p["name"] for p in projects}
    convs = parse_claude(data, names)
    conv1 = next(c for c in convs if c.id == "cl-1")
    assert [m.role for m in conv1.messages] == ["user", "assistant", "user", "assistant"]
    assert conv1.project == "IAtelier Ops"
    assert conv1.messages[0].timestamp is not None


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _all_convs():
    return ingest(FIXTURES / "chatgpt_export.zip") + ingest(FIXTURES / "claude_export.zip")


def test_analyze_summary():
    profile = analyze(_all_convs())
    assert "error" not in profile
    s = profile["summary"]
    assert s["conversations"] == 5
    assert s["user_messages"] == 7  # chatgpt: 2+1+1, claude: 2+1
    assert s["first_activity"] is not None and s["last_activity"] is not None


def test_analyze_sources():
    profile = analyze(_all_convs())
    assert profile["per_source"]["chatgpt"]["conversations"] == 3
    assert profile["per_source"]["claude"]["conversations"] == 2


def test_analyze_topics_no_stopwords():
    profile = analyze(_all_convs())
    terms = [t["term"] for t in profile["topics"]["unigrams"]]
    assert terms and not (set(terms) & {"le", "the", "je", "de"})


def test_analyze_project_signals():
    profile = analyze(_all_convs())
    project_titles = [p["title"] for p in profile["projects"]]
    assert "Pricing MVP devis garages" in project_titles
    assert "Atelier Odoo migration" in project_titles


def test_analyze_style():
    profile = analyze(_all_convs())
    style = profile["style"]
    assert style["avg_user_message_chars"] > 0
    assert style["question_ratio"] > 0
    assert style["tutoiement_vs_vouvoiement"].count("/") == 1


def test_analyze_languages():
    profile = analyze(_all_convs())
    langs = profile["languages"]
    assert 0.0 <= langs["english_share"] <= 1.0
    assert abs(langs["english_share"] + langs["french_share"] - 1.0) < 0.01


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def test_markdown_renders_all_sections():
    profile = analyze(_all_convs())
    md = to_markdown(profile)
    for heading in ("Overview", "Top topics", "Project signals", "How you write"):
        assert heading in md
    assert "mindprint" in md  # footer attribution
    assert "█" in md  # activity bars


def test_markdown_error_case():
    assert "⚠️" in to_markdown({"error": "no parseable conversations found in export"})


def test_json_write(tmp_path: Path):
    profile = analyze(_all_convs())
    out = write_json(profile, tmp_path / "a" / "mindprint.json")
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["summary"]["conversations"] == 5


def test_markdown_write(tmp_path: Path):
    profile = analyze(_all_convs())
    out = write_markdown(profile, tmp_path / "mindprint.md")
    assert out.exists() and out.stat().st_size > 200


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_end_to_end(tmp_path: Path):
    outdir = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "-m", "mindprint.cli", str(FIXTURES / "chatgpt_export.zip"), "-o", str(outdir)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert (outdir / "mindprint.json").exists()
    assert (outdir / "mindprint.md").exists()
    assert "✅" in proc.stdout


def test_claude_content_blocks_tolerated():
    """Modern Claude exports use content block lists, not plain text."""
    data = [
        {
            "uuid": "cl-blocks",
            "name": "Blocks test",
            "chat_messages": [
                {"sender": "human", "created_at": "2026-08-01T10:00:00Z",
                 "content": [{"type": "text", "text": "Analyse ce CSV stp"},
                             {"type": "tool_use", "id": "t1", "name": "repl"},
                             {"type": "thinking", "thinking": "hidden reasoning"}]},
                {"sender": "assistant", "created_at": "2026-08-01T10:01:00Z",
                 "content": [{"type": "text", "text": "Voici l'analyse."}]},
            ],
        }
    ]
    convs = parse_claude(data)
    assert len(convs) == 1
    msgs = convs[0].messages
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert "tool_use" not in msgs[0].text and "hidden reasoning" not in msgs[0].text
    assert "CSV" in msgs[0].text


def test_claude_unknown_sender_and_missing_text_survive():
    data = [
        {
            "uuid": "cl-odd",
            "name": "Odd messages",
            "chat_messages": [
                {"sender": "system", "text": "should be skipped"},
                {"sender": "human", "content": [{"type": "tool_result"}]},  # no text at all
                {"sender": "human", "text": "vraie question"},
            ],
        }
    ]
    convs = parse_claude(data)
    assert [m.text for m in convs[0].messages] == ["vraie question"]


def test_chatgpt_cycle_guard_and_dangling_parent():
    data = [
        {
            "conversation_id": "cyc-1",
            "title": "Cycle test",
            "current_node": "a",
            "mapping": {
                "a": {"id": "a", "message": {"author": {"role": "user"},
                       "content": {"parts": ["hello"]}, "create_time": 1}, "parent": "b", "children": []},
                "b": {"id": "b", "message": None, "parent": "a", "children": ["a"]},  # cycle
                "orphan": {"id": "orphan", "message": {"author": {"role": "user"},
                            "content": {"parts": ["lost"]}, "create_time": 2}, "parent": "ghost", "children": []},
            },
        }
    ]
    convs = parse_chatgpt(data)
    assert len(convs) == 1
    texts = [m.text for m in convs[0].messages]
    assert texts == ["hello"]  # cycle broken, dangling parent stops walk


def test_provider_flag_forces_parser():
    convs = ingest(FIXTURES / "claude_export.zip", provider="claude")
    assert {c.source for c in convs} == {"claude"}


def test_mindprint_escape_in_markdown(tmp_path: Path):
    profile = {"summary": {}, "per_source": {}, "activity": {}, "topics": {}, "languages": {},
               "style": {}, "projects": [{"title": "**bold_ *weird* <script>alert(1)</script>",
                                          "source": "chatgpt", "project": None,
                                          "evidence_hits": 5, "last_touched": None}]}
    from mindprint.export import write_markdown
    out = write_markdown(profile, tmp_path / "x.md")
    content = out.read_text(encoding="utf-8")
    assert "\\*\\*bold" in content
    assert "<script>" not in content


def test_sharded_chatgpt_export(tmp_path: Path):
    """Modern ChatGPT exports split conversations across conversations-NNN.json shards."""
    from tests.make_fixtures import build_chatgpt_export, write_zip

    convs_data = build_chatgpt_export()
    half = len(convs_data) // 2
    zip_path = tmp_path / "sharded.zip"
    write_zip(
        zip_path,
        {
            "user.json": {"name": "T"},
            "conversations-000.json": convs_data[:half],
            "conversations-001.json": convs_data[half:],
        },
    )
    convs = ingest(zip_path)
    assert len(convs) == len(convs_data)
    assert {c.source for c in convs} == {"chatgpt"}


def test_dedupe_overlapping_exports():
    """Feeding the same export twice must not double-count conversations."""
    chatgpt = ingest(FIXTURES / "chatgpt_export.zip")
    profile = analyze(chatgpt + chatgpt)
    assert profile["summary"]["conversations"] == 3
    assert profile["summary"]["user_messages"] == 4


def test_multi_export_cli(tmp_path: Path):
    """CLI accepts several exports and reports per-source stats."""
    outdir = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "-m", "mindprint.cli",
         str(FIXTURES / "chatgpt_export.zip"), str(FIXTURES / "claude_export.zip"),
         "-o", str(outdir)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    profile = json.loads((outdir / "mindprint.json").read_text())
    assert set(profile["per_source"]) == {"chatgpt", "claude"}
    assert profile["summary"]["conversations"] == 5


def test_claude_per_file_projects_layout(tmp_path: Path):
    """New Claude manifest exports: projects/<uuid>.json instead of projects.json."""
    from tests.make_fixtures import build_claude_export

    convs, projects = build_claude_export()

    src = tmp_path / "claude_new"
    src.mkdir()
    (src / "conversations.json").write_text(json.dumps(convs), encoding="utf-8")
    (src / "projects").mkdir()
    for proj in projects:
        (src / "projects" / f"{proj['uuid']}.json").write_text(json.dumps(proj), encoding="utf-8")
    parsed = ingest(src)
    by_title = {c.title: c for c in parsed}
    assert by_title["Atelier Odoo migration"].project == "IAtelier Ops"


def test_memory_file_structure():
    """The memory file must be compact, dated, and AI-actionable."""
    from mindprint.memoryfile import build_memory_file

    profile = analyze(_all_convs())
    mf = build_memory_file(profile)
    assert "# User memory file" in mf
    assert "## Communication" in mf
    assert "## Active projects" in mf
    assert "Atelier Odoo migration" in mf
    assert "French" in mf or "French" in mf  # language guidance present
    assert "verify time-sensitive details" in mf  # staleness disclaimer
    assert len(mf) < 3500  # compact enough for a system prompt
    lines = [l for l in mf.splitlines() if l.startswith("- ")]
    assert len(lines) >= 4


def test_memory_file_error_case():
    from mindprint.memoryfile import build_memory_file

    mf = build_memory_file({"error": "no parseable conversations found in export"})
    assert "profile unavailable" in mf


def test_cli_writes_memory_file(tmp_path: Path):
    outdir = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "-m", "mindprint.cli", str(FIXTURES / "claude_export.zip"), "-o", str(outdir)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert (outdir / "memory-file.md").exists()


def test_hermes_db_parsing(tmp_path: Path):
    """Hermes state.db: only user/assistant text from interactive sources."""
    import sqlite3

    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE sessions (id TEXT, source TEXT, title TEXT, started_at REAL, last_activity_at REAL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL);
        INSERT INTO sessions VALUES ('s1', 'discord', 'Test chat', 100.0, 200.0);
        INSERT INTO sessions VALUES ('s2', 'cron', 'Automated job', 150.0, 180.0);
        INSERT INTO messages VALUES (1, 's1', 'user', 'Bonjour Hermes', 100.0);
        INSERT INTO messages VALUES (2, 's1', 'assistant', 'Salut !', 101.0);
        INSERT INTO messages VALUES (3, 's1', 'tool', '{"result": "payload"}', 101.5);
        INSERT INTO messages VALUES (4, 's2', 'user', 'automated prompt not the user', 150.0);
        """
    )
    conn.commit()
    conn.close()
    convs = ingest(db)
    assert len(convs) == 1  # cron session excluded
    assert [m.role for m in convs[0].messages] == ["user", "assistant"]
    assert convs[0].source == "hermes"
    assert "payload" not in " ".join(m.text for m in convs[0].messages)


def test_hermes_db_readonly(tmp_path: Path):
    """mindprint must refuse to create/modify a Hermes DB: mode=ro or bust."""
    db = tmp_path / "missing.db"  # mode=ro must fail on nonexistent file, not create it
    with pytest.raises(Exception):
        ingest(db)
    assert not db.exists()  # nothing was created


def test_vault_generation(tmp_path: Path):
    """Vault: dashboard indexes project notes; open-loops exclude chitchat."""
    from mindprint.vault import build_vault

    convs = _all_convs()
    from mindprint.analyze import analyze
    profile = analyze(convs)
    manifest = build_vault(convs, profile, tmp_path)
    dashboard = (tmp_path / "Dashboard.md").read_text(encoding="utf-8")
    assert "[[Atelier Odoo migration]]" in dashboard
    assert "Open-loops" in dashboard
    assert (tmp_path / "Memory-file.md").exists()
    assert any("Timeline/" in f for f in manifest["files"])
    openloops = (tmp_path / "Open-loops.md").read_text(encoding="utf-8")
    assert "je vais au lit" not in openloops


def test_cli_bad_path():
    proc = subprocess.run(
        [sys.executable, "-m", "mindprint.cli", "/nonexistent.zip"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
