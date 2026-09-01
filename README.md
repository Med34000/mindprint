# 🧠 mindprint

**Turn your AI chat exports into a structured self-profile — 100% locally.**

Your ChatGPT and Claude histories contain months of your projects, decisions, writing style and priorities — scattered across corporate silos. **mindprint** merges them into one profile file you own: topics, active projects, activity timeline, writing style. And unlike cloud alternatives, **not a single byte leaves your machine**.

> Proton's [AI Paper Trail](https://proton.me/lumo/ai/ai-paper-trail) showed the demand: people want to see what their AI knows about them. But it uploads your export to their servers and covers only ChatGPT/Claude. mindprint is the open-source, fully offline counterpart — and its output is a **reusable profile**, not a one-shot gimmick report.

## ✨ What you get

- **Topics** — the words and phrases you actually use, ranked
- **🚀 Project signals** — conversations carrying project evidence (MVP, pricing, deadline, client…), with Claude project names when available
- **Activity timeline** — conversations per month, busiest hours
- **✍️ Writing style** — message length, question ratio, tu/vous usage
- **🌍 Language mix** — EN/FR heuristic
- **JSON + Markdown** output: the JSON is machine-readable (feed it to any AI as system context — your own "memory file"), the Markdown is human-readable

## 🔒 Privacy model

| | mindprint | Cloud tools |
|---|---|---|
| Upload of your export | ❌ never | ✅ |
| Account required | ❌ | ✅ |
| Network access at runtime | ❌ none | required |
| Code auditable | ✅ MIT, this repo | ❌ |

The analysis runs on pure local statistics by default. No telemetry, no calls home, no model required. (An optional opt-in LLM-enrichment layer via your own local Ollama endpoint is on the roadmap — it would send data only to the endpoint *you* configure.)

## 📦 Install

```bash
pip install .          # from a clone of this repo
# or
uv pip install -e ".[dev]"   # development setup
```

Requires Python ≥ 3.10. No runtime dependencies.

## 🚀 Usage

1. **Export your data** from your provider (official export, takes minutes to hours):
   - **ChatGPT**: Settings → Data controls → Export data → download the ZIP from the email link
   - **Claude**: Settings → Privacy → Export data → download the ZIP from the email link
2. **Run mindprint** on the ZIP:

```bash
mindprint ~/Downloads/chatgpt-export.zip
# ✅ Parsed 412 conversations (3891 user messages, 7602 assistant replies)
#   JSON: mindprint-output/mindprint.json
#   Markdown: mindprint-output/mindprint.md
```

Extracted the ZIP already? Point mindprint at the directory instead — both work.

## 🗂 Supported formats

| Provider | Status | Notes |
|---|---|---|
| ChatGPT | ✅ | Handles the `mapping` tree (regenerations/edits excluded — active thread only) and legacy flat layouts |
| Claude | ✅ | Reads `conversations.json` + `projects.json` (project names attached) |
| Gemini | 🧭 planned | via Google Takeout |
| Grok | 🧭 planned | via X archive |

## 🧪 Development

```bash
uv pip install -e ".[dev]"
python -m pytest tests/
```

The test-suite generates synthetic export fixtures mimicking the real official formats (`tests/make_fixtures.py`) — no real personal data is ever committed.

## 🗺 Roadmap

- [ ] v0.1 — ChatGPT + Claude ingestion, statistical profile (this release)
- [ ] v0.2 — optional LLM enrichment (local Ollama, opt-in)
- [ ] v0.3 — Gemini (Takeout) + Grok (X archive)
- [ ] v0.4 — timeline diffing: "what changed in my focus this month?"
- [ ] Desktop app packaging

## ⚠️ Ethics

mindprint is for **your own data, on your own machine**. Analyzing another person's export without their consent is illegal profiling (GDPR art. 6 and equivalents) — don't, and we won't help.

## License

MIT
