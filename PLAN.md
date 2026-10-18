# swotvac-saviour — Working Plan (branch `llm-first`)

Living document: current state, next steps, and everything needed to continue
this project after a break. Locked product decisions live here so they don't
have to be re-derived.

## What this is

Single-user, locally hosted study tool. A study directory holds one folder per
subject (folder name = subject tag, e.g. `Functional Analysis/`); each folder
holds that subject's question PDFs. A sync pass transcribes every question
with an OpenRouter LLM (deterministic parser supplies structure/labels only),
stores questions in SQLite inside `<study-dir>/.database/swotvac.db`, and a
FastAPI web UI serves a play queue and a browse view.

Run it: `uv run swotvac-saviour serve --db <study-dir>/.database/swotvac.db
--port 8321` (default bind 127.0.0.1; `--host 0.0.0.0` for LAN).

## Locked decisions (do not re-litigate)

- Sessions, stats page, streaks, upload form: deleted. Global queue only.
- DB lives in `<study-dir>/.database/` so sharing a study dir shares state.
- Solutions: fixed placeholder "no solutions - feature not added yet".
  Extraction is a future feature (Phase 3).
- Hints: verbatim `[Hint: ...]` from the PDF only, never LLM-invented.
- Queue: stable order; reshuffle reorders current+future only; new syncs
  append to tail; exhausted queue re-queues flagged/marked on demand.
- Checkboxes attempted/marked/flagged all reversible; marked ⇒ attempted.
- Fresh sync wipes documents, questions, and state after JS confirmation.
- Cost cap per document is user-set on the sync form (0 = no cap).
- One guard-rail retry per question, then deterministic fallback + warning.
- `OPENROUTER_API_KEY` loads from `.env` (study dir, then repo root); the
  `.env` value wins over the ambient environment. Key is gitignored.
- Default model `z-ai/glm-5.3-flash` (vision-capable, cheap); override per
  sync in the web form; model persists in the `settings` table.
- LLM math MUST be `$...$`-delimited (inline) or `$$...$$` (display); the
  guard rail rejects unbalanced `$`. Pre-`$`-era rows fall back to the legacy
  fragment heuristic in `normalise.latex_segments`.

## Current state (as of this writing)

Branch `llm-first`. Two commits: `cc36bfa` (queue model + engine + sessions
removed) and Phase 2 work **uncommitted in the working tree** — commit it as
checkpoint 2 before starting anything else.

Phase 2 delivered (verified, 43 tests passing):
- Sharability: `requires-python` relaxed to `>=3.11` (keep deps minimal; no
  new frameworks). `python-multipart` must stay — FastAPI needs it for any
  `Form()` parsing, not just uploads.
- LaTeX pipeline fixed at the root: prompt enforces `$`/`$$` delimiters,
  `latex_segments` splits on delimiters, unbalanced-`$` guard rail added,
  legacy heuristic kept for old rows.
- "Quiet Workbook" theme in `static/style.css` (paper bg, serif question
  sheet at 52rem max width, aria-pressed state toggles, reduced-motion and
  dark-mode tokens).
- Browse shows real question text (KaTeX-rendered, 3-line clamp) per row;
  orphaned `questions.html`/`tags_list.html` deleted.

Live-verified end to end: LLM sync 7/7 questions (avg confidence 0.986,
~50 s), play/answer/reshuffle/requeue flows, fresh sync wipe + re-parse,
sha256 gate (re-sync skips), source-page popover, all pages HTTP 200.

## Roadmap — next steps in order

### 0. Commit checkpoint 2 (immediate)
`git add -A && git commit` on `llm-first` with the Phase 2 summary above.
Nothing else starts before this.

### 1. Phase 3 — solutions engine (build or defer indefinitely)
- Parser already detects the solutions-section boundary
  (`SOLUTIONS_BOUNDARY_RE` in `parser.py`) and currently *discards* those
  pages. Change: keep solutions pages per question.
- One extra LLM call per question over the solution region; `solution`
  column already exists in the schema; dropdown in `play.html` renders it
  via KaTeX (delimiters same as body).
- Cost: same order as the question pass. Make it a sync-form toggle.
- Suggested constraint if built: solutions stay verbatim-transcribed, never
  generated; same guard rails + confidence column treatment.

### 2. Phase 4 — tag rename, CLI parity, dead code
- Known live repro: renaming a subject folder orphans the old tag and mints
  an empty new one (sha gate skips the PDF). Fix: reconcile by matching the
  PDF set per folder, or offer a `tag rename` action in the UI.
- CLI: add `sync` subcommand mirroring the web form (directory, model, cap,
  fresh). Web form remains primary.
- Dead code sweep: `importer.import_pdf` is now legacy — decide delete vs
  keep for the `import` CLI command; remove whichever is unused.

### 3. Phase 5 — docs
- README rewrite: folder layout, `.env` key setup, first-run flow, sync/play/
  browse usage, cost cap, privacy note (PDFs leave the machine to the model
  provider). Use the `writing-guidelines` skill for tone.
- Optional first-run `--init` flag that writes `.env` skeleton and creates
  `.database/` (approved sharability item).

### 4. Phase 6 — final review + ship
- Run the `caveman-review`/`cavecrew` reviewer pass over the full diff
  (key handling, LaTeX HTML-injection surface, queue invariants).
- `caveman-commit` final commit. Optionally publish (`uv tool install` from
  git works today; PyPI only if wanted).

## Open decision (needs owner input)

- `serve` binds 127.0.0.1 by default. Do we flip the default to 0.0.0.0 for
  LAN sharing, or keep local-only and document `--host`? (Single-user tool;
  0.0.0.0 exposes unauthenticated state toggles + sync to the LAN.)

## Environment / tooling notes (for future sessions)

- Tests: `uv run pytest` (43 passing; no live API calls in the suite).
- Live smoke: create a mock study dir under `/tmp`, copy `.env` into it, run
  `sync_directory` from a script or drive the web UI. The dev server used in
  verification ran on port 8321.
- Skills used / to use: `caveman` (tone, whole session), `frontend-design`
  (theme work), `web-design-guidelines` (a11y audit — fetch the Vercel
  command.md before reviewing), `writing-guidelines` (README),
  `caveman-review` + `caveman-commit` (ship). All live in `~/.agents/skills/`
  and `~/.omp/skills/`; React/Vercel skills are dead weight for this repo.
- Sample PDFs in `samples/`: `exercises.pdf` (exercise-style, 122 questions)
  and `MAST20030ProblemBooklet_Topic4.pdf` (number-style, 7 questions).
- Branch layout: `main` = deterministic-parser era (still shippable as v1);
  `llm-first` = current line. Remote is `origin/master` — push may need an
  explicit `git push origin llm-first`.
- `.env` is gitignored; never commit the key. `PLAN_3.pdf` at repo root is
  the user's own notes file — leave it alone.
