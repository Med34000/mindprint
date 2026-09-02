"""Build the public demo assets from a fully synthetic corpus.

Generates a fictional 3-year multi-source history (two providers), runs the
real mindprint CLI on it, and produces:
  - example/mindprint.example.json / .md  (linked from the README)
  - docs/terminal.svg                     (README hero screenshot, real output)

Everything here is fabricated data — no real person's export is ever used.

Run from the repo root:  python scripts/make_demo_profile.py
"""

from __future__ import annotations

import html
import json
import random
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PERSONAS = [
    ("Renovation", ["cabinet", "plywood", "sander", "finish", "jig", "workbench"],
     ["How do I get a clean finish on plywood shelves?",
      "Best jig for repeated drawer dovetails?",
      "My sander leaves swirls, what am I doing wrong?"]),
    ("Sourdough", ["starter", "hydration", "crumb", "oven", "levain", "autolyse"],
     ["My starter smells like acetone, should I feed it more?",
      "75% hydration dough too sticky to shape, help?",
      "Dutch oven vs baking steel for crust?"]),
    ("Freelance admin", ["invoice", "quote", "client", "taxes", "contract", "deposit"],
     ["Draft a polite late-payment reminder for an invoice 30 days overdue",
      "Should I ask for a 30% deposit on a 2-week project?",
      "How do I price a website for a small bakery?"]),
    ("Family trip Japan", ["kyoto", "rail pass", "ryokan", "luggage", "onsen", "itinerary"],
     ["7-day itinerary with two kids: Kyoto or Osaka as base?",
      "Is the JR pass still worth it for Tokyo-Kyoto-Osaka?",
      "Ryokan with private onsen that accepts children?"]),
    ("Python side project", ["pandas", "venv", "script", "csv", "cron", "plot"],
     ["My pandas merge duplicates rows, what am I missing?",
      "Simplest way to schedule a script every morning on Linux?",
      "Matplotlib: bar chart with month labels, quick example?"]),
    ("Cycling training", ["zone 2", "ftp", "route", "cadence", "gran fondo", "recovery"],
     ["How do I structure a week with 6 hours available?",
      "Zone 2 feels too easy — am I doing it right?",
      "Best 60km route with a climb near Grenoble?"]),
    ("Apartment hunt", ["visit", "lease", "deposit", "neighborhood", "landlord", "surface"],
     ["Red flags to check during a first apartment visit?",
      "Is a 3x income rule still standard for leases?",
      "How to negotiate rent on a unit listed for 60 days?"]),
]

ASSISTANT_FILLER = [
    "Great question — let's break it down step by step.",
    "Here's a concrete plan you can apply this week.",
    "The short answer: yes, with a few caveats.",
    "Let's compare the three options on cost, effort, and risk.",
    "Based on what you described, start with the smallest testable version.",
]


def _ts(month_offset: int, day: int, hour: int) -> float:
    base = datetime(2023, 3, 1, tzinfo=timezone.utc)
    dt = base + timedelta(days=30 * month_offset + day, hours=hour - 12)
    return dt.timestamp()


def build_synthetic_exports(dest: Path, seed: int = 42) -> tuple[Path, Path]:
    """Create two extracted exports (ChatGPT-like + Claude-like) of fictional chats."""
    rng = random.Random(seed)
    gpt_dir, claude_dir = dest / "demo_chatgpt", dest / "demo_claude"
    gpt_dir.mkdir(parents=True)
    claude_dir.mkdir(parents=True)

    gpt_convs, claude_convs = [], []
    n_months = 41  # 2023-03 .. 2026-08
    for m in range(n_months):
        for _ in range(rng.randint(6, 13)):
            persona, lexicon, questions = rng.choice(PERSONAS)
            n_msgs = rng.randint(3, 7)
            day, hour = rng.randint(0, 27), rng.choice([9, 12, 16, 18, 20, 21, 22, 23])
            t0 = _ts(m, day, hour)
            title = f"{persona}: {rng.choice(lexicon)}"
            conv_id = f"demo-{m}-{rng.randrange(10**8)}"
            msgs = []
            for i in range(n_msgs):
                role = "user" if i % 2 == 0 else "assistant"
                if role == "user":
                    text = rng.choice(questions) if rng.random() < 0.6 else \
                        f"My take on {rng.choice(lexicon)}: I want to keep it simple and cheap."
                else:
                    text = rng.choice(ASSISTANT_FILLER) + f" ({persona} · {rng.choice(lexicon)})"
                msgs.append({"author": {"role": role}, "create_time": t0 + i * 90,
                             "content": {"parts": [text]}})
            payload = {"conversation_id": conv_id, "title": title,
                       "create_time": t0, "update_time": t0 + n_msgs * 90,
                       "messages": msgs}
            gpt_convs.append(payload)
            claude_payload = {
                "uuid": conv_id.replace("demo", "cl"), "name": title,
                "created_at": datetime.fromtimestamp(t0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "chat_messages": [
                    {"sender": "human" if mm["author"]["role"] == "user" else "assistant",
                     "text": mm["content"]["parts"][0],
                     "created_at": datetime.fromtimestamp(mm["create_time"], tz=timezone.utc)
                     .strftime("%Y-%m-%dT%H:%M:%S.000Z")}
                    for mm in msgs
                ],
            }
            # Split the load between the two providers, slightly favouring chatgpt.
            if rng.random() < 0.62:
                gpt_convs.append(payload)
            else:
                claude_convs.append(claude_payload)

    (gpt_dir / "conversations.json").write_text(json.dumps(gpt_convs), encoding="utf-8")
    (gpt_dir / "user.json").write_text(json.dumps({"name": "Alex (synthetic)"}), encoding="utf-8")
    (claude_dir / "conversations.json").write_text(json.dumps(claude_convs), encoding="utf-8")
    (claude_dir / "users.json").write_text(json.dumps([{"uuid": "u-demo", "email": "alex@example.invalid"}]),
                                           encoding="utf-8")
    return gpt_dir, claude_dir


def render_terminal_svg(lines: list[str], out: Path, width: int = 760) -> None:
    """Minimal dark terminal window with the real CLI output, as an SVG."""
    lh, pad, title_h = 21, 22, 44
    height = title_h + pad + lh * len(lines) + pad
    esc = [html.escape(l).replace(" ", "&#160;") for l in lines]

    def color_for(line: str) -> str:
        if line.startswith("$"):
            return "#a6e3a1"
        if "✅" in line or "Parsed" in line:
            return "#cdd6f4"
        if line.strip().startswith(("•", "JSON", "Markdown")):
            return "#89b4fa"
        if line.startswith(("#", "##")):
            return "#f5c2e7"
        return "#bac2de"

    body = []
    y = title_h + pad + 14
    for raw, line in zip(lines, esc):
        body.append(
            f'<text x="{pad}" y="{y}" font-family="ui-monospace,\'SF Mono\',Menlo,Consolas,monospace" '
            f'font-size="13.5" fill="{color_for(raw)}">{line}</text>'
        )
        y += lh

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" rx="12" fill="#1e1e2e"/>
  <rect width="{width}" height="{title_h}" rx="12" fill="#181825"/>
  <rect y="{title_h - 12}" width="{width}" height="12" fill="#181825"/>
  <circle cx="24" cy="{title_h // 2}" r="6" fill="#f38ba8"/>
  <circle cx="44" cy="{title_h // 2}" r="6" fill="#f9e2af"/>
  <circle cx="64" cy="{title_h // 2}" r="6" fill="#a6e3a1"/>
  <text x="{width // 2}" y="{title_h // 2 + 4}" text-anchor="middle"
        font-family="ui-monospace,Menlo,monospace" font-size="12" fill="#6c7086">terminal — mindprint (demo data)</text>
  {"".join(body)}
</svg>'''
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        gpt_dir, claude_dir = build_synthetic_exports(Path(tmp))
        outdir = Path(tmp) / "out"
        proc = subprocess.run(
            [sys.executable, "-m", "mindprint.cli", str(gpt_dir), str(claude_dir), "-o", str(outdir)],
            capture_output=True, text=True, cwd=ROOT,
        )
        if proc.returncode != 0:
            sys.exit(proc.stderr)

        example_dir = ROOT / "example"
        example_dir.mkdir(exist_ok=True)
        header = """# 🧪 Example output

This profile was generated from a **fully synthetic corpus** (fictional user "Alex",
see `scripts/make_demo_profile.py`) — no real personal data is in this file.

Run mindprint on your own export to get yours, 100% locally.

---

"""
        (example_dir / "mindprint.example.md").write_text(
            header + (outdir / "mindprint.md").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (example_dir / "mindprint.example.json").write_text(
            (outdir / "mindprint.json").read_text(encoding="utf-8"), encoding="utf-8"
        )

        md_lines = (outdir / "mindprint.md").read_text(encoding="utf-8").splitlines()
        terminal_lines = (
            ["$ pip install .",
             "$ mindprint demo_chatgpt/ demo_claude/ -o my-profile"]
            + [l for l in proc.stdout.strip().splitlines()]
            + ["", "$ head -24 my-profile/mindprint.md"]
            + md_lines[:24]
        )
        render_terminal_svg(terminal_lines, ROOT / "docs" / "terminal.svg")

    print(f"example + docs/terminal.svg written; stdout was:\n{proc.stdout}")


if __name__ == "__main__":
    main()
