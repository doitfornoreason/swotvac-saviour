"""swotvac-saviour: parse question PDFs into structured, shuffled practice decks."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import normalise, parser
from .ingestion import load_pdf


def _cmd_parse(args: argparse.Namespace) -> int:
    doc = load_pdf(args.pdf)
    questions = parser.split_questions(doc, style=args.style)
    for q in questions:
        q.body_latex = normalise.to_latex(q.body)
    payload = [q.to_dict() for q in questions]
    with open(args.out, "w", encoding="utf8") as fh:
        json.dump(payload, fh, ensure_ascii=True, indent=2)
    print(f"Parsed {len(questions)} questions -> {args.out}")
    warned = sum(1 for q in questions if q.warnings)
    if warned:
        print(f"{warned} questions carry extraction warnings", file=sys.stderr)
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    from . import importer

    try:
        doc_id, outcome = importer.import_pdf(
            args.db, args.pdf, args.tag or [], style=args.style
        )
    except sqlite3.IntegrityError as err:
        print(f"Import failed: duplicate question row ({err})", file=sys.stderr)
        return 1
    except importer.NoSuchTagError as err:
        print(f"Unknown tag: {err}. Create it with 'swotvac-saviour tag add {err}'.", file=sys.stderr)
        return 1
    except (FileNotFoundError, OSError) as err:
        print(f"Cannot read {args.pdf}: {err}", file=sys.stderr)
        return 1
    except Exception as err:  # pymupdf raises FileDataError/RuntimeError for bad PDFs
        print(f"Import failed: not a valid question PDF ({err})", file=sys.stderr)
        return 1
    if outcome == "skipped":
        print(
            f"Already imported {args.pdf}; skipping "
            f"(document id {doc_id}, tags assigned from request)"
        )
    else:
        print(
            f"{'Replaced' if outcome == 'replaced' else 'Imported'} "
            f"{Path(args.pdf).name} as document id {doc_id} -> {args.db}"
        )
    return 0


def _cmd_tag(args: argparse.Namespace) -> int:
    from . import db

    conn = db.connect(args.db)
    try:
        db.init_db(conn)
        if args.action == "add":
            if not args.name:
                print("tag add requires a NAME", file=sys.stderr)
                return 2
            try:
                db.add_tag(conn, args.name)
            except db.DuplicateTagError:
                print(f"Tag already exists: {args.name}", file=sys.stderr)
                return 1
            conn.commit()
            print(f"Added tag: {args.name}")
            return 0
        if args.action == "list":
            for name in db.list_tags(conn):
                print(name)
            return 0
        if args.action == "assign":
            row = conn.execute(
                "SELECT id FROM documents WHERE id = ?", (args.document_id,)
            ).fetchone()
            if row is None:
                print(f"No document with id {args.document_id}", file=sys.stderr)
                return 1
            tag = db.tag_id(conn, args.name)
            if tag is None:
                print(f"Unknown tag: {args.name}", file=sys.stderr)
                return 1
            db.assign_tag(conn, args.document_id, tag)
            conn.commit()
            print(f"Assigned tag '{args.name}' to document {args.document_id}")
            return 0
        print(f"Unknown tag action: {args.action}", file=sys.stderr)
        return 2
    finally:
        conn.close()


def _cmd_documents(args: argparse.Namespace) -> int:
    from . import db

    conn = db.connect(args.db)
    try:
        db.init_db(conn)
        rows = conn.execute(
            "SELECT id, filename, sha256 FROM documents ORDER BY id"
        ).fetchall()
        for doc_id, filename, sha in rows:
            tags = ", ".join(db.tags_for_document(conn, doc_id)) or "-"
            print(f"{doc_id}\t{filename}\t{sha[:12]}...\t{tags}")
        return 0
    finally:
        conn.close()


def _cmd_serve(args: argparse.Namespace) -> int:
    return _run_server(args.db, host=args.host, port=args.port)


def _link_for(host: str, port: int) -> str:
    display = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    return f"http://{display}:{port}"


def _run_server(
    db_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8321,
    study_dir: str | Path | None = None,
) -> int:
    from .web import create_app

    import uvicorn

    key, source = transcribe.load_api_key_source(study_dir)
    if key:
        print(f"API key: found ({source})")
    else:
        print("API key: missing — enter it on the setup page")
    print(f"Visit: {_link_for(host, port)}/setup" if not key
          else f"Visit: {_link_for(host, port)}")
    uvicorn.run(create_app(db_path, study_dir), host=host, port=port)
    return 0


def _cmd_boot(_args: argparse.Namespace) -> int:
    from .transcribe import load_api_key

    default_dir = Path.home() / "swotvac-study"
    try:
        raw = input(f"Study directory [{default_dir}]: ").strip()
    except EOFError:
        raw = ""
    study_dir = Path(raw).expanduser() if raw else default_dir
    db_dir = study_dir / ".database"
    db_dir.mkdir(parents=True, exist_ok=True)
    print(f"Study dir: {study_dir}")
    print(f"Database:  {db_dir / 'swotvac.db'}")
    print("Starting server...")
    return _run_server(str(db_dir / "swotvac.db"), study_dir=str(study_dir))


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="swotvac-saviour")
    sub = ap.add_subparsers(dest="command")
    p = sub.add_parser("parse", help="split a question PDF into questions.json")
    p.add_argument("pdf", help="path to the question PDF")
    p.add_argument("--out", default="questions.json", help="output JSON path")
    p.add_argument(
        "--style",
        choices=sorted(parser._LABEL_PATTERNS),
        default="exercise",
        help="question label style: exercise (Exercise 1.1.) or number (4.)",
    )
    im = sub.add_parser("import", help="parse a PDF and store it in the database")
    im.add_argument("pdf", help="path to the question PDF")
    im.add_argument("--db", default="swotvac.db", help="SQLite database path")
    im.add_argument(
        "--tag", action="append", default=[], metavar="NAME",
        help="assign a subject tag (auto-created); repeatable",
    )
    im.add_argument(
        "--style",
        choices=sorted(parser._LABEL_PATTERNS),
        default="exercise",
        help="question label style",
    )
    t = sub.add_parser("tag", help="manage subject tags")
    t.add_argument("action", choices=["add", "list", "assign"])
    t.add_argument("name", nargs="?", help="tag name (add/assign)")
    t.add_argument("document_id", nargs="?", type=int, help="document id (assign)")
    t.add_argument("--db", default="swotvac.db", help="SQLite database path")
    d = sub.add_parser("documents", help="list imported documents")
    d.add_argument("--db", default="swotvac.db", help="SQLite database path")
    s = sub.add_parser("serve", help="run the web UI (single-user, local only)")
    s.add_argument("--db", default="swotvac.db", help="SQLite database path")
    s.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    s.add_argument("--port", type=int, default=8321, help="port to listen on")
    return ap


def main() -> None:
    args = build_arg_parser().parse_args()
    if not args.command:
        sys.exit(_cmd_boot(args))
    if args.command == "parse":
        sys.exit(_cmd_parse(args))
    if args.command == "import":
        sys.exit(_cmd_import(args))
    if args.command == "tag":
        sys.exit(_cmd_tag(args))
    if args.command == "documents":
        sys.exit(_cmd_documents(args))
    if args.command == "serve":
        sys.exit(_cmd_serve(args))