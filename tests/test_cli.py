"""CLI sync subcommand: error paths, fresh confirm, wiring, default db path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import swotvac_saviour


def _run(monkeypatch, tmp_path, argv, *, sync_mock=None, prompt=None, key="sk-or-test"):
    monkeypatch.setattr(sys, "argv", ["swotvac-saviour", *argv])
    monkeypatch.setattr(
        "swotvac_saviour.transcribe.load_api_key_source",
        lambda d: (key, "test") if key else (None, None),
    )
    if sync_mock is not None:
        monkeypatch.setattr("swotvac_saviour.sync.sync_directory", sync_mock)
    if prompt is not None:
        monkeypatch.setattr("builtins.input", lambda _p: prompt)
    with pytest.raises(SystemExit) as exc:
        swotvac_saviour.main()
    return exc.value.code


def test_sync_wiring_default_db_and_flags(monkeypatch, tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    captured = {}

    def fake_sync(conn, directory, **kwargs):
        captured.update(kwargs, directory=directory)
        captured["conn_path"] = str(conn.execute("PRAGMA database_list").fetchone()[2])
        return swotvac_saviour.sync.SyncResult()

    rc = _run(monkeypatch, tmp_path,
              ["sync", str(study), "--model", "m/x", "--cap", "5"],
              sync_mock=fake_sync)
    assert rc == 0
    assert captured["model"] == "m/x"
    assert captured["cap"] == 5
    assert captured["fresh"] is False
    assert captured["directory"] == study
    assert captured["conn_path"] == str(study / ".database" / "swotvac.db")


def test_sync_default_model(monkeypatch, tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    captured = {}

    def fake_sync(conn, directory, **kwargs):
        captured.update(kwargs)
        return swotvac_saviour.sync.SyncResult()

    _run(monkeypatch, tmp_path, ["sync", str(study)], sync_mock=fake_sync)
    assert captured["model"] == swotvac_saviour.transcribe.DEFAULT_MODEL


def test_sync_missing_key_fails_fast(monkeypatch, tmp_path, capsys):
    study = tmp_path / "study"
    study.mkdir()
    rc = _run(monkeypatch, tmp_path, ["sync", str(study)], key=None)
    assert rc == 1
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err


def test_sync_rejects_missing_directory(monkeypatch, tmp_path, capsys):
    study = tmp_path / "study"
    rc = _run(monkeypatch, tmp_path, ["sync", str(study)], sync_mock=lambda *a, **k: None)
    assert rc == 1
    assert "not a directory" in capsys.readouterr().err


def test_sync_fresh_requires_wipe_confirmation(monkeypatch, tmp_path, capsys):
    study = tmp_path / "study"
    study.mkdir()
    called = []

    def fake_sync(conn, directory, **kwargs):
        called.append(1)
        return swotvac_saviour.sync.SyncResult()

    rc = _run(monkeypatch, tmp_path, ["sync", str(study), "--fresh"],
              sync_mock=fake_sync, prompt="no")
    assert rc == 1
    assert called == []
    assert "cancelled" in capsys.readouterr().err


def test_sync_fresh_confirmed_runs_wipe(monkeypatch, tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    captured = {}

    def fake_sync(conn, directory, **kwargs):
        captured.update(kwargs)
        return swotvac_saviour.sync.SyncResult()

    rc = _run(monkeypatch, tmp_path, ["sync", str(study), "--fresh"],
              sync_mock=fake_sync, prompt="wipe")
    assert rc == 0
    assert captured["fresh"] is True


def test_sync_explicit_db_path(monkeypatch, tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    custom = tmp_path / "elsewhere" / "custom.db"
    captured = {}

    def fake_sync(conn, directory, **kwargs):
        captured["conn_path"] = str(conn.execute("PRAGMA database_list").fetchone()[2])
        return swotvac_saviour.sync.SyncResult()

    rc = _run(monkeypatch, tmp_path, ["sync", str(study), "--db", str(custom)],
              sync_mock=fake_sync)
    assert rc == 0
    assert captured["conn_path"] == str(custom)
    assert custom.exists()