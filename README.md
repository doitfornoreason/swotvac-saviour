# swotvac-saviour

Turn exam question PDFs into a practice deck you can play through in the browser. Each question is transcribed by a vision LLM (OpenRouter), stored in SQLite, and served as a stable play queue with resuffle, requeue, and cost caps. Single-user, local-first: your PDFs and database never leave your machine except to call the model provider.

## How it works

1. **Sync** scans your study directory, crops each question from every PDF, and asks an LLM to transcribe it.
2. **Play** walks you through the resulting queue in a browser; answer, flag, or mark questions as you go.
3. **Browse** shows every question with its source PDF page.

The deterministic parser still runs first: it supplies structure (question boundaries, labels, verbatim hints), and the LLM only transcribes. If the LLM fails a guard rail, the parser output becomes the question body and a warning is recorded.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- An [OpenRouter](https://openrouter.ai/) API key. Vision-capable models only; the default is `z-ai/glm-5.3-flash` (cheap and capable).

## Quickstart

Create a study directory: one folder per subject, each holding that subject's question PDFs.

```
~/swotvac-study/
└── Functional Analysis/
    └── exercises.pdf
```

Run the app:

```sh
uv run swotvac-saviour
```

On first run it prompts for a study directory (press Enter for `~/swotvac-study`), creates the database at `<study-dir>/.database/swotvac.db`, and starts the web UI on port 8321. With no API key found, it prints a link to the setup page instead.

## First run

1. Open the printed URL. If the key is missing, you land on `/setup`.
2. Paste your OpenRouter key into the form. It is written to `<study-dir>/.env`, never to a terminal, and never committed (`.env` is gitignored).
3. You're redirected to the dashboard.

Key lookup order: `<study-dir>/.env` first, then the repo root `.env`, then the `OPENROUTER_API_KEY` environment variable. The first match wins, so a study-directory key always beats an ambient one.

## Syncing

On the dashboard, point the sync form at your study directory and click **Sync**. Every PDF in every subject folder is transcribed; a content hash (`sha256`) gate skips files already synced. New PDFs append to the tail of the play queue.

Form options:

- **Model**: any vision-capable OpenRouter model. Your choice is remembered for next sync.
- **Cost cap**: maximum LLM calls per document, `0` for no cap. When the cap is hit, remaining questions fall back to the deterministic parse and a warning is recorded.
- **Fresh sync**: wipes all documents, questions, and play state, then re-parses everything. Requires typing "wipe" to confirm.

Prefer the terminal?

```sh
uv run swotvac-saviour sync ~/swotvac-study --model z-ai/glm-5.3-flash --cap 50 --fresh
```

The CLI takes the same options as the web form. Omitting `--db` targets `<directory>/.database/swotvac.db`.

## Playing

Each question shows its body (LaTeX rendered via KaTeX), source PDF page, and verbatim hints from the original document; the LLM never invents hints. Controls: **Attempted**, **Marked**, **Flagged**, all reversible (marked implies attempted). The queue order is stable; **Reshuffle** reorders the current and future questions only. When the queue is exhausted, flagged and marked questions are re-queued on demand.

## Other commands

- `parse <pdf>`: split a PDF into `questions.json` with the deterministic parser alone, no LLM call.
- `tag add|list|assign`: manage subject tags against a database.
- `documents --db <path>`: list synced documents with their hashes and tags.
- `serve --db <path> --host 127.0.0.1 --port 8321`: run the web UI against an existing database.

## Privacy

Question crops are sent to OpenRouter for transcription and to no one else. Documents, questions, and your play state stay in `<study-dir>/.database/swotvac.db`. To keep data fully offline, use `parse` instead of `sync` and import via the tag commands.

## Development

```sh
uv run pytest
```

The test suite covers the parser, guard rails, queue invariants, and CLI/web wiring; it makes no live API calls. For a live smoke test, create a study directory under `/tmp`, copy a `.env` with your key into it, and run a sync. Sample PDFs live in `samples/`.
