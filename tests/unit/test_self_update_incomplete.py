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


_DEPS_UMASKS = []

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
                        lambda repo, umask=-1: _DEPS_UMASKS.append(umask)
                        or True)
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
    (origin / "newdir").mkdir()
    (origin / "newdir" / "x.py").write_text("X = 1\n")
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
        "sudo find /opt/canasta-ansible -path /opt/canasta-ansible/.venv "
        "-prune -o -type d -exec chmod g+s {} +",
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
            "sudo find /opt/canasta-ansible -path /opt/canasta-ansible/.venv "
            "-prune -o -type d -exec chmod g+s {} + && "
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


# --- umask and root warning on the shared install ---------------------------

@pytest.mark.parametrize("shared, group_writable, deps_umask", [
    (True, True, 0o002),
    (False, False, -1),
])
def test_update_umask_follows_the_install(
        tmp_path, monkeypatch, capsys, shared, group_writable, deps_umask):
    repo = _install(tmp_path, monkeypatch)
    monkeypatch.setattr(canasta_cli, "_install_group_shared",
                        lambda r: shared)
    _DEPS_UMASKS.clear()
    old = os.umask(0o022)
    try:
        canasta_cli.self_update_cli()
    finally:
        os.umask(old)
    assert "Updated Canasta CLI" in capsys.readouterr().out
    assert bool((repo / "newdir").stat().st_mode & 0o020) is group_writable
    assert bool((repo / "newdir" / "x.py").stat().st_mode & 0o020) \
        is group_writable
    assert _DEPS_UMASKS == [deps_umask]
    # The process itself keeps its umask for the command that follows.
    assert os.umask(0o022) == 0o022


@pytest.mark.parametrize("euid, run_mode, self_updated, shared, warned", [
    (0, None, None, True, True),
    (1000, None, None, True, False),
    (0, "docker", None, True, False),
    (0, None, "1", True, False),
    (0, None, None, False, False),
])
def test_root_warning_on_shared_install(
        monkeypatch, capsys, euid, run_mode, self_updated, shared, warned):
    monkeypatch.setattr(canasta_cli.os, "geteuid", lambda: euid)
    for name, value in (("CANASTA_RUN_MODE", run_mode),
                        ("CANASTA_SELF_UPDATED", self_updated)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setattr(canasta_cli, "_install_group_shared",
                        lambda r: shared)
    canasta_cli.warn_root_on_shared_install()
    err = capsys.readouterr().err
    if warned:
        assert err == (
            "Warning: canasta is running as root. The CLI install is meant "
            "to be run as a member of the canasta group; files root creates "
            "here can block later updates for that user.\n")
    else:
        assert err == ""


# --- pre-fix layouts: root repairs, members are stopped, non-members skip ----

def _other_group():
    """A group this user belongs to other than its primary one, which a
    non-root owner may chown its files to."""
    for gid in os.getgroups():
        if gid != os.getgid():
            return gid
    pytest.skip("needs a supplementary group")


@needs_non_root
def test_root_repair_normalizes_the_shared_install(
        tmp_path, monkeypatch, capsys):
    gid = _other_group()
    monkeypatch.setattr(canasta_cli.grp, "getgrnam",
                        lambda name: type("G", (), {"gr_gid": gid})())
    repo = tmp_path / "install"
    (repo / "rootdir").mkdir(parents=True)
    (repo / "rootdir" / "f.py").write_text("")
    (repo / ".venv" / "lib").mkdir(parents=True)
    for path in (repo, repo / "rootdir", repo / ".venv",
                 repo / ".venv" / "lib"):
        path.chmod(0o755)
    (repo / "rootdir" / "f.py").chmod(0o644)

    canasta_cli._repair_shared_install(str(repo))
    assert capsys.readouterr().out == (
        "Repaired group ownership and permissions of %s.\n" % repo)
    for path in (repo, repo / "rootdir"):
        st = path.stat()
        assert st.st_gid == gid
        assert st.st_mode & 0o2020 == 0o2020
    st = (repo / "rootdir" / "f.py").stat()
    assert st.st_gid == gid and st.st_mode & 0o020
    venv = (repo / ".venv" / "lib").stat()
    assert venv.st_gid != gid and not venv.st_mode & 0o2020

    canasta_cli._repair_shared_install(str(repo))
    assert capsys.readouterr().out == ""


@needs_non_root
def test_root_repair_leaves_object_and_bytecode_modes_alone(
        tmp_path, monkeypatch, capsys):
    gid = _other_group()
    monkeypatch.setattr(canasta_cli.grp, "getgrnam",
                        lambda name: type("G", (), {"gr_gid": gid})())
    repo = tmp_path / "install"
    (repo / ".git" / "objects" / "ab").mkdir(parents=True)
    (repo / "filter_plugins" / "__pycache__").mkdir(parents=True)
    canasta_cli._repair_shared_install(str(repo))
    assert "Repaired" in capsys.readouterr().out

    obj = repo / ".git" / "objects" / "ab" / "cdef"
    obj.write_text("")
    obj.chmod(0o444)
    pyc = repo / "filter_plugins" / "__pycache__" / "m.cpython-312.pyc"
    pyc.write_text("")
    pyc.chmod(0o644)
    canasta_cli._repair_shared_install(str(repo))
    assert capsys.readouterr().out == ""
    assert obj.stat().st_mode & 0o777 == 0o444
    assert pyc.stat().st_mode & 0o777 == 0o644
    assert obj.stat().st_gid == gid and pyc.stat().st_gid == gid

    # Their directories, and the group of the files, are still repaired.
    (repo / ".git" / "objects" / "ab").chmod(0o755)
    canasta_cli._repair_shared_install(str(repo))
    assert "Repaired" in capsys.readouterr().out
    assert (repo / ".git" / "objects" / "ab").stat().st_mode & 0o2020 == 0o2020
    assert obj.stat().st_mode & 0o777 == 0o444

    os.chown(pyc, -1, os.getgid())
    canasta_cli._repair_shared_install(str(repo))
    assert "Repaired" in capsys.readouterr().out
    assert pyc.stat().st_gid == gid
    assert pyc.stat().st_mode & 0o777 == 0o644


def test_root_repair_runs_only_for_root_on_a_shared_install(
        tmp_path, monkeypatch, capsys):
    repo = _install(tmp_path, monkeypatch)
    _git(repo, "checkout", "-q", "v4.1.0")
    repaired = []
    monkeypatch.setattr(canasta_cli, "_repair_shared_install",
                        lambda r: repaired.append(r))
    monkeypatch.setattr(canasta_cli, "_install_group_shared", lambda r: True)
    monkeypatch.setattr(canasta_cli, "_in_group", lambda name: False)
    for euid, expected in ((1000, []), (0, [str(repo)])):
        repaired.clear()
        monkeypatch.setattr(canasta_cli.os, "geteuid", lambda: euid)
        canasta_cli.self_update_cli()
        assert repaired == expected
    monkeypatch.setattr(canasta_cli, "_install_group_shared", lambda r: False)
    repaired.clear()
    canasta_cli.self_update_cli()
    assert repaired == []


@needs_non_root
def test_member_with_an_unwritable_dir_is_stopped_before_git_moves(
        tmp_path, monkeypatch, capsys):
    repo = _install(tmp_path, monkeypatch)
    monkeypatch.setattr(canasta_cli, "_install_group_shared", lambda r: True)
    monkeypatch.setattr(canasta_cli, "_in_group", lambda name: True)
    plugins = repo / "filter_plugins"
    plugins.chmod(0o555)
    try:
        with pytest.raises(SystemExit) as exc:
            canasta_cli.self_update_cli()
    finally:
        plugins.chmod(0o755)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert ("Error: you cannot write these directories of the Canasta CLI "
            "install at %s, so it cannot be updated:\n  %s\n" % (repo, plugins)
            ) in err
    assert "chmod g+s" in err
    assert (repo / "VERSION").read_text() == "4.0.0\n"
    assert not (repo / "filter_plugins" / "new.py").exists()


@needs_non_root
def test_non_member_keeps_the_skip_warning(tmp_path, monkeypatch, capsys):
    repo = _install(tmp_path, monkeypatch)
    monkeypatch.setattr(canasta_cli, "_install_group_shared", lambda r: True)
    monkeypatch.setattr(canasta_cli, "_in_group", lambda name: False)
    for path in (repo / "filter_plugins", repo / ".git"):
        path.chmod(0o555)
    try:
        canasta_cli.self_update_cli()
    finally:
        for path in (repo / "filter_plugins", repo / ".git"):
            path.chmod(0o755)
    err = capsys.readouterr().err
    assert "WARNING: self-update skipped" in err
    assert "sudo usermod -aG canasta $(id -un)" in err
    assert "Error:" not in err
    assert (repo / "VERSION").read_text() == "4.0.0\n"
