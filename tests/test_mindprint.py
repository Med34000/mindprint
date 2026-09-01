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


def test_cli_bad_path():
    proc = subprocess.run(
        [sys.executable, "-m", "mindprint.cli", "/nonexistent.zip"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
