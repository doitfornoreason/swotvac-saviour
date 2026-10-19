"""FastAPI web shell: sync, play, browse, tags."""

from __future__ import annotations

import sqlite3
import threading
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from . import db, normalise, parser, transcribe
from .ingestion import load_pdf

_PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))


def _static_v(name: str) -> str:
    """Cache-busting version: static file mtime (edit => new URL)."""
    try:
        return str(int((_PACKAGE_DIR / "static" / name).stat().st_mtime))
    except OSError:
        return "0"


templates.env.globals["static_v"] = _static_v

STYLES = ("exercise", "number")


def create_app(db_path: str | Path, study_dir: str | Path | None = None) -> FastAPI:
    """Build the app against ``db_path``. ``study_dir`` defaults the sync form."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app):
        conn = db.connect(db_path)
        db.init_db(conn)
        conn.close()
        yield

    app = FastAPI(title="swotvac-saviour", lifespan=lifespan)

    # CSRF guard: a malicious page in the user's browser can POST to
    # http://127.0.0.1:8321 without reading responses, so state-changing
    # endpoints must reject requests whose Origin/Referer host differs from
    # ours. Same-origin browsers send Origin; curl/scripts send neither.
    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            host = request.headers.get("host", "")
            bad = False
            origin = request.headers.get("origin")
            if origin:
                bad = urlparse(origin).netloc != host
            else:
                referer = request.headers.get("referer")
                if referer:
                    bad = urlparse(referer).netloc != host
            if bad:
                return PlainTextResponse(
                    "cross-origin request rejected", status_code=403
                )
        return await call_next(request)
    app.state.db_path = str(db_path)
    app.mount("/static", StaticFiles(directory=_PACKAGE_DIR / "static"), name="static")
    app.state.study_dir = str(Path(study_dir)) if study_dir else ""
    # Background sync job state; mutated by the sync thread, read by handlers.
    app.state.sync_job = {
        "running": False,
        "done": False,
        "stage": "",
        "name": "",
        "tag": "",
        "question_index": 0,
        "question_total": 0,
        "total_docs": 0,
        "summary": None,
        "error": None,
    }

    def _start_sync_job(study_dir: Path, model: str, cap: int, fresh: bool, key: str) -> bool:
        """Kick off a background sync thread. False if one is already running."""
        job = app.state.sync_job
        if job["running"]:
            return False
        job.update(
            running=True, done=False, stage="starting", name="", tag="",
            question_index=0, question_total=0, total_docs=0,
            summary=None, error=None,
        )

        def on_progress(event: dict) -> None:
            if event.get("stage") == "done":
                job["summary"] = event["result"].summary()
                return
            job["stage"] = event.get("stage", "")
            for field in ("name", "tag", "question_index", "question_total", "total_docs"):
                if field in event:
                    job[field] = event[field]

        def run() -> None:
            from . import sync as sync_mod

            try:
                conn = db.connect(app.state.db_path)
                try:
                    result = sync_mod.sync_directory(
                        conn, study_dir,
                        model=model or transcribe.DEFAULT_MODEL,
                        cap=max(0, cap), key=key, fresh=fresh,
                        progress=on_progress,
                    )
                finally:
                    conn.close()
                app.state.last_sync = result.summary()
                job["summary"] = result.summary()
            except Exception as err:  # background thread: report, never crash
                job["error"] = str(err)
                app.state.last_sync = f"Sync failed: {err}"
            finally:
                job["running"] = False
                job["done"] = True

        threading.Thread(target=run, name="sync-job", daemon=True).start()
        return True

    def get_db():
        conn = db.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def render(request: Request, name: str, context: dict | None = None, status: int = 200):
        ctx = {"request": request, "study_dir": app.state.study_dir}
        if context:
            ctx.update(context)
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    # --- dashboard -----------------------------------------------------------
    @app.get("/")
    def dashboard(request: Request, conn=Depends(get_db)):
        return render(
            request,
            "index.html",
            {
                "documents": db.list_documents(conn),
                "tags": db.list_tags(conn),
                "counts": db.queue_counts(conn),
                "model": db.get_setting(conn, "model") or transcribe.DEFAULT_MODEL,
                "dir": db.get_setting(conn, "study_dir") or app.state.study_dir,
                "sync_job": app.state.sync_job,
            },
        )

    @app.post("/sync")
    def sync(
        request: Request,
        conn=Depends(get_db),
        dir: str = Form(""),
        model: str = Form(""),
        cap: int = Form(0),
        fresh: str = Form(""),
    ):
        directory = dir or app.state.study_dir
        if not directory:
            raise HTTPException(status_code=400, detail="study directory required")
        study_dir = Path(directory)
        if fresh:
            import secrets

            fresh_token = secrets.token_hex(16)
            app.state.fresh_token = fresh_token
            return render(
                request,
                "confirm_fresh.html",
                {"directory": directory, "model": model, "cap": cap,
                 "fresh_token": fresh_token},
            )
        if not (key := transcribe.load_api_key(study_dir)):
            raise HTTPException(
                status_code=400,
                detail=f"no OPENROUTER_API_KEY found in {study_dir}/.env",
            )
        if model:
            db.set_setting(conn, "model", model)
            db.set_setting(conn, "study_dir", str(study_dir))
            conn.commit()
        _start_sync_job(study_dir, model, cap, fresh=False, key=key)
        return RedirectResponse("/", status_code=303)

    @app.post("/sync/confirm")
    def sync_confirm(
        request: Request,
        conn=Depends(get_db),
        dir: str = Form(""),
        model: str = Form(""),
        cap: int = Form(0),
        fresh: str = Form(""),
        token: str = Form(""),
    ):
        if fresh and token != getattr(app.state, "fresh_token", None):
            raise HTTPException(status_code=409, detail="fresh sync token mismatch; start over")
        directory = dir or app.state.study_dir
        study_dir = Path(directory)
        if not (key := transcribe.load_api_key(study_dir)):
            raise HTTPException(
                status_code=400,
                detail=f"no OPENROUTER_API_KEY found in {study_dir}/.env",
            )
        if model:
            db.set_setting(conn, "model", model)
            db.set_setting(conn, "study_dir", str(study_dir))
            conn.commit()
        _start_sync_job(study_dir, model, cap, fresh=bool(fresh), key=key)
        return RedirectResponse("/", status_code=303)

    # --- sync progress -------------------------------------------------------
    @app.get("/sync/status")
    def sync_status():
        # dict() copy is atomic in CPython; avoids serializing mid-mutation.
        return dict(app.state.sync_job)

    # --- setup ---------------------------------------------------------------
    @app.get("/setup")
    def setup_page(request: Request, conn=Depends(get_db)):
        study_dir = app.state.study_dir or db.get_setting(conn, "study_dir") or ""
        has_key = bool(transcribe.load_api_key(study_dir or None))
        return render(
            request,
            "setup.html",
            {
                "study_dir": study_dir,
                "has_key": has_key,
                "saved": bool(request.query_params.get("saved")),
                "model": db.get_setting(conn, "model") or transcribe.DEFAULT_MODEL,
            },
        )

    @app.post("/setup")
    def setup_save(
        request: Request,
        conn=Depends(get_db),
        key: str = Form(""),
        model: str = Form(""),
    ):
        key = key.strip()
        if not key:
            raise HTTPException(status_code=400, detail="API key required")
        study_dir = app.state.study_dir or db.get_setting(conn, "study_dir") or ""
        if not study_dir:
            raise HTTPException(
                status_code=400,
                detail="no study directory known; run swotvac-saviour with no arguments",
            )
        transcribe.save_api_key(study_dir, key)
        if model:
            db.set_setting(conn, "model", model)
            conn.commit()
        return RedirectResponse("/setup?saved=1", status_code=303)

    # --- play -----------------------------------------------------------------
    @app.get("/play")
    def play(request: Request, conn=Depends(get_db)):
        current = db.current_queue_question(conn)
        if current is not None:
            current["body_html"] = normalise.render_body(current["body_latex"])
            current["state"] = db.question_state(conn, current["question_id"])
            return render(request, "play.html", {"q": current})
        return render(request, "done.html", {"counts": db.queue_counts(conn)})

    @app.post("/play/answer")
    def play_answer(
        request: Request,
        question_id: int = Form(...),
        conn=Depends(get_db),
    ):
        # answering advances the queue and marks the question attempted
        db.set_question_state(conn, question_id, attempted=True)
        db.advance_queue(conn, question_id)
        return RedirectResponse("/play", status_code=303)

    @app.post("/play/reshuffle")
    def play_reshuffle(request: Request, conn=Depends(get_db)):
        db.reshuffle_queue(conn)
        return RedirectResponse("/play", status_code=303)

    @app.post("/play/requeue")
    def play_requeue(request: Request, conn=Depends(get_db)):
        db.requeue_flagged(conn)
        return RedirectResponse("/play", status_code=303)

    # --- question view (browse detail + state toggles) ------------------------
    def _question_view(request: Request, conn: sqlite3.Connection, question_id: int):
        q = db.get_question(conn, question_id)
        if q is None:
            raise HTTPException(status_code=404, detail="no such question")
        q["body_html"] = normalise.render_body(q["body_latex"])
        return render(request, "question.html", {"q": q})

    @app.get("/questions/{question_id}")
    def question_view(request: Request, question_id: int, conn=Depends(get_db)):
        return _question_view(request, conn, question_id)

    @app.post("/questions/{question_id}/state")
    def question_state(
        request: Request,
        question_id: int,
        field: str = Form(...),
        value: int = Form(...),
        next: str = Form("/play"),
        conn=Depends(get_db),
    ):
        if field not in ("attempted", "marked", "flagged") or value not in (0, 1):
            raise HTTPException(status_code=400, detail="bad state toggle")
        kwargs = {field: bool(value)}
        db.set_question_state(conn, question_id, **kwargs)
        return Response(status_code=303, headers={"Location": next})

    # --- browse ----------------------------------------------------------------
    @app.get("/browse")
    def browse(
        request: Request,
        conn=Depends(get_db),
        tag: int = 0,
        doc: int = 0,
        flagged: int = 0,
        unmarked: int = 0,
    ):
        questions = db.browse_questions(
            conn,
            tag_id=tag or None,
            source_document_id=doc or None,
            flagged=bool(flagged),
            attempted_unmarked=bool(unmarked),
        )
        for q in questions:
            q["body_html"] = normalise.render_body(q["body_latex"])
        return render(
            request,
            "browse.html",
            {
                "questions": questions,
                "tag_options": db.tag_options(conn),
                "documents": db.list_documents(conn),
                "filters": {"tag": tag, "doc": doc, "flagged": flagged, "unmarked": unmarked},
            },
        )

    # --- documents --------------------------------------------------------------
    @app.get("/documents/{doc_id}/review")
    def review(request: Request, doc_id: int, conn=Depends(get_db)):
        docs = {d["id"]: d for d in db.list_documents(conn)}
        if doc_id not in docs:
            raise HTTPException(status_code=404, detail="no such document")
        questions = db.questions_for_document(conn, doc_id)
        return render(
            request,
            "review.html",
            {
                "doc": docs[doc_id],
                "questions": questions,
                "warned": sum(1 for q in questions if q["warnings"]),
            },
        )

    @app.post("/documents/{doc_id}/reimport")
    def reimport(request: Request, doc_id: int, conn=Depends(get_db)):
        row = conn.execute(
            "SELECT original_path FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such document")
        original = row[0]
        if not Path(original).exists():
            raise HTTPException(status_code=404, detail="original PDF file missing")
        try:
            doc = load_pdf(original)
            questions = parser.split_questions(doc)
            for q in questions:
                q.body_latex = normalise.to_latex(q.body)
        except Exception as err:  # pymupdf raises FileDataError/RuntimeError
            raise HTTPException(status_code=400, detail=f"reimport failed: {err}")
        db.replace_document_questions(conn, doc_id, [q.to_dict() for q in questions])
        conn.commit()
        return Response(status_code=303, headers={"Location": f"/documents/{doc_id}/review"})

    @app.get("/documents/{doc_id}/page/{page}")
    def page_image(request: Request, doc_id: int, page: int, conn=Depends(get_db)):
        row = conn.execute(
            "SELECT original_path FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such document")
        original = row[0]
        if not Path(original).exists():
            raise HTTPException(status_code=404, detail="original PDF file missing")
        index = page - 1
        png = _render_png(original, index)
        return Response(
            content=png,
            media_type="image/png",
            headers={"Cache-Control": "max-age=31536000, immutable"},
        )

    @lru_cache(maxsize=256)
    def _render_png(original: str, index: int, dpi: int = 120) -> bytes:
        """PNG of one printed PDF page. Cached: the URL is immutable per doc+page."""
        import pymupdf  # lazy: only the page-image path needs MuPDF

        doc = pymupdf.open(original)
        try:
            if not (0 <= index < doc.page_count):
                raise HTTPException(status_code=404, detail="page out of range")
            return doc[index].get_pixmap(dpi=dpi).tobytes("png")
        finally:
            doc.close()

    # --- tags -------------------------------------------------------------------
    @app.get("/tags")
    def tags_page(request: Request, conn=Depends(get_db)):
        return render(request, "tags.html", {"tags": db.list_tags(conn)})

    @app.post("/tags")
    def tags_create(request: Request, name: str = Form(...), conn=Depends(get_db)):
        try:
            db.add_tag(conn, name)
            conn.commit()
        except db.DuplicateTagError:
            pass  # tag exists: just re-render the list
        return Response(status_code=303, headers={"Location": "/tags"})

    return app
