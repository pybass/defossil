"""Codex CLI source: extracts typed messages from the rollout files under the sessions root."""

import json
from pathlib import Path
from typing import Any

from defossil.core.features.message.models import NewMessage, Source

SOURCE = Source.CODEX

# Codex feeds itself context as user-role items; these openings mark the ones nobody typed.
INJECTED_PREFIXES = ("# AGENTS.md instructions", "<environment_context>", "<user_instructions>", "<recommended_plugins>")


def collect_messages(sessions_dir: Path) -> list[NewMessage]:
    """Scan all rollout files and return every typed message from interactive sessions."""
    messages = []
    for file in sorted(sessions_dir.rglob("*.jsonl")):
        messages.extend(_file_messages(file))
    return messages


def _file_messages(file: Path) -> list[NewMessage]:
    """Parse one rollout file; only interactive (cli) sessions count — exec runs, defossil's own included, return nothing."""
    lines: list[dict[str, Any]] = []
    for raw in file.read_text(encoding="utf-8").splitlines():
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    session = next(((line.get("payload") or {}) for line in lines if line.get("type") == "session_meta"), {})
    if session.get("source") != "cli" or not session.get("id"):
        return []
    meta = {"project": session.get("cwd") or "", "session_id": session["id"]}
    # Codex up to 0.144 wrote the typed text twice, as an event and as a response item; 0.147 dropped the event and
    # gave the item an id. Reading the event where it exists keeps the injected blocks out without filtering text.
    events = [m for line in lines if (m := _from_event(line, session["id"], meta)) is not None]
    return events or [m for line in lines if (m := _from_item(line, meta)) is not None]


def _from_event(line: dict[str, Any], session_id: str, meta: dict[str, str]) -> NewMessage | None:
    """Build a message from a `user_message` event, or None for any other line; the event carries no id of its own."""
    payload = line.get("payload") or {}
    if line.get("type") != "event_msg" or payload.get("type") != "user_message":
        return None
    text = (payload.get("message") or "").strip()
    timestamp = line.get("timestamp")
    if not text or not timestamp:
        return None
    return NewMessage(source=SOURCE, source_key=f"{session_id}:{timestamp}", typed_at=timestamp, text=text, meta=meta)


def _from_item(line: dict[str, Any], meta: dict[str, str]) -> NewMessage | None:
    """Build a message from a user-role response item, or None when the line is not one, or holds injected context."""
    payload = line.get("payload") or {}
    if line.get("type") != "response_item" or payload.get("type") != "message" or payload.get("role") != "user":
        return None
    parts = [part.get("text") or "" for part in payload.get("content") or [] if part.get("type") == "input_text"]
    text = "\n".join(parts).strip()
    timestamp = line.get("timestamp")
    # An item without an id comes from a pre-0.147 rollout, where the event is the copy worth reading.
    if not text or not timestamp or not payload.get("id") or text.startswith(INJECTED_PREFIXES):
        return None
    return NewMessage(source=SOURCE, source_key=payload["id"], typed_at=timestamp, text=text, meta=meta)
