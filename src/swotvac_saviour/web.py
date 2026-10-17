"""FastAPI web shell: dashboard, upload, review, tag browsing."""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.templating import Jinja2Templates

from . import db, importer, normalise

_PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))

STYLES = ("exercise", "number")


def create_app(db_path: str | Path) -> FastAPI:
    from contextlib import asynccontextmanager

    db_path = str(db_path)
    uploads_dir = Path(db_path).resolve().parent / "uploads"

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        conn = db.connect(db_path)
        try:
            db.init_db(conn)
        finally:
            conn.close()
        uploads_dir.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(title="swotvac-saviour", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.state.db_path = db_path
    app.state.uploads_dir = uploads_dir
    app.mount("/static", StaticFiles(directory=str(_PACKAGE_DIR / "static")), name="static")

    def get_db(request: Request):
        conn = db.connect(request.app.state.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def render(request: Request, name: str, context: dict | None = None, status: int = 200):
        ctx = {"request": request, "styles": STYLES}
        if context:
            ctx.update(context)
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    # --- dashboard ---------------------------------------------------------
    @app.get("/")
    def dashboard(request: Request, conn=Depends(get_db)):
        return render(
            request,
            "index.html",
            {"documents": db.list_documents(conn), "tags": db.list_tags(conn)},
        )

    # --- upload ---------------------------------------------------------------
    @app.get("/upload")
    def upload_form(request: Request, conn=Depends(get_db)):
        return render(request, "upload.html", {"tags": db.list_tags(conn)})

    @app.post("/upload")
    async def upload(
        request: Request,
        pdf: UploadFile,
        tags: list[str] = Form(default=[]),
        style: str = Form("exercise"),
    ):
        app_state = request.app.state
        if not pdf.filename:
            raise HTTPException(status_code=400, detail="missing filename")
        if style not in STYLES:
            raise HTTPException(status_code=400, detail=f"unknown style: {style}")

        uploads_dir = Path(app_state.uploads_dir)
        uploads_dir.mkdir(parents=True, exist_ok=True)

        import hashlib

        hasher = hashlib.sha256()
        tmp_path = uploads_dir / f".upload-{id(request)}.part"
        total = 0
        try:
            with open(tmp_path, "wb") as out:
                while chunk := await pdf.read(1 << 20):
                    hasher.update(chunk)
                    total += len(chunk)
                    out.write(chunk)
            if total == 0:
                raise HTTPException(status_code=400, detail="empty file")
            sha = hasher.hexdigest()
            stored = uploads_dir / f"{sha}.pdf"
            if not stored.exists():
                tmp_path.replace(stored)
            else:
                tmp_path.unlink(missing_ok=True)
        except HTTPException:
            tmp_path.unlink(missing_ok=True)
            raise
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise

        try:
            doc_id, outcome = importer.import_pdf(
                app_state.db_path,
                str(stored),
                tags,
                style=style,
                filename=Path(pdf.filename).name,
                sha=sha,
            )
        except db.DuplicateTagError as err:
            stored.unlink(missing_ok=True)
            return Response(f"tag conflict: {err}", status_code=409)
        except importer.NoSuchTagError as err:
            stored.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"unknown tag: {err}")
        except sqlite3.IntegrityError as err:
            stored.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"duplicate question row: {err}")
        except (RuntimeError, ValueError) as err:
            stored.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"not a valid question PDF: {err}")

        return RedirectResponse(
            f"/documents/{doc_id}/review?outcome={outcome}", status_code=303
        )

    # --- documents ---------------------------------------------------------
    @app.get("/documents/{doc_id}/review")
    def review(request: Request, doc_id: int, outcome: str = "", conn=Depends(get_db)):
        row = conn.execute(
            "SELECT id, filename, sha256 FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such document")
        questions = db.questions_for_document(conn, doc_id)
        return render(
            request,
            "review.html",
            {
                "doc": {"id": row[0], "filename": row[1], "sha256": row[2]},
                "questions": questions,
                "tags": db.tags_for_document(conn, doc_id),
                "outcome": outcome,
                "warned": sum(1 for q in questions if q["warnings"]),
            },
        )

    @app.post("/documents/{doc_id}/reimport")
    def reimport(
        request: Request,
        doc_id: int,
        style: str = Form("exercise"),
        conn=Depends(get_db),
    ):
        if style not in STYLES:
            raise HTTPException(status_code=400, detail=f"unknown style: {style}")
        row = conn.execute(
            "SELECT original_path FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such document")
        original = row[0]
        if not Path(original).exists():
            raise HTTPException(status_code=400, detail="original PDF file missing")
        tags = db.tags_for_document(conn, doc_id)
        _, outcome = importer.import_pdf(
            request.app.state.db_path, original, tags, style=style, replace=True
        )
        return RedirectResponse(
            f"/documents/{doc_id}/review?outcome={outcome}", status_code=303
        )

    @app.post("/documents/{doc_id}/tags")
    def assign_tag(
        request: Request, doc_id: int, tag_name: str = Form(...), conn=Depends(get_db)
    ):
        row = conn.execute("SELECT id FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such document")
        tag = db.tag_id(conn, tag_name)
        if tag is None:
            raise HTTPException(status_code=400, detail=f"unknown tag: {tag_name}")
        db.assign_tag(conn, doc_id, tag)
        conn.commit()
        if request.headers.get("HX-Request") == "true":
            return render(
                request,
                "tags_list.html",
                {"tags": db.tags_for_document(conn, doc_id), "doc_id": doc_id},
            )
        return RedirectResponse(f"/documents/{doc_id}/review", status_code=303)

    # --- tags ------------------------------------------------------------------
    @app.get("/tags")
    def tags_page(request: Request, conn=Depends(get_db)):
        return render(request, "tags.html", {"tags": _tag_options(conn)})

    @app.post("/tags")
    def create_tag(request: Request, name: str = Form(...), conn=Depends(get_db)):
        name = name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="tag name is required")
        try:
            db.add_tag(conn, name)
            conn.commit()
        except db.DuplicateTagError:
            raise HTTPException(status_code=409, detail=f"tag already exists: {name}")
        if request.headers.get("HX-Request") == "true":
            return Response(headers={"HX-Refresh": "true"})
        return RedirectResponse("/tags", status_code=303)

    @app.get("/tags/{tag_id}/questions")
    def tag_questions(request: Request, tag_id: int, conn=Depends(get_db)):
        row = conn.execute("SELECT name FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such tag")
        return render(
            request,
            "questions.html",
            {"tag": row[0], "questions": db.questions_for_tag(conn, tag_id)},
        )

    def _tag_options(conn) -> list[dict]:
        rows = conn.execute("SELECT id, name FROM tags ORDER BY name").fetchall()
        return [{"id": r[0], "name": r[1]} for r in rows]

    # --- play mode ---------------------------------------------------------
    @app.get("/play")
    def play_setup(request: Request, conn=Depends(get_db)):
        return render(request, "play_setup.html", {"tags": _tag_options(conn)})

    def _parse_tag_ids(raw: list[str]) -> list[int]:
        ids = []
        for value in raw:
            try:
                ids.append(int(value))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"bad tag id: {value}")
        return ids

    def _validate_tags(conn, tag_ids: list[int]) -> None:
        if not tag_ids:
            return
        marks = ",".join("?" for _ in tag_ids)
        rows = conn.execute(
            f"SELECT id FROM tags WHERE id IN ({marks})", tuple(tag_ids)
        ).fetchall()
        found = {r[0] for r in rows}
        missing = [tid for tid in tag_ids if tid not in found]
        if missing:
            raise HTTPException(status_code=400, detail=f"unknown tag id(s): {missing}")

    @app.post("/sessions")
    def create_session_route(
        request: Request,
        tags: list[str] = Form(default=[]),
        retry_of: int | None = Form(default=None),
        conn=Depends(get_db),
    ):
        from . import session as ses

        if tags and retry_of is not None:
            raise HTTPException(status_code=400, detail="pick tags or retry_of, not both")
        try:
            if retry_of is not None:
                if conn.execute(
                    "SELECT 1 FROM sessions WHERE id = ?", (retry_of,)
                ).fetchone() is None:
                    raise HTTPException(status_code=404, detail="no such session")
                session_id, _seed = ses.create_session(
                    conn, [], source_session_id=retry_of
                )
            else:
                tag_ids = _parse_tag_ids(tags)
                _validate_tags(conn, tag_ids)
                session_id, _seed = ses.create_session(conn, tag_ids)
        except ses.NoQuestionsError as err:
            return Response(str(err), status_code=400)
        if request.headers.get("HX-Request") == "true":
            return Response(headers={"HX-Redirect": f"/sessions/{session_id}/play"})
        return RedirectResponse(f"/sessions/{session_id}/play", status_code=303)

    def _question_response(request: Request, session_id: int, conn) -> Response:
        from . import session as ses

        current = ses.current_question(conn, session_id)
        if current is None:
            if (
                conn.execute(
                    "SELECT 1 FROM session_questions WHERE session_id = ? LIMIT 1",
                    (session_id,),
                ).fetchone()
                is None
            ):
                raise HTTPException(status_code=404, detail="no such session")
            ses.finish(conn, session_id)
            if request.headers.get("HX-Request") == "true":
                return Response(
                    headers={"HX-Redirect": f"/sessions/{session_id}/summary"}
                )
            return RedirectResponse(f"/sessions/{session_id}/summary", status_code=303)
        current["segments"] = normalise.latex_segments(current["body_latex"])
        if request.headers.get("HX-Request") == "true":
            return render(request, "play_question.html", {"q": current, "sid": session_id})
        return render(request, "play_page.html", {"q": current, "sid": session_id})

    @app.get("/sessions/{session_id}/play")
    def play(request: Request, session_id: int, conn=Depends(get_db)):
        return _question_response(request, session_id, conn)

    @app.post("/sessions/{session_id}/answer")
    def answer(
        request: Request,
        session_id: int,
        question_id: int = Form(...),
        result: str = Form(...),
        conn=Depends(get_db),
    ):
        from . import session as ses

        if result not in ses.ALLOWED_RESULTS:
            raise HTTPException(status_code=400, detail=f"result must be {ses.ALLOWED_RESULTS}")
        row = conn.execute(
            "SELECT finished FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such session")
        if row[0] is not None:
            raise HTTPException(status_code=400, detail="session already finished")
        try:
            ses.record_result(conn, session_id, question_id, result)
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err))
        return _question_response(request, session_id, conn)

    @app.post("/sessions/{session_id}/stop")
    def stop(request: Request, session_id: int, conn=Depends(get_db)):
        from . import session as ses

        if conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone() is None:
            raise HTTPException(status_code=404, detail="no such session")
        ses.finish(conn, session_id)
        if request.headers.get("HX-Request") == "true":
            return Response(headers={"HX-Redirect": f"/sessions/{session_id}/summary"})
        return RedirectResponse(f"/sessions/{session_id}/summary", status_code=303)

    @app.get("/sessions/{session_id}/summary")
    def summary(request: Request, session_id: int, conn=Depends(get_db)):
        from . import session as ses

        data = ses.summary(conn, session_id)
        if data is None:
            raise HTTPException(status_code=404, detail="no such session")
        return render(request, "summary.html", {"s": data})

    @app.get("/stats")
    def stats(request: Request, conn=Depends(get_db)):
        sessions = conn.execute(
            "SELECT id, seed, created_at, finished FROM sessions ORDER BY id DESC"
        ).fetchall()
        session_counts = conn.execute(
            "SELECT session_id, result, COUNT(*) FROM session_questions "
            "WHERE result IS NOT NULL GROUP BY session_id, result"
        ).fetchall()
        by_session: dict[int, dict[str, int]] = {}
        for sid, result, n in session_counts:
            by_session.setdefault(sid, {})[result] = n
        session_rows = []
        for row in sessions:
            tally = by_session.get(row[0], {})
            session_rows.append(
                {
                    "id": row[0],
                    "seed": row[1],
                    "created_at": row[2],
                    "finished": row[3],
                    "known": tally.get("known", 0),
                    "retry": tally.get("retry", 0),
                }
            )
        tag_counts = conn.execute(
            """
            SELECT dt.tag_id, t.name, sq.result, COUNT(*)
            FROM session_questions sq
            JOIN questions q ON q.id = sq.question_id
            JOIN document_tags dt ON dt.document_id = q.document_id
            JOIN tags t ON t.id = dt.tag_id
            WHERE sq.result IS NOT NULL
            GROUP BY dt.tag_id, t.name, sq.result
            ORDER BY t.name
            """
        ).fetchall()
        by_tag: dict[int, dict[str, int]] = {}
        for tid, name, result, n in tag_counts:
            by_tag.setdefault(tid, {})[result] = n
        tags = conn.execute("SELECT id, name FROM tags ORDER BY name").fetchall()
        tag_rows = []
        for tid, name in tags:
            tally = by_tag.get(tid, {})
            known, retry = tally.get("known", 0), tally.get("retry", 0)
            accuracy = "—" if known + retry == 0 else f"{known / (known + retry):.0%}"
            tag_rows.append({"name": name, "known": known, "retry": retry, "accuracy": accuracy})
        return render(request, "stats.html", {"sessions": session_rows, "tags": tag_rows})

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

    return app