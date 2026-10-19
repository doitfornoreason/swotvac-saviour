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
Done 2026-10-19: README rewritten (`46a3065`) per `writing-guidelines`:
folder layout, `.env` key setup via `/setup`, first-run boot flow,
sync/play usage, CLI `sync` parity, cost cap, privacy note.
First-run `--init` flag is superseded by the Phase 5 bare-command boot
flow; dropped.

### 5. Phase 7 — final review + ship
Review done 2026-10-19 (`cavecrew-reviewer` over `git diff 7672409..HEAD`).
Five findings fixed: `.env` written with owner-only `0o600`; CSRF guard
middleware (POST/PUT/DELETE/PATCH with foreign `Origin`/`Referer` → 403);
`sync_directory` per-PDF `except Exception` narrowed to
`OSError/ValueError/RuntimeError/pymupdf.FileDataError` so DB bugs surface;
`parser.label_styles()` public accessor replaces `_LABEL_PATTERNS` access;
CLI `--fresh` prompt handles `EOFError` gracefully.
Two findings rejected as false positives: queue-invariant tests exist
(`test_play_reshuffle_keeps_past_fixed`, `test_play_requeue_flagged`), so
the README claim was already accurate. 67 tests passing.
Remaining: push/publish.

## Open decision (needs owner input)

Resolved 2026-10-19: `serve` keeps the `127.0.0.1` bind default. LAN users
pass `--host 0.0.0.0` explicitly. The CSRF guard blocks cross-origin state
changes, but there is still no auth, so keep the default local-only.

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

## User feedback (2026-10-19, test run) — TODO

- Dashboard sync form: no progress/status updates while a sync is running;
  the page appears frozen and loading takes a while.
- TODO: add live progress feedback (e.g. per-question or per-document status,
  spinner, streamed updates) to the dashboard sync form. Do not implement
  until the user finishes the current test run and requests changes.

### Reviewer-2 findings (2026-10-19, live verification)

- 🔴 Parser mis-split: doc 2 ("without solutions") q21–30 all have
  `page_number=7` and bodies are prior-knowledge summary text
  ("Summary of some assumed prior knowledge..."), not practice questions.
  They sit in the play queue. Fix: guard rail in parser for summary/
  reference pages, and/or manual delete of ids 21–30 in this DB.
- 🟡 ~26/85 questions carry "suspected truncation" warnings (crop cut
  mid-sentence). Guard rail catches it but bodies incomplete. Consider
  higher-DPI re-crop pass.
- 🔵 `db.connect()` `busy_timeout=5000` too short: while a CLI sync holds
  the WAL write lock, POST `/play/answer` returns 500 ("database is
  locked"). Verified root cause; all POSTs 200 post-sync. Fix: longer
  busy_timeout or retry loop for web writes during sync.
- 🔵 Path nit: `original_path` stored as the sync process's mount view
  (`/mnt/Ubuntu/home/mir/...`) vs server's `/home/nine/...` — same inode,
  works; consider path normalisation.
- Confirmation: web-UI background sync with progress feedback is the right
  architecture; CLI serial sync of 13 PDFs took ~2h40m with no output.
- Remaining sync: 6 PDFs not yet synced (sha-gate skips the done 7).


## Session wrap-up (2026-10-19, sync performance + reviewer fixes)

Work completed:
1. Sync parallelized: `sync_directory` renders all crops on the main thread
   (pymupdf is not thread-safe), then runs per-question LLM calls in a
   `ThreadPoolExecutor` (10 workers). DB inserts stay on the main thread in
   parse order. Live: 191 questions across 6 PDFs in ~17 min (was ~2h40m
   serial for 13); 191 llm, 0 fallback.
2. Continuation-aware crops: parser records `end_page` per question;
   `crop_png_bytes` accepts `continuation` segments (page, end_label) and
   stacks pixmaps into one PNG. Fixes the cross-page truncation class for
   questions with a following label. Last question never continues (no
   bounding label; page-tail junk stays out of the crop).
3. Summary-page guard rail: `SUMMARY_BOUNDARY_RE` (case-sensitive,
   punctuation-excluded) treats capitalized Summary/Prerequisites/Prior
   knowledge/Reference/Formula-sheet headings as back matter. Fixes the
   reviewer-2 red finding: "Diagnostic-Self-Test without solutions" now
   parses 10 questions (was 20, 10 fake from the prior-knowledge page).
   Fake rows 21-30 deleted from the live DB (queue cascaded, 266 = 266).
4. Progress counter made monotonic: pool completion order is
   nondeterministic; dashboard now shows a completed-count that never
   regresses. Progress polling (`/sync/status` + dashboard.js) live-verified
   end to end (question i/n updates observed).
5. 73 tests passing (6 new: pool + order preservation, continuation crop
   spy + stacking, summary boundary, lowercase-prose non-match, end_page).

Known limitation (accepted): `httpx` timeout=120 bounds read gaps, not total
generation time. One glm-5.3-flash call streamed for ~20 min (guard-rail
retry doubled a slow reasoning generation); the call did complete. A
per-question total deadline would leak worker threads — revisit only if
tails recur. Truncation warnings on newly synced docs are heuristic false
positives (bodies ending in equations), ~56/266 overall.

Remaining: tag reconciliation (orphaned tag on folder rename); Phase 3
solutions engine when owner asks; push/publish.

Reviewer round 2 (cavecrew-reviewer over 03dec79): 4 findings fixed in
place — last spanning question now gets continuation segments (bounded by
parser end_page), over-inclusion of label-free intermediate pages removed
(segments gated on end_page), unresolvable continuation bounds skip the
segment instead of stacking a full page, end-label match takes the lowest
occurrence (cross-references sit above the real heading), and summary
headings now skip only their page instead of ending the question pool.
Accepted: no total-deadline on model calls (leaked-thread tradeoff);
cross-reference below the real label can still shrink a segment (needs
label geometry the text parser does not have).

## Session wrap-up (2026-10-19, rendering + play fix)

Work completed:
1. Play blank-screen fix (3fe35f7): play/reshuffle/requeue returned 200 with
   an htmx-only HX-Redirect header while play.html uses plain form posts;
   browsers rendered an empty body. Now real 303 redirects; test tightened
   to 303.
2. Rendering engine (df54d74): normalise.render_body() emits finished safe
   HTML for a body_latex blob - itemize/enumerate become ul/ol (with
   item[(a)] labels as spans, math inside items still KaTeX), textbf/textit/
   emph/texttt/underline become strong/em/code/u, $$...$$ blocks render in
   full display mode via a new .katex-display-block span, everything else
   stays html-escaped. Templates (play/question/browse) render q.body_html
   directly; latex_segments kept for legacy. Prompt now teaches the LLM to
   emit itemize/enumerate + text styles.
3. Static cache-busting: /static URLs carry ?v=<file mtime>; browsers were
   serving stale play.js after upgrades (verified live: old copy persisted
   even across reload).
4. 86 tests passing (11 new renderer tests). Live-verified q3/q7/q54/q115
   against the real DB in a browser.
