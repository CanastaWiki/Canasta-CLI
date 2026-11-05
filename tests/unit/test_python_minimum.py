"""The CLI stops on a Python older than its requirements need.

A venv created with an older Python cannot install requirements.txt, so the
CLI kept running against dependencies that never updated.
"""

import os
import re
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)
import canasta as canasta_cli  # noqa: E402


class TestTheCheck:
    def test_an_older_python_stops_with_the_venv_commands(self, capsys):
        with pytest.raises(SystemExit) as exc:
            canasta_cli.check_python_version((3, 11, 4, "final", 0))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "needs Python 3.12 or newer" in err
        assert "running on Python 3.11.4" in err
        assert "python3.12 -m venv --clear %s" % os.path.join(
            canasta_cli.SCRIPT_DIR, ".venv") in err
        assert "requirements.txt" in err
        assert "requirements.yml" in err

    @pytest.mark.parametrize("version", [(3, 12, 0), (3, 13, 1), (4, 0, 0)])
    def test_the_minimum_and_newer_go_ahead(self, version):
        canasta_cli.check_python_version(version)

    def test_main_checks_first(self):
        with open(os.path.join(REPO_ROOT, "canasta.py")) as f:
            source = f.read()
        body = source.split("\ndef main():\n", 1)[1]
        assert body.lstrip().startswith("check_python_version()")


class TestOneMinimum:
    def test_requirements_were_compiled_for_the_minimum(self):
        with open(os.path.join(REPO_ROOT, "requirements.txt")) as f:
            header = f.read(400)
        compiled = re.search(r"pip-compile with Python (\d+)\.(\d+)", header)
        assert tuple(int(n) for n in compiled.groups()) == canasta_cli.MIN_PYTHON

    def test_the_installer_requires_the_minimum(self):
        with open(os.path.join(REPO_ROOT, "get-canasta.sh")) as f:
            installer = f.read()
        minor = canasta_cli.MIN_PYTHON[1]
        assert '"$pyver" -lt %d ]]' % minor in installer
        assert "Python 3.%d+ required" % minor in installer
        assert "requires Python 3.%d+" % minor in installer
