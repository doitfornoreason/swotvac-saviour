# swotvac-saviour — Plan v3 (llm-first branch)

Current working plan. Supersedes PLAN.md v2 (old folder-workflow draft).
Status when written: build started on branch `llm-first`, halted mid-flight
for review. Nothing committed yet.

## Motive

The deterministic parser alone is not good enough for real question PDFs:
maths mangles, sub-parts break, hints and structure drift. The fork to
`llm-first` makes LLM transcription the primary engine and rebuilds the
practice flow around a global queue instead of per-tag sessions.

## Target workflow (student view)

1. Study directory holds one folder per subject; each folder holds that
   subject's PDFs (recursively). Folder name = subject tag.
2. Student opens the locally served site once, gives the study directory and
   an OpenRouter model (default `z-ai/glm-5.3-flash`, id verified at build).
3. Sync: tool discovers subject folders, gates PDFs by sha256, transcribes
   each question with the LLM (deterministic parse as fallback), stores rows
   with tag, source filename, question number, and placeholder solution.
4. Play page: stable randomised queue, LaTeX question, source + label top
   right, solution dropdown ("no solutions - feature not added yet"),
   checkboxes attempted / marked / flag for review, reshuffle button.
5. Browse page: filter by tag, source, flagged, attempted-but-not-marked.

## Locked decisions (from review)

- Sessions (seeded decks, streaks, stats page) are deleted, not kept.
- Web upload form deleted; folders are the only ingestion path.
- Database lives in `<study-dir>/.database/swotvac.db`.
- Solutions: fixed placeholder string everywhere; extraction later.
- Hints: verbatim `[Hint: ...]` from the PDF only, never LLM-generated;
  shown inline with the question when present.
- Queue is global; reshuffle reorders current+future only, past fixed;
  new syncs append to tail; exhausted queue shows a done screen and
  reshuffle re-queues flagged/marked questions.
- State checkboxes all reversible; marking a question auto-marks attempted.
- Fresh sync wipes documents, questions and state after JS reconfirmation.
- Cost cap per document is user-set on the sync form.
- One guard-rail retry per question, then deterministic fallback + warning.
- `OPENROUTER_API_KEY` comes from `.env` (study dir, then repo root); the
  `.env` value wins over the ambient environment.
- Browse is read-only; clicking a question views it with checkboxes without
  advancing the queue.

## Build state on `llm-first` (halted)

Done so far, uncommitted:
- `httpx` added as a runtime dependency.
- `db.py` rewritten: questions gains attempted/marked/flagged/engine/
  confidence columns; new `queue` and `settings` tables; helpers for state,
  queue append/advance/reshuffle/requeue, browse filters, settings get/set.
- `transcribe.py` created: OpenRouter chat client, `.env` loader, crop
  rendering via PyMuPDF label search, guard rails, retry, JSON extraction.
- `sync.py` created: folder discovery, style detection, per-document sync
  with sha gate, LLM pass with deterministic fallback, cost cap, fresh wipe.
- Deleted: `session.py`, `tests/test_play.py`, play/summary/stats/upload
  templates.

Remaining when confirmed:
1. `web.py` rewrite: drop sessions/upload/stats routes; add sync form
   (directory + model + cap), fresh sync button with reconfirmation, play
   route on the global queue, browse page with filters, state checkbox
   endpoints, question view endpoint.
2. Templates: new base nav (Dashboard / Play / Browse / Tags), index with
   sync form and fresh sync, play page, browse page, question view.
3. `parser.py` and `ingestion.py` unchanged (reuse as fallback engine).
4. `__init__.py`: CLI keeps parse/import/tag/documents/serve; serve gains
   `--dir` to point the study directory (DB path then `.database/swotvac.db`).
5. Tests: rewrite `test_web.py`-style coverage for new routes; unit tests for
   guard rails, queue invariants (reshuffle keeps past fixed, append to tail,
   requeue flagged), marked-implies-attempted, sha gate, fresh-sync wipe,
   cost cap, `.env` key precedence. Existing `test_db.py` updated to the new
   schema; `test_parser.py` untouched. No live API calls in tests.
6. Verification: real sync over a sample folder layout with a real key once
   provided (currently no `.env`), browser click-through of play and browse.

## Open decisions

- Exact OpenRouter model id + price check for the default (`z-ai/glm-5.3-flash`
  vs `google/gemini-2.5-flash`) — verify at build time.
- Done-screen contents beyond counts (e.g. per-tag breakdown).
- Whether CLI `sync` subcommand is added alongside the web-driven sync or the
  web form is the only entry point.

## Risks

- Cost runaway on big booklets — mitigated by user-set per-document cap and
  deterministic fallback.
- Transcription drift — label match, brace/HTML checks, confidence column,
  warnings, source badge for verification.
- Mid-build halt: working tree has half-migrated schema; resuming means
  finishing web.py/templates/tests before anything is usable again.
- Privacy: PDFs leave the machine to the model provider; the sync form should
  state this once.

## If confirmed

Continue in the order above until web.py + templates + tests are done, run the
full suite, then commit `llm-first` work as a single reviewable commit. Say
"BUILD" to resume; say "switch path" with the change and the plan gets
rewritten before any further code.
