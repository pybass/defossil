# defossil

**Status: prototype.** No tests yet; anything can change without backward compatibility.

Improve your English by reviewing your own chats with AI coding agents. The name comes from "fossilized errors" — recurring mistakes that stick in a learner's language. You already write a lot of English when talking to agents like Claude Code and Codex. defossil collects those messages, reviews them with an LLM, and gives you two things:

- **Corrections** — one per mistake: your fragment, the fix, and a short note on how to say it better.
- **Reports** — the most valuable part. A short lesson over many corrections: the mistakes you repeat, the native-language patterns in your phrasing, shorter ways to say what you keep saying long — real examples from your own text. Fast to read and a realistic picture of your English.

## How it works

Three background workers, one per step, each on its own 5-minute clock: the importer collects and classifies (always on), the review worker reviews, the report worker makes reports. The last two spend money, so they are gated by one in-memory auto-AI switch that starts off on every launch. Each table has a single writer, so corrections and reports are append-only and no step races another; a report window is a correction id range, which stays valid no matter when the report worker runs.

1. **Collect** — archive every message you typed, verbatim, into SQLite, deduplicated by the source's own key. Only real typed text: tool output, command expansions, and programmatic runs are skipped. Sources: Claude Code and Codex CLI; one module per source.
2. **Classify** — stamp each new message `pending` / `non-english` / `too-short` / `no-prose` / `too-long`, once. Only `pending` goes to review; the text itself is never rewritten.
3. **Review** — send `pending` messages to the LLM in batches, store what it corrects — real mistakes and style (wordiness, calques, register) — as corrections, and stamp the messages `reviewed`. A message is reviewed once, ever. On the dashboard a correction can be acknowledged, and the explain button asks the LLM for a deeper explanation.
4. **Report** — a markdown lesson over each `corrections_per_report` corrections: repeated mistakes, native-language patterns, shorter phrasings, one focus habit until the next report. Reports are stored and never regenerated.

## Architecture

`web` → `Core` → feature service → `Db`. `Core` is a container and the lifecycle: it opens the database, builds one service per feature, starts them in order and stops them in reverse. A feature is one job, named after the record it owns: `message` (the archive and its sources), `correction`, `report`, `setting`, and `ai` — every prompt the app sends, plus the `ai_calls` log of what each call cost. Every table has exactly one owner, and only the owner writes SQL against it. Features reach each other through `self.core.services.<other>`.

The schema evolves through append-only migrations (`core/migrations.py`, tracked by `PRAGMA user_version`), so it can change without dropping data. Nothing is redone: a message is classified and reviewed once, corrections and reports only accumulate. The archive is the one thing the sources cannot give back (Claude Code deletes transcripts after ~30 days), and nothing drops it.

## Usage

Python, FastAPI, SQLite. LLM calls go through the `claude` CLI by default (`claude -p` — works with a Claude subscription, no API key); a setting switches to `codex exec`. Local only: data never leaves the machine except text sent for review.

Run `defossil`, open http://127.0.0.1:3677.

## Settings

The data root cannot live in the database it locates, so it is the one setting outside it: `~/.local/share/defossil` by default, overridden only by `--data-dir`. Everything else — native language, AI backend, model and effort per prompt category, source roots, batch sizes, page size — lives in the `settings` table, is edited on the dashboard's settings page, and is read at use time, so a change applies without a restart.

## Non-goals

Decided against — do not re-propose or implement:

- **Exercises** — drills, quizzes, flashcards, spaced repetition built from the stored mistakes. The app shows mistakes and writes reports, nothing more.
- **Dismissing false positives** — a "not a mistake" flag. Premature: the archive shows no false positives yet.
- **Fossils page** — a page grouping corrections by category and fragment. Fragments group only when they repeat verbatim, so it adds little over the corrections page and the report.

## License

[MIT](LICENSE)
