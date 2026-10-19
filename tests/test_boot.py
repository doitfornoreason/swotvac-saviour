"""First-run boot flow: study-dir prompt, db dir creation, link choice."""

from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import patch

import pytest

import swotvac_saviour
from swotvac_saviour import _cmd_boot, main


def _boot(study: Path, key: str | None = None):
    from swotvac_saviour import _cmd_boot as boot

    with patch("builtins.input", return_value=str(study)), \
         patch("uvicorn.run") as run, \
         patch("swotvac_saviour.transcribe.load_api_key_source",
               return_value=(key, None if key is None else "test")):
        result = boot(None)
    return result, run


def test_boot_missing_key_prints_setup_link(tmp_path, capsys):
    study = tmp_path / "study"
    result, run = _boot(study)
    assert result == 0
    run.assert_called_once()
    app = run.call_args.args[0]
    assert str(study / ".database" / "swotvac.db") == app.state.db_path
    assert str(study) == app.state.study_dir
    assert (study / ".database").is_dir()
    out = capsys.readouterr().out
    assert "Visit: http://127.0.0.1:8321/setup" in out
    assert "missing" in out


def test_boot_with_key_prints_root_link(tmp_path, capsys):
    study = tmp_path / "study"
    study.mkdir()
    result, run = _boot(study, key="sk-or-test")
    assert result == 0
    out = capsys.readouterr().out
    assert "Visit: http://127.0.0.1:8321\n" in out
    assert "/setup" not in out


def test_boot_empty_input_uses_home_default(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(swotvac_saviour.Path, "home", lambda: tmp_path)
    with patch("builtins.input", return_value=""), \
         patch("uvicorn.run"):
        result = swotvac_saviour._cmd_boot(None)
    assert result == 0
    assert (tmp_path / "swotvac-study" / ".database").is_dir()


def test_main_without_command_dispatches_boot(monkeypatch):
    called = []
    monkeypatch.setattr(swotvac_saviour, "_cmd_boot", lambda args: called.append(1) or 0)
    monkeypatch.setattr(sys, "argv", ["swotvac-saviour"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert called == [1]


def test_subcommand_not_required():
    args = swotvac_saviour.build_arg_parser().parse_args([])
    assert args.command is None
