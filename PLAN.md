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

- Bare `swotvac-saviour` command = first-run boot: study-dir prompt (default
  `~/swotvac-study`), then serve. API key entry happens on the web `/setup`
  page, never a terminal prompt. Default port 8321; link prints
  `http://127.0.0.1:8321`.
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

Branch `llm-first`. Three commits: `cc36bfa` (queue model + engine + sessions
removed), `e2cce47` (Phase 2), and `7a1bc27` (prototype: `sync.py` tag
reconciliation + session wrap-up). `main` points at the same tip as
`llm-first` (ahead 3 of `origin/master`). Working tree is clean except the
user's deleted PLAN PDFs (unstaged; leave alone).

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
about 50 s), play/answer/reshuffle/requeue flows, fresh sync wipe + re-parse,
sha256 gate (re-sync skips), source-page popover, all pages HTTP 200.

## Roadmap — next steps in order

### 0. Commit checkpoint 2 (done)
Done as `e2cce47`; the pending `sync.py` tag-reconciliation change also
landed as `7a1bc27`. Next work starts at item 2 (Phase 4 remainder).

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
Done 2026-10-19: CLI `sync` subcommand added (`swotvac-saviour sync <dir>`
with `--model`, `--cap`, `--fresh` with wipe confirmation, default DB
`<dir>/.database/swotvac.db`); live-verified 7/7 LLM transcription.
`importer.import_pdf` and the `import` CLI command deleted (LLM-less import
contradicted the product decision); `sha256_file` moved into `sync.py`;
`importer.py` removed.
Remaining: full tag reconciliation — renaming a subject folder still leaves
the orphaned old tag attached (sha gate re-attaches the new folder tag but
the old one lingers). Reconcile by matching the PDF set per folder, or offer
a `tag rename` action in the UI.

### 3. Phase 5 — onboarding: bare-command boot (confirmed design)
- `swotvac-saviour` with no args: make subcommand optional in
  `build_arg_parser`; no args dispatches to a first-run setup-and-serve flow.
- Terminal flow: status lines (DB check, key check, server boot), one prompt
  for study dir (Enter = `~/swotvac-study`), create
  `<dir>/.database/swotvac.db`, boot uvicorn on 8321, then print clickable
  link. Key missing → print `/setup` link instead of root.
- Web `/setup` page (GET+POST in `web.py` + `setup.html`): form writes
  `OPENROUTER_API_KEY` (and optional default model) to `<study-dir>/.env`,
  success links back to the dashboard. No key ever typed in a terminal.
- Sync stays web-form primary: no terminal-side sync prompt; student clicks
  the link and uses the existing sync form.
- Existing subcommands unchanged; `serve` also gains the link print.
- Tests: setup flow (dir creation, `.env` write, default dispatch, key-missing
  link choice).

### 4. Phase 6 — docs
- README rewrite: folder layout, `.env` key setup, first-run flow, sync/play/
  browse usage, cost cap, privacy note (PDFs leave the machine to the model
  provider). Use the `writing-guidelines` skill for tone.
- First-run `--init` flag (writes `.env` skeleton, creates `.database/`) is
  superseded by the Phase 5 bare-command boot flow; drop it.

### 5. Phase 7 — final review + ship
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

---

## Session wrap-up (2026-10-19)

Work completed this session:
1. Branch `llm-first` forked; all v1 sessions/streak code removed.
2. Checkpoint 1 (`cc36bfa`): queue model, LLM transcription engine,
   `.env` key precedence, sync.py folder pipeline, 43 passing tests.
3. Phase 1 live-smoke: real OpenRouter sync over `/tmp/study` booklet,
   7/7 LLM transcribed, queue/reshuffle/requeue/fresh-sync verified in
   browser.
4. Sharability: `requires-python` relaxed to `>=3.11`; `python-multipart`
   kept (FastAPI Form parsing needs it, not just uploads).
5. Phase 2 commit (`e2cce47`): LaTeX `$`/`$$` delimiter pipeline (prompt +
   guard rail + delimiter-aware segmenter), Quiet Workbook UI theme, wider
   question sheet (52rem), browse rows show real question text (3-line
   clamp), orphaned templates deleted, requires-python change.
6. Phase 4 tag reconciliation committed as `7a1bc27`: documents skipped by
   sha now re-attach the current folder's tag if renamed,
   `SyncResult.retagged` added, summary reports it. 43 tests passing.

## Session wrap-up (2026-10-19, continued)

Work completed:
1. `80d425e`: Phase 5 onboarding shipped — bare `swotvac-saviour` boot flow
   (study-dir prompt, `<dir>/.database/` creation, uvicorn on 8321,
   clickable link print) and web `/setup` page (key entry writes
   `<study-dir>/.env`; default model saved to settings). 53 tests.
2. Phase 4 remainder: CLI `sync` subcommand (positional directory, `--db`
   default `<dir>/.database/swotvac.db`, `--model`, `--cap` clamped to 0+,
   `--fresh` requires typing `wipe`); live-verified real OpenRouter sync
   7/7 questions, tag `MATH`, queue filled. Found + fixed missing
   `db.init_db` in the CLI path.
3. Dead code: `importer.py` deleted along with the `import` CLI command
   (LLM-less import contradicted locked decisions); `sha256_file` moved to
   `sync.py`; importer-pinned `test_db` tests replaced by `test_sync.py`
   (LLM rows, sha gate, retag-on-rename, guard-rail fallback, cost cap).
   62 tests passing.

Remaining (for next session):
1. Tag reconciliation: renamed folder still leaves the orphaned old tag.
2. Phase 3 solutions engine, Phase 6 docs, Phase 7 review + ship — build
   solutions only when the owner says so.

