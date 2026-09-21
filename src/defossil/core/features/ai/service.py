import json
import logging
import re
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, assert_never

from pydantic import ValidationError

from defossil.core.errors import AiError, NotFoundError
from defossil.core.features.ai.backends import build_backend
from defossil.core.features.ai.models import AiCall, AiResponse, PromptCategory
from defossil.core.features.correction.models import CATEGORIES, Correction, NewCorrection
from defossil.core.features.report.models import ReportChatTurn
from defossil.core.features.setting.models import Settings
from defossil.core.service import Service

if TYPE_CHECKING:
    from defossil.core.core import Core

logger = logging.getLogger(__name__)


def _load_prompt(name: str) -> str:
    """Return the text of `prompts/<name>.md` — every prompt the app sends lives in that folder next to this module."""
    return (Path(__file__).parent / "prompts" / f"{name}.md").read_text()


# Nothing marks a stored correction stale when a prompt changes: a message is reviewed once, ever.
# Filled with replace, not str.format — the templates' JSON examples are full of literal braces.
REVIEW_PROMPT_TEMPLATE = _load_prompt("review")  # {native_language} and {categories} filled per call
EXPLAIN_PROMPT_TEMPLATE = _load_prompt("explain")  # {native_language} filled per call
REPORT_PROMPT_TEMPLATE = _load_prompt("report")  # every {name} is the Settings field of that name, filled per call
CHAT_PROMPT_TEMPLATE = _load_prompt("chat")  # {native_language} filled per call; opens a chat session, sent once per session


class AiService(Service):
    """Owner of the `ai_calls` table and of every prompt: it builds each ask, runs and logs the call, parses the answer.

    Also holds the auto-AI switch — the one flag that gates unattended spending. The review and report workers
    check it; user-initiated asks (explains, chats) ignore it, so nothing here enforces it on `send_*`.
    """

    def __init__(self, core: Core) -> None:
        """Bind to Core; auto AI starts off — not persisted, every launch must be switched on by hand."""
        super().__init__(core)
        self._auto_ai = threading.Event()

    def is_auto_ai(self) -> bool:
        """Whether the background workers may spend money; off pauses reviews and reports, importing is unaffected."""
        return self._auto_ai.is_set()

    def set_auto_ai(self, enabled: bool) -> None:
        """Turn auto AI on or off; the caller wakes the workers, this flag only permits them."""
        logger.info(f"auto ai {'on' if enabled else 'off'}")
        if enabled:
            self._auto_ai.set()
        else:
            self._auto_ai.clear()

    def send_review_prompt(self, texts: dict[int, str]) -> list[NewCorrection]:
        """Review the texts, keyed by message id, and return every correction found tied to its message.

        Raises AiError on a failed call or an unreadable answer; nothing partial comes back.
        """
        try:
            messages_json = json.dumps([{"id": mid, "text": text} for mid, text in texts.items()], ensure_ascii=False)
            reply = self._send(PromptCategory.REVIEW, self.get_review_prompt() + messages_json).reply
            # Strip the code fences the model sometimes adds, then flatten the per-message JSON array behind them.
            reply = reply.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            corrections = [NewCorrection(message_id=item["id"], **c) for item in json.loads(reply) for c in item["corrections"]]
        except (subprocess.CalledProcessError, json.JSONDecodeError, ValidationError, KeyError, TypeError) as e:
            raise AiError(f"{type(e).__name__}: {e}") from e
        if any(c.message_id not in texts for c in corrections):
            raise AiError("the answer names a message id outside the batch")
        return corrections

    def send_explain_prompt(self, correction: Correction, message_text: str, question: str) -> str:
        """Ask the backend to clarify one correction, the writer's question appended if any; the reply is markdown, as is."""
        data = {
            "category": correction.category,
            "original": correction.original,
            "corrected": correction.corrected,
            "explanation": correction.explanation,
            "message": message_text,
        }
        prompt = self.get_explain_prompt() + json.dumps(data, ensure_ascii=False, indent=2)
        if question.strip():
            prompt += f"\n\nThe writer's own question:\n{question.strip()}"
        return self._send(PromptCategory.EXPLAIN, prompt).reply

    def send_report_prompt(
        self,
        corrections: list[Correction],
        previous_corrections: list[Correction],
        messages: list[str],
        previous_reports: list[str],
    ) -> str:
        """Ask the backend for a lesson over the report window's inputs; the reply is the report markdown, as is."""
        data = {
            # The window's corrections are chronological, so their first and last typed_at are the period's bounds.
            "period": f"{corrections[0].typed_at:%Y-%m-%d} to {corrections[-1].typed_at:%Y-%m-%d}" if corrections else "",
            "corrections": [f"[{c.category}] {c.original} -> {c.corrected} ({c.explanation})" for c in corrections],
            "previous_corrections": [
                f"[{c.category}] {c.original} -> {c.corrected} ({c.explanation})" for c in previous_corrections
            ],
            "messages": messages,
            "previous_reports": previous_reports,
        }
        return self._send(PromptCategory.REPORT, self.get_report_prompt() + json.dumps(data, ensure_ascii=False)).reply

    def send_chat_prompt(
        self, settings: Settings, report_text: str, turns: list[ReportChatTurn], question: str, session_id: str | None
    ) -> tuple[str, str]:
        """Ask one more question in a report's chat; returns the answer markdown and the session it lives in.

        With *session_id*, only the question is sent, as a turn in that session; a session the CLI no longer has
        fails with CalledProcessError. Without, a new session opens on the lesson and the stored *turns*, so the
        conversation continues where the record says it was. *settings* is the caller's snapshot for the whole turn.
        """
        if session_id is not None:
            response = self._send(PromptCategory.CHAT, question, settings, session_id=session_id)
        else:
            data: dict[str, object] = {"lesson": report_text}
            if turns:
                data["earlier_turns"] = [{"question": t.question, "answer": t.answer} for t in turns]
            data["question"] = question
            prompt = self.get_chat_prompt() + json.dumps(data, ensure_ascii=False, indent=2)
            response = self._send(PromptCategory.CHAT, prompt, settings, persist=True)
        if not response.session_id:
            raise AiError("the backend reported no session id")
        return response.reply, response.session_id

    def get_review_prompt(self) -> str:
        """Return the review prompt as it is sent: the native language and one line per catalog category filled in."""
        lines = [
            f"- {category} ({info.kind}): {info.description}" + (f" — e.g. {info.example}" if info.example else "")
            for category, info in CATEGORIES.items()
        ]
        native_language = self.core.services.setting.get_settings().native_language
        return REVIEW_PROMPT_TEMPLATE.replace("{native_language}", native_language).replace("{categories}", "\n".join(lines))

    def get_explain_prompt(self) -> str:
        """Return the explain prompt as it is sent; the correction JSON is appended per ask."""
        return EXPLAIN_PROMPT_TEMPLATE.replace("{native_language}", self.core.services.setting.get_settings().native_language)

    def get_report_prompt(self) -> str:
        """Return the report prompt as it is sent; the window JSON is appended per ask."""
        settings = self.core.services.setting.get_settings()
        return re.sub(r"\{(\w+)\}", lambda m: str(getattr(settings, m[1])), REPORT_PROMPT_TEMPLATE)

    def get_chat_prompt(self) -> str:
        """Return the chat prompt as it is sent when a session opens; the lesson, earlier turns and question JSON follow."""
        return CHAT_PROMPT_TEMPLATE.replace("{native_language}", self.core.services.setting.get_settings().native_language)

    def _send(
        self,
        category: PromptCategory,
        prompt: str,
        settings: Settings | None = None,
        *,
        session_id: str | None = None,
        persist: bool = False,
    ) -> AiResponse:
        """Answer *prompt* with the backend, model and effort the settings name for *category*, logging the call either way.

        *settings* is read fresh unless the caller passes its own snapshot. *session_id* and *persist* go to the
        backend as they are. A failed call is logged with its error and re-raised.
        """
        if settings is None:
            settings = self.core.services.setting.get_settings()
        match category:
            case PromptCategory.REVIEW:
                model, effort = settings.review_model, settings.review_effort
            case PromptCategory.EXPLAIN:
                model, effort = settings.explain_model, settings.explain_effort
            case PromptCategory.REPORT:
                model, effort = settings.report_model, settings.report_effort
            case PromptCategory.CHAT:
                model, effort = settings.chat_model, settings.chat_effort
            case _:
                assert_never(category)
        backend = build_backend(settings.ai_backend)
        started_at = datetime.now(UTC)
        started = time.monotonic()
        response: AiResponse | None = None
        error: str | None = None
        try:
            response = backend.send_prompt(prompt, model, effort, session_id=session_id, persist=persist)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            raise
        else:
            return response
        finally:
            metrics = response if response is not None else AiResponse(reply="")
            with self.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO ai_calls (created_at, category, backend, model, effort, prompt, reply,
                        input_tokens, output_tokens, cost_usd, duration_ms, error, session_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        started_at.isoformat(),
                        category,
                        settings.ai_backend,
                        metrics.model or model,  # what actually answered when the CLI says, the requested one otherwise
                        effort,
                        prompt,
                        response.reply if response is not None else None,
                        metrics.input_tokens,
                        metrics.output_tokens,
                        metrics.cost_usd,
                        int((time.monotonic() - started) * 1000),
                        error,
                        metrics.session_id or session_id,  # a failed resume still names the session it tried
                    ),
                )

    def get_calls(self, category: PromptCategory | None, limit: int, offset: int) -> list[AiCall]:
        """Return one page of stored calls, newest first; a None category means no filter."""
        rows = self.db.conn.execute(
            """
            SELECT * FROM ai_calls WHERE (:category IS NULL OR category = :category)
            ORDER BY id DESC LIMIT :limit OFFSET :offset
            """,
            {"category": category, "limit": limit, "offset": offset},
        )
        return [AiCall.from_row(row) for row in rows]

    def get_call(self, call_id: int) -> AiCall:
        """Return one stored call, full prompt and reply included."""
        row = self.db.conn.execute("SELECT * FROM ai_calls WHERE id = ?", (call_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"No AI call {call_id}")
        return AiCall.from_row(row)

    def count_calls(self, category: PromptCategory | None = None) -> int:
        """Count the stored calls, failed ones included; a None category means no filter."""
        row = self.db.conn.execute(
            "SELECT COUNT(*) FROM ai_calls WHERE (:category IS NULL OR category = :category)", {"category": category}
        ).fetchone()
        return int(row[0])

    def count_calls_by_category(self) -> dict[str, int]:
        """Count the call log per category, largest first."""
        rows = self.db.conn.execute("SELECT category, COUNT(*) AS n FROM ai_calls GROUP BY category ORDER BY n DESC")
        return {row["category"]: row["n"] for row in rows}
