"""Claude Code source: extracts human-typed messages from the per-project transcript files."""

import json
from pathlib import Path
from typing import Any

from defossil.core.features.message.models import NewMessage, Source

SOURCE = Source.CLAUDE_CODE


def collect_messages(projects_dir: Path) -> list[NewMessage]:
    """Scan all transcript files and return every human-typed message, nothing programmatic or injected."""
    messages = []
    for file in sorted(projects_dir.glob("*/*.jsonl")):
        for raw in file.read_text(encoding="utf-8").splitlines():
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue
            message = _to_message(line, file.parent.name)
            if message is not None:
                messages.append(message)
    return messages


def _to_message(line: dict[str, Any], fallback_project: str) -> NewMessage | None:
    """Build a message from a transcript line, or None when the line is not a human-typed message."""
    # origin.kind marks modern lines; promptSource alone covers a few older ones that lack origin. Dropping
    # non-human lines also keeps defossil's own `claude -p` review calls out of the archive.
    human = (line.get("origin") or {}).get("kind") == "human" or line.get("promptSource") in ("typed", "queued")
    if line.get("type") != "user" or line.get("isMeta") is True or line.get("isSidechain") is True or not human:
        return None
    content = (line.get("message") or {}).get("content")
    if not isinstance(content, str):  # block arrays are tool results and images, never typed text
        return None
    text = content.strip()
    if not text or text.startswith("[Request interrupted"):
        return None
    if not (line.get("uuid") and line.get("sessionId") and line.get("timestamp")):
        return None
    meta = {"project": line.get("cwd") or fallback_project, "session_id": line["sessionId"]}
    return NewMessage(source=SOURCE, source_key=line["uuid"], typed_at=line["timestamp"], text=text, meta=meta)
