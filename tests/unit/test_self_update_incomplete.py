"""A self-update that git could only partly write must not pass for a
success.

`git checkout` reports files it cannot write in an unwritable directory
and still exits 0, having moved HEAD and VERSION. The CLI judges the move
by the tree instead, and checks the tree on every run of a
self-update-managed install, so a half-updated install is reported with
the repair commands rather than failing later in a playbook. A developer's
clone with local edits is left alone.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import canasta as canasta_cli  # noqa: E402
from direct_commands import _helpers, doctor  # noqa: E402


needs_non_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root can write an unwritable directory")


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "advice.detachedHead=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv("CANASTA_SELF_UPDATED", raising=False)
    monkeypatch.setattr(canasta_cli, "_self_update_refresh_deps",
                        lambda repo: True)
    execv_calls = []
    monkeypatch.setattr(canasta_cli.os, "execv",
                        lambda *a: execv_calls.append(a))
    yield execv_calls
    os.environ.pop("CANASTA_SELF_UPDATED", None)


def _install(tmp_path, monkeypatch, managed=True):
    """An install cloned from an origin with releases v4.0.0 and v4.1.0,
    checked out at v4.0.0. v4.1.0 adds a file to filter_plugins/."""
    origin = tmp_path / "origin"
    (origin / "filter_plugins").mkdir(parents=True)
    _git(origin, "init", "-q", "-b", "main")
    (origin / "VERSION").write_text("4.0.0\n")
    (origin / "filter_plugins" / "old.py").write_text("OLD = 1\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "4.0.0")
    _git(origin, "tag", "v4.0.0")
    (origin / "VERSION").write_text("4.1.0\n")
    (origin / "filter_plugins" / "new.py").write_text("NEW = 1\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "4.1.0")
    _git(origin, "tag", "v4.1.0")

    repo = tmp_path / "install"
    _git(tmp_path, "clone", "-q", str(origin), str(repo))
    _git(repo, "checkout", "-q", "v4.0.0")
    if managed:
        (repo / "BUILD_COMMIT").write_text("old\n")
    monkeypatch.setattr(canasta_cli, "SCRIPT_DIR", str(repo))
    return repo


@needs_non_root
def test_incomplete_move_is_reported_and_stops(
        tmp_path, monkeypatch, capsys, _isolate):
    repo = _install(tmp_path, monkeypatch)
    plugins = repo / "filter_plugins"
    plugins.chmod(0o555)
    try:
        with pytest.raises(SystemExit) as exc:
            canasta_cli.self_update_cli()
    finally:
        plugins.chmod(0o755)
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    assert "Updated Canasta CLI" not in out
    assert "Error: the update to v4.1.0 is incomplete" in err
    assert "filter_plugins/new.py" in err
    assert "Permission denied" in err
    assert "git -C %s checkout -- ." % repo in err
    assert _isolate == []
    # HEAD moved, so the build is recorded for the repaired tree.
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo,
        capture_output=True, text=True).stdout.strip()
    assert (repo / "BUILD_COMMIT").read_text().strip() == head


@needs_non_root
def test_up_to_date_install_left_incomplete_is_reported(
        tmp_path, monkeypatch, capsys, _isolate):
    repo = _install(tmp_path, monkeypatch)
    plugins = repo / "filter_plugins"
    plugins.chmod(0o555)
    try:
        _git(repo, "checkout", "-q", "v4.1.0")
    finally:
        plugins.chmod(0o755)
    with pytest.raises(SystemExit) as exc:
        canasta_cli.self_update_cli()
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    assert "already up to date" not in out
    assert ("Error: the Canasta CLI install at %s is incomplete; these "
            "files do not match version 4.1.0" % repo) in err
    assert "D filter_plugins/new.py" in err
    assert "Repair the install, then re-run the command:" in err

    _git(repo, "checkout", "--", ".")
    canasta_cli.self_update_cli()
    assert "already up to date" in capsys.readouterr().out


def test_complete_move_succeeds(tmp_path, monkeypatch, capsys, _isolate):
    repo = _install(tmp_path, monkeypatch)
    canasta_cli.self_update_cli()
    out = capsys.readouterr().out
    assert "Updated Canasta CLI from 4.0.0" in out
    assert (repo / "filter_plugins" / "new.py").exists()
    assert len(_isolate) == 1


def test_developer_clone_with_local_edits_is_not_blocked(
        tmp_path, monkeypatch, capsys, _isolate):
    repo = _install(tmp_path, monkeypatch, managed=False)
    _git(repo, "checkout", "-q", "v4.1.0")
    (repo / "filter_plugins" / "old.py").write_text("EDITED = 1\n")
    canasta_cli.self_update_cli()
    out, err = capsys.readouterr()
    assert "already up to date" in out
    assert "incomplete" not in err


def test_repair_commands_follow_the_install_group():
    assert _helpers._install_repair_commands("/opt/canasta-ansible", True) == [
        "sudo chgrp -R canasta /opt/canasta-ansible",
        "sudo chmod -R g+w /opt/canasta-ansible",
        "git -C /opt/canasta-ansible checkout -- .",
    ]
    assert _helpers._install_repair_commands(
        "/home/u/canasta-ansible", False, "u") == [
        "sudo chown -R u /home/u/canasta-ansible",
        "git -C /home/u/canasta-ansible checkout -- .",
    ]


# --- doctor -----------------------------------------------------------------

def _doctor(selfupdate):
    parts = [
        "Python 3.12.0", "Docker version 27.0.0",
        "Docker Compose version v2.30.0", "OK",
        "user docker www-data canasta", "OK", "v3.15.0", "k3s version v1.30.0",
        "REACHABLE", "INSTALLED", "git version 2.45.0", "OK", "OK",
        "Linux", "16 GB", "50G", "1024", "", "OK", selfupdate,
    ]
    d = _helpers._SENTINEL
    return doctor._parse_doctor(("\n" + d + "\n").join(parts) + "\n", "h")


def test_doctor_reports_unwritable_dirs_and_incomplete_tree():
    result = _doctor(
        "WRITABLE\ndir=/opt/canasta-ansible\ngroup=canasta\n"
        "unwritable=1 /opt/canasta-ansible/filter_plugins\nchanged=5")
    assert ("Self-update:     BLOCKED — you cannot write 1 of the "
            "directories in /opt/canasta-ansible (first: "
            "/opt/canasta-ansible/filter_plugins)") in result
    assert ("CLI install:     INCOMPLETE — files in /opt/canasta-ansible do "
            "not match the installed version (5 changed or missing)") in result
    assert ("Repair with: sudo chgrp -R canasta /opt/canasta-ansible && "
            "sudo chmod -R g+w /opt/canasta-ansible && "
            "git -C /opt/canasta-ansible checkout -- .") in result
    assert "Self-update:     OK" not in result


def test_doctor_healthy_install_is_ok():
    result = _doctor(
        "WRITABLE\ndir=/opt/canasta-ansible\ngroup=canasta\n"
        "unwritable=0 \nchanged=0")
    assert "Self-update:     OK (install dir writable)" in result
    assert "INCOMPLETE" not in result


@needs_non_root
def test_doctor_install_probe(tmp_path, monkeypatch):
    repo = _install(tmp_path, monkeypatch)
    plugins = repo / "filter_plugins"
    plugins.chmod(0o555)
    try:
        _git(repo, "checkout", "-q", "v4.1.0")
        out = subprocess.run(
            [sys.executable, "-c", doctor._INSTALL_PROBE, str(repo)],
            capture_output=True, text=True, check=True).stdout
    finally:
        plugins.chmod(0o755)
    _, install = doctor._parse_install_probe("WRITABLE\n" + out)
    assert install["unwritable"] == 1
    assert install["first_unwritable"] == str(plugins)
    assert install["changed"] == 1
