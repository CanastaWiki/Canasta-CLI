"""`canasta maintenance` output keeps stdout and stderr apart, so
`canasta maintenance script dumpBackup --full > dump.xml` captures only
the script's stdout."""

import sys

from direct_commands import _helpers

_CHILD = (
    "import sys; "
    "sys.stdout.write('OUT\\n'); sys.stdout.flush(); "
    "sys.stderr.write('ERR\\n'); sys.stderr.flush()"
)


def test_stream_in_container_keeps_stderr_off_stdout(monkeypatch, capfd):
    monkeypatch.setattr(_helpers, "_resolve_compose_cmd",
                        lambda inst: [sys.executable, "-c", _CHILD])
    monkeypatch.setattr(_helpers, "_compose_exec_args",
                        lambda service, user=None: [])
    rc = _helpers._stream_in_container(
        "mysite", {"host": "localhost", "path": ""}, "true")
    assert rc == 0
    captured = capfd.readouterr()
    assert captured.out == "OUT\n"
    assert captured.err == "ERR\n"
