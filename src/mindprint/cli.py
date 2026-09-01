"""mindprint CLI — analyze an official ChatGPT or Claude export, fully offline."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .analyze import analyze
from .export import write_json, write_markdown
from .ingest import Provider, UnsupportedExportError, ingest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mindprint",
        description=(
            "Turn your official ChatGPT/Claude data export into a structured self-profile. "
            "Everything runs locally; nothing is uploaded."
        ),
    )
    parser.add_argument("export", help="Path to the data-export ZIP (or extracted directory)")
    parser.add_argument(
        "-o", "--outdir", default="mindprint-output", help="Output directory (default: ./mindprint-output)"
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "chatgpt", "claude"],
        default="auto",
        help="Force the parser instead of auto-detection (use when an export was re-zipped or merged)",
    )
    parser.add_argument("--version", action="version", version=f"mindprint {__version__}")
    return parser


def _write_private(path: Path, write) -> Path:
    """Write an output file restricted to the owner (0o600) — it holds sensitive data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    export_path = Path(args.export)
    if not export_path.exists():
        print(f"error: path not found: {export_path}", file=sys.stderr)
        return 2
    try:
        conversations = ingest(export_path, provider=args.provider)
    except UnsupportedExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"error: could not read export: {exc}", file=sys.stderr)
        return 2

    profile = analyze(conversations)
    outdir = Path(args.outdir)
    json_path = _write_private(outdir / "mindprint.json", lambda p: write_json(profile, p))
    md_path = _write_private(outdir / "mindprint.md", lambda p: write_markdown(profile, p))

    if "error" in profile:
        print(f"⚠️ {profile['error']}")
        return 1

    summary = profile["summary"]
    print(f"✅ Parsed {summary['conversations']} conversations "
          f"({summary['user_messages']} user messages, {summary['assistant_messages']} assistant replies)")
    for source, st in profile["per_source"].items():
        print(f"   • {source}: {st['conversations']} conversations, {st['user_messages']} user messages")
    print(f"   JSON: {json_path}")
    print(f"   Markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
