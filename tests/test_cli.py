# SPDX-License-Identifier: BSD-2-Clause
import pytest

from webparity import __version__
from webparity.cli import COMMANDS, build_parser, main


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "visit" in capsys.readouterr().out


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_has_help(command, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main([command, "--help"])
    assert exit_info.value.code == 0
    assert "usage" in capsys.readouterr().out


def test_commands_match_parser():
    parser = build_parser()
    choices = next(a for a in parser._actions if a.dest == "command").choices
    assert set(choices) == set(COMMANDS)


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_sites_and_candidates(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WEBPARITY_DATA", str(tmp_path))
    monkeypatch.delenv("WEBPARITY_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    assert main(["sites", "add", "https://www.example.org/", "example.net"]) == 0
    assert "2 sites added" in capsys.readouterr().out
    assert main(["sites"]) == 0
    assert "queued" in capsys.readouterr().out
    assert main(["candidates"]) == 0
