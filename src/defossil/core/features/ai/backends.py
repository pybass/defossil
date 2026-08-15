"""The backend contract, both CLI implementations, and the one place a name from the settings becomes a backend.

A failed call raises to the caller, which decides whether that is recorded or fatal. A missing CLI binary is not
that: it is the machine being wrong, and it stops the run.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from defossil.core.features.ai.models import AiResponse


class AiBackend(Protocol):
    """One prompt in, the answer with its metadata out; what is asked and how it is read is the caller's."""

    def send_prompt(self, prompt: str, model: str, effort: str) -> AiResponse:
        """Answer *prompt* on *model* at *effort*; a failed call raises to the caller."""
        ...


class ClaudeBackend:
    """Answers a prompt by running the `claude` CLI in print mode."""

    def send_prompt(self, prompt: str, model: str, effort: str) -> AiResponse:
        """Run `claude -p` over *prompt* with the given model and effort."""
        claude_bin = shutil.which("claude")
        if claude_bin is None:
            raise RuntimeError("claude CLI not found in PATH")
        # Hooks are for interactive sessions: without this they fire on every call (sounds, prompt-injecting hooks).
        argv = [claude_bin, "-p", "--output-format", "json", "--settings", '{"disableAllHooks": true}']
        argv += ["--model", model, "--effort", effort]
        result = subprocess.run(  # noqa: S603 -- fixed argv, resolved binary, no shell; nothing user-controlled is executed
            argv, input=prompt, capture_output=True, text=True, check=True
        )
        answer = json.loads(result.stdout)
        usage = answer.get("usage")
        # modelUsage also lists the CLI's internal haiku helper calls; the answer comes from the model that wrote the most.
        model_usage = answer.get("modelUsage") or {}
        return AiResponse(
            reply=str(answer["result"]).strip(),
            model=max(model_usage, key=lambda m: model_usage[m].get("outputTokens") or 0, default=None),
            # Cache writes and reads are counted in: with `claude -p` almost the whole prompt lands there.
            input_tokens=sum(
                usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
            )
            if usage
            else None,
            output_tokens=usage.get("output_tokens") if usage else None,
            cost_usd=answer.get("total_cost_usd"),
        )


class CodexBackend:
    """Answers a prompt by running the `codex` CLI non-interactively; the CLI reports no usable usage metadata."""

    def send_prompt(self, prompt: str, model: str, effort: str) -> AiResponse:
        """Run `codex exec` over *prompt*; tokens and cost stay None — only the machine-readable answer file comes back."""
        codex_bin = shutil.which("codex")
        if codex_bin is None:
            raise RuntimeError("codex CLI not found in PATH")
        # --ephemeral keeps the run out of ~/.codex/sessions, so the collector never re-ingests defossil's own prompts.
        with tempfile.TemporaryDirectory() as tmp:
            answer_file = Path(tmp) / "answer.txt"
            flags = ["--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", "-o", str(answer_file)]
            flags += ["--model", model, "-c", f"model_reasoning_effort={effort}"]
            argv = [codex_bin, "exec", *flags, "-"]
            subprocess.run(  # noqa: S603 -- fixed argv, resolved binary, no shell; the prompt only travels over stdin
                argv, input=prompt, capture_output=True, text=True, check=True
            )
            return AiResponse(reply=answer_file.read_text().strip())


def build_backend(name: str) -> AiBackend:
    """Return the backend *name* names; an unknown name stops the run."""
    match name:
        case "claude":
            return ClaudeBackend()
        case "codex":
            return CodexBackend()
        case _:
            raise ValueError(f"Unknown AI backend {name!r}: use claude or codex")
