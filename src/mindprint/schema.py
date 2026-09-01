"""Unified, provider-agnostic conversation schema.

Every parser normalizes its provider's export into these dataclasses, so the
analysis layer never needs to know where a conversation came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class UnifiedMessage:
    """A single message inside a conversation."""

    role: str  # "user" | "assistant" | "system" | "tool"
    text: str
    timestamp: float | None = None  # unix seconds (UTC)
    model: str | None = None  # model that produced the message, when known


@dataclass
class UnifiedConversation:
    """A conversation normalized from any provider export."""

    source: str  # "chatgpt" | "claude"
    id: str
    title: str
    created_at: float | None = None
    updated_at: float | None = None
    project: str | None = None  # Claude project name, when the chat belongs to one
    messages: list[UnifiedMessage] = field(default_factory=list)

    def user_messages(self) -> list[UnifiedMessage]:
        return [m for m in self.messages if m.role == "user"]

    def assistant_messages(self) -> list[UnifiedMessage]:
        return [m for m in self.messages if m.role == "assistant"]

    def iter_user_text(self) -> Iterator[str]:
        for m in self.user_messages():
            if m.text.strip():
                yield m.text


def empty_stats() -> dict:
    """Shared shape for per-source counters in profile output."""
    return {"conversations": 0, "user_messages": 0, "assistant_messages": 0}
