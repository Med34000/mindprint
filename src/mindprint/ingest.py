"""Export ingestion: detect providers from an official data-export ZIP (or directory).

Each parser is responsible for exactly one provider format. Ingestion never
requires network access: users bring the ZIP they downloaded from their
provider's settings page. Entries are parsed in-memory with size caps (no
extraction to disk, so Zip-Slip/bomb risks stay out of scope by design).
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .schema import UnifiedConversation, UnifiedMessage

# Hard caps: real exports can carry huge user attachments; refuse rather than OOM.
MAX_ENTRY_BYTES = 512 * 1024 * 1024  # 512 MiB per JSON entry
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB across all read entries

Provider = Literal["auto", "chatgpt", "claude"]


class UnsupportedExportError(ValueError):
    """Raised when no parser recognizes the supplied archive or directory."""


def _load_zip_entry(zf: zipfile.ZipFile, name: str) -> object:
    """Read and json-parse a single ZIP entry in memory; None if absent/broken."""
    info = zf.getinfo(name)
    if info.file_size > MAX_ENTRY_BYTES:
        raise UnsupportedExportError(
            f"ZIP entry {name} is {info.file_size / 1e6:.0f} MB — above the "
            f"{MAX_ENTRY_BYTES // (1024 * 1024)} MB safety cap. Re-export without attachments."
        )
    try:
        with zf.open(name) as fh:
            return json.load(fh)
    except KeyError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# ChatGPT
# ---------------------------------------------------------------------------

def parse_chatgpt(data: object) -> list[UnifiedConversation]:
    """Parse ChatGPT conversations.json.

    Layout: array of conversations; messages live in a `mapping` tree keyed by
    node id, each node holding {message, parent, children}. The active thread is
    the path from current_node back to the root (regenerations/edits live on
    side branches and are intentionally excluded; legacy exports may instead
    use a flat list of messages).
    """
    if not isinstance(data, list):
        raise ValueError("ChatGPT conversations.json must be an array of conversations")
    out: list[UnifiedConversation] = []
    for conv in data:
        if not isinstance(conv, dict):
            continue
        title = str(conv.get("title") or "")
        conv_id = str(conv.get("conversation_id") or conv.get("id") or "")
        msgs = _chatgpt_messages_from_tree(conv) or _chatgpt_messages_flat(conv)
        out.append(
            UnifiedConversation(
                source="chatgpt",
                id=conv_id,
                title=title,
                created_at=conv.get("create_time"),
                updated_at=conv.get("update_time"),
                messages=msgs,
            )
        )
    return out


def _chatgpt_messages_from_tree(conv: dict) -> list:
    """Reconstruct the active thread by walking current_node up through parents.

    Guards: missing current_node falls back to latest-by-time; dangling parent
    ids stop the walk; a visited set prevents pathological cycles.
    """
    mapping = conv.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        return []
    current = conv.get("current_node")
    node = mapping.get(current) if current is not None else None
    if node is None:
        def _sort_key(item):
            msg = item[1].get("message") or {}
            return msg.get("create_time") or 0.0

        latest_id, node = max(mapping.items(), key=_sort_key)
    chain = []
    visited: set[str] = set()
    while isinstance(node, dict):
        node_id = node.get("id")
        if node_id is not None:
            if node_id in visited:
                break  # cycle guard
            visited.add(str(node_id))
        message = node.get("message")
        if message:
            chain.append(message)
        parent = node.get("parent")
        node = mapping.get(parent) if parent is not None else None
    chain.reverse()
    return [m for m in (_chatgpt_message(m) for m in chain) if m is not None]


def _chatgpt_message(message: dict):
    """Convert one ChatGPT message node, or return None when contentless.

    Only string parts are kept: images/attachments/tool payloads are skipped,
    not crashed on.
    """
    author = message.get("author") or {}
    role = author.get("role")
    content = message.get("content") or {}
    parts = content.get("parts")
    text = "\n".join(p for p in parts if isinstance(p, str)) if isinstance(parts, list) else ""
    if not text and isinstance(content.get("text"), str):
        text = content["text"]
    if not role or not text.strip():
        return None
    metadata = author.get("metadata")
    return UnifiedMessage(
        role=str(role),
        text=text,
        timestamp=message.get("create_time"),
        model=metadata.get("model_slug") if isinstance(metadata, dict) else None,
    )


def _chatgpt_messages_flat(conv: dict) -> list:
    """Legacy layout: some old exports store a flat message list."""
    msgs_raw = conv.get("messages")
    if not isinstance(msgs_raw, list):
        return []
    out = []
    for m in msgs_raw:
        if isinstance(m, dict):
            conv_msg = _chatgpt_message(m)
            if conv_msg is not None:
                out.append(conv_msg)
    return out


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

def _claude_text(message: dict) -> str:
    """Extract text from a Claude message: `text` string or `content` block list.

    Anthropic keeps adding block types (thinking, tool_use, tool_result,
    web_search…). Only text blocks are read; anything else is skipped safely.
    """
    text = message.get("text")
    if isinstance(text, str) and text.strip():
        return text
    blocks = message.get("content")
    if isinstance(blocks, list):
        chunks = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                chunks.append(block["text"])
        return "\n".join(chunks)
    return ""


def parse_claude(data: object, project_names: dict | None = None) -> list[UnifiedConversation]:
    """Parse Claude conversations.json (+ optional projects.json name map).

    Layout: flat array; each conversation has chat_messages (sender
    human/assistant, text or content blocks), uuid, name, created_at, and
    optionally project_uuid linking to projects.json. Unknown senders and
    missing fields degrade gracefully instead of dropping the conversation.
    """
    if not isinstance(data, list):
        raise ValueError("Claude conversations.json must be an array of conversations")
    project_names = project_names or {}

    out: list[UnifiedConversation] = []
    for conv in data:
        if not isinstance(conv, dict):
            continue
        msgs = []
        for m in conv.get("chat_messages") or []:
            if not isinstance(m, dict):
                continue
            sender = m.get("sender")
            role = {"human": "user", "assistant": "assistant"}.get(sender)
            text = _claude_text(m)
            if role and text.strip():
                msgs.append(
                    UnifiedMessage(
                        role=role,
                        text=text,
                        timestamp=_claude_time(m.get("created_at")),
                    )
                )
        puuid = conv.get("project_uuid")
        out.append(
            UnifiedConversation(
                source="claude",
                id=str(conv.get("uuid") or ""),
                title=str(conv.get("name") or ""),
                created_at=_claude_time(conv.get("created_at")),
                updated_at=_claude_time(conv.get("updated_at")),
                project=project_names.get(puuid) if isinstance(puuid, str) else None,
                messages=msgs,
            )
        )
    return out


def _claude_time(value) -> float | None:
    """Claude timestamps are ISO-8601 strings; numeric seconds also tolerated."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Hermes (local SQLite session DB)
# ---------------------------------------------------------------------------

# Session sources that carry the user's own voice. cron/subagent/api_server
# sessions contain automated prompts and agent-internal traffic, not the user.
HERMES_USER_SOURCES = frozenset({"cli", "tui", "desktop", "discord", "telegram"})

# Non-prose tokens that leak into Hermes messages: media attachments, URLs,
# filesystem paths from tool output. Stripped before analysis. Patterns are
# linear-time and applied only to the first 4 000 chars — tool-output blobs
# can be megabytes long and only their head ever carries paths/media markers.
_HERMES_NOISE_RE = [
    re.compile(r"MEDIA:\S+"),
    re.compile(r"https?://\S+"),
    re.compile(r"\S*/(?:opt|usr|home|tmp|var)/[^\s\"']*"),
    re.compile(r"\b[0-9a-f]{8,}\b"),
]
_HERMES_CLEAN_HEAD = 4000


def _clean_hermes_text(text: str) -> str:
    # Bound the contribution of any single message: huge blobs are tool-output
    # transcripts whose tail is pure noise for a prose profile. Cleaning runs
    # on the head only — patterns above stay off the unbounded tail.
    head, tail = text[:_HERMES_CLEAN_HEAD], ""
    for pattern in _HERMES_NOISE_RE:
        head = pattern.sub(" ", head)
    return re.sub(r"[ \t]+", " ", head).strip()


def parse_hermes(db_path: str | Path) -> list[UnifiedConversation]:
    """Parse a Hermes state.db (SQLite) in READ-ONLY mode.

    Hermes stores sessions locally in SQLite — no export flow exists, the
    database *is* the export. Layout: sessions(id, source, title, started_at,
    last_activity_at) + messages(session_id, role, content, timestamp) with
    role in user/assistant/tool/session_meta. Only user/assistant text is
    kept; tool payloads and session metadata are skipped.

    The connection is opened read-only (`mode=ro`) — mindprint never writes
    to a live assistant database.
    """
    import sqlite3

    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            """
            SELECT s.id, s.title, s.started_at, s.last_activity_at, s.source,
                   m.role, m.content, m.timestamp
            FROM sessions s JOIN messages m ON m.session_id = s.id
            WHERE s.source IN ({placeholders})
              AND m.role IN ('user', 'assistant')
              AND m.content IS NOT NULL
            ORDER BY s.started_at, m.timestamp, m.id
            """.format(placeholders=",".join("?" * len(HERMES_USER_SOURCES))),
            tuple(sorted(HERMES_USER_SOURCES)),
        ).fetchall()
    finally:
        conn.close()

    convs: dict[str, UnifiedConversation] = {}
    order: list[str] = []
    for sid, title, started, last_activity, source, role, content, ts in rows:
        text = _clean_hermes_text(str(content))
        if len(text) < 2:
            continue
        conv = convs.get(sid)
        if conv is None:
            conv = UnifiedConversation(
                source="hermes",
                id=str(sid),
                title=str(title or "(untitled)"),
                created_at=started,
                updated_at=last_activity,
            )
            convs[sid] = conv
            order.append(sid)
        conv.messages.append(
            UnifiedMessage(
                role="user" if role == "user" else "assistant",
                text=text,
                timestamp=ts,
            )
        )
    return [convs[k] for k in order if convs[k].messages]


# ---------------------------------------------------------------------------
# Claude Code (local JSONL session logs)
# ---------------------------------------------------------------------------

# Claude Code logs every coding-agent session to ~/.claude/projects/<slug>/*.jsonl
# — continuously, no manual export. Each line is a JSON event:
#   {type: "user"|"assistant"|"summary"|..., sessionId, timestamp, cwd,
#    isSidechain: bool, message: {role, content: str | [blocks]}, uuid}
# Sidechains are subagent transcripts (not the user's voice) and are skipped,
# as are summary/snapshot/meta lines.

def _cc_text(message: dict) -> str:
    """Extract user/assistant prose from a Claude Code message payload."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                chunks.append(block["text"])
        return "\n".join(chunks)
    return ""


def parse_claude_code(paths: list[Path]) -> list[UnifiedConversation]:
    """Parse Claude Code JSONL session files into unified conversations."""
    convs: dict[str, UnifiedConversation] = {}
    order: list[str] = []
    for path in paths:
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or event.get("type") not in ("user", "assistant"):
                    continue
                if event.get("isSidechain"):
                    continue
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                text = _cc_text(message).strip()
                if not text:
                    continue
                sid = str(event.get("sessionId") or path.stem)
                conv = convs.get(sid)
                if conv is None:
                    # Project name: the parent directory slug, or the cwd.
                    slug = path.parent.name if path.parent.name not in ("projects", ".") else ""
                    cwd = str(event.get("cwd") or "").replace("\\", "/").rstrip("/").split("/")[-1]
                    conv = UnifiedConversation(
                        source="claude-code",
                        id=sid,
                        title=slug or cwd or "(session claude-code)",
                        created_at=_cc_time(event.get("timestamp")),
                    )
                    convs[sid] = conv
                    order.append(sid)
                ts = _cc_time(event.get("timestamp"))
                if ts and (conv.updated_at is None or ts > conv.updated_at):
                    conv.updated_at = ts
                conv.messages.append(
                    UnifiedMessage(
                        role="user" if event["type"] == "user" else "assistant",
                        text=text[:8000],  # bound giant paste/tool echoes
                        timestamp=ts,
                    )
                )
    return [convs[k] for k in order if convs[k].messages]


def _cc_time(value) -> float | None:
    if value is None:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def ingest(path: str | Path, provider: Provider = "auto") -> list[UnifiedConversation]:
    """Load an official export ZIP (or extracted directory).

    provider="auto" detects the format from archive markers (falling back to
    structural probing on truly ambiguous exports); explicit "chatgpt"/"claude"
    skips detection entirely.
    """
    path = Path(path)
    # Hermes session DB: a local SQLite file, no ZIP involved.
    if path.is_file() and path.suffix == ".db":
        return parse_hermes(path)
    # Claude Code: a .jsonl session file or a directory of them.
    if path.is_file() and path.suffix == ".jsonl":
        return parse_claude_code([path])
    if path.is_dir() and any(path.glob("*.jsonl")):
        return parse_claude_code(sorted(path.glob("*.jsonl")))
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            return _ingest_from_names(zf, names, provider)
    if path.is_dir():
        names = {str(p.relative_to(path)) for p in path.rglob("*") if p.is_file()}
        return _ingest_from_names(path, names, provider)
    raise UnsupportedExportError(f"Not a ZIP export or directory: {path}")


def _ingest_from_names(source, names: set[str], provider: Provider) -> list[UnifiedConversation]:
    # Modern ChatGPT exports shard conversations across conversations-000.json, -001.json…
    conv_entries = _find_conversation_entries(names)
    if not conv_entries:
        raise UnsupportedExportError(
            "No conversations.json (or conversations-NNN.json shards) found — "
            "is this an official ChatGPT or Claude data export?"
        )
    data: object = []
    for entry in conv_entries:
        chunk = _load(source, entry)
        if isinstance(chunk, list):
            data.extend(chunk)
        elif chunk is not None:
            data = chunk  # single non-sharded payload
            break
    if not data:
        raise UnsupportedExportError("conversations file(s) empty or not valid JSON")

    if provider == "chatgpt":
        return parse_chatgpt(data)
    if provider == "claude":
        return parse_claude(data, _claude_project_names(source, names))

    # Auto-detect: Claude carries markers ChatGPT never has, and vice versa.
    is_claude = any(
        _find_entry(names, m) or any(n.startswith("projects/") or "/projects/" in n for n in names)
        for m in ("projects.json", "users.json", "memories.json")
    )
    is_chatgpt = any(
        _find_entry(names, m)
        for m in ("user.json", "model_comparisons.json", "shared_conversations.json", "chat.html")
    )
    if is_claude and not is_chatgpt:
        return parse_claude(data, _claude_project_names(source, names))
    if is_chatgpt and not is_claude:
        return parse_chatgpt(data)
    # Ambiguous (e.g. re-zipped, merged or renamed exports): probe structure.
    return _probe_both(data)


def _probe_both(data: object) -> list[UnifiedConversation]:
    """Try both parsers; decide by yield, then by structural signature."""
    try:
        chatgpt = parse_chatgpt(data)
    except ValueError:
        chatgpt = []
    try:
        claude = parse_claude(data)
    except ValueError:
        claude = []
    chatgpt_ok = any(c.messages for c in chatgpt)
    claude_ok = any(c.messages for c in claude)
    if chatgpt_ok and not claude_ok:
        return chatgpt
    if claude_ok and not chatgpt_ok:
        return claude
    if chatgpt_ok and claude_ok:
        # Claude conversations carry chat_messages with sender fields.
        if isinstance(data, list) and data and isinstance(data[0], dict):
            msgs = data[0].get("chat_messages")
            if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict) and "sender" in msgs[0]:
                return claude
        return chatgpt
    raise UnsupportedExportError("conversations.json recognized but no parseable conversations found")


def _claude_project_names(source, names: set[str]) -> dict:
    """Collect project uuid→name maps from both known layouts.

    Old: single projects.json containing a list of project objects.
    New (manifest exports): a projects/ directory with one JSON file per
    project, each a bare object {uuid, name, …}.
    """
    out: dict = {}
    entry = _find_entry(names, "projects.json")
    if entry is not None:
        data = _load(source, entry)
        if isinstance(data, list):
            out.update({p.get("uuid"): p.get("name") for p in data if isinstance(p, dict) and p.get("uuid")})
    import re

    per_file = sorted(
        n for n in names if re.search(r"(^|/)projects/[^/]+\.json$", n) and not n.endswith("/projects.json")
    )
    for name in per_file:
        data = _load(source, name)
        if isinstance(data, dict) and data.get("uuid"):
            out[data["uuid"]] = data.get("name")
    return out


def _find_conversation_entries(names: set[str]) -> list[str]:
    """All conversation payloads, shards included, in stable order.

    Matches conversations.json plus the newer sharded layout
    (conversations-000.json, conversations-001.json, …), at any directory depth.
    """
    import re

    pattern = re.compile(r"(^|/)conversations(-\d+)?\.json$")
    matches = sorted(n for n in names if pattern.search(n))
    # Single canonical file wins over nothing else; shards sort naturally by index.
    return matches


def _find_entry(names: set[str], filename: str) -> str | None:
    """Match an entry by basename, whatever directory prefix wraps it."""
    matches = sorted(n for n in names if n == filename or n.replace("\\", "/").endswith("/" + filename))
    return matches[0] if matches else None


def _load(source, entry: str):
    if isinstance(source, zipfile.ZipFile):
        return _load_zip_entry(source, entry)
    try:
        with open(Path(source) / entry, "rb") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
