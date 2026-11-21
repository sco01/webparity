# SPDX-License-Identifier: BSD-2-Clause
import pytest

from webparity import __version__
from webparity.cli import COMMANDS, main


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "visit" in capsys.readouterr().out


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_commands_exist_but_are_not_implemented(command, capsys):
    assert main([command]) == 2
    assert "not implemented yet" in capsys.readouterr().err


def test_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out
