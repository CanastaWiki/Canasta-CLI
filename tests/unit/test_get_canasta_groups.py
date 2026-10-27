"""The native Linux installer adds the user to each group on its own.

usermod -aG with a list changes nothing when any one group is missing,
which on RHEL (no www-data group; gid 33 is tape) left the user out of
canasta too. add_user_to_groups is run with getent and usermod stubbed.
"""

import os
import re
import subprocess

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INSTALLER = os.path.join(REPO_ROOT, "get-canasta.sh")


def _run(tmp_path, groups, failing=()):
    """Run add_user_to_groups with `groups` as the host's {name: gid}."""
    with open(INSTALLER) as f:
        script = f.read()
    body = re.sub(r'\nmain "\$@"\s*$', "\n", script)
    table = " ".join("%s:%s" % (n, g) for n, g in groups.items())
    stubs = f"""
SUDO=""
getent() {{
    local e
    for e in {table}; do
        if [[ "$2" == "${{e%%:*}}" || "$2" == "${{e##*:}}" ]]; then
            echo "${{e%%:*}}:x:${{e##*:}}:"; return 0
        fi
    done
    return 2
}}
usermod() {{
    case " {' '.join(failing)} " in
        *" $2 "*) echo "usermod: cannot lock /etc/group" >&2; return 10 ;;
    esac
    echo "USERMOD $*" >> "$LOG"
}}
add_user_to_groups alice
"""
    log = str(tmp_path / "usermod.log")
    result = subprocess.run(
        ["bash", "-c", body + stubs, "get-canasta.sh"],
        capture_output=True, text=True, check=True,
        env={"PATH": os.environ["PATH"], "LOG": log},
    )
    calls = []
    if os.path.exists(log):
        with open(log) as f:
            calls = f.read().splitlines()
    return calls, result.stdout, result.stderr


def test_debian_adds_each_group_separately(tmp_path):
    calls, out, err = _run(tmp_path, {"canasta": 990, "docker": 991, "www-data": 33})
    assert calls == [
        "USERMOD -aG canasta alice",
        "USERMOD -aG docker alice",
        "USERMOD -aG www-data alice",
    ]
    assert "Added alice to the 'www-data' group." in out
    assert err == ""


def test_rhel_adds_the_gid_33_group_by_its_own_name(tmp_path):
    calls, out, err = _run(tmp_path, {"canasta": 990, "docker": 991, "tape": 33})
    assert calls == [
        "USERMOD -aG canasta alice",
        "USERMOD -aG docker alice",
        "USERMOD -aG tape alice",
    ]
    assert "'tape' (gid 33, www-data in the containers)" in out
    assert err == ""


def test_a_missing_group_is_reported_and_the_rest_still_added(tmp_path):
    calls, out, err = _run(tmp_path, {"canasta": 990, "tape": 33})
    assert calls == ["USERMOD -aG canasta alice", "USERMOD -aG tape alice"]
    assert "Group 'docker' does not exist; alice was not added to it." in err


def test_no_gid_33_group_is_reported(tmp_path):
    calls, out, err = _run(tmp_path, {"canasta": 990, "docker": 991})
    assert calls == ["USERMOD -aG canasta alice", "USERMOD -aG docker alice"]
    assert "No group has gid 33 on this host" in err


def test_a_failing_usermod_is_reported_with_its_error(tmp_path):
    calls, out, err = _run(tmp_path, {"canasta": 990, "docker": 991, "www-data": 33},
                           failing=("canasta",))
    assert calls == ["USERMOD -aG docker alice", "USERMOD -aG www-data alice"]
    assert ("Could not add alice to the 'canasta' group: "
            "usermod: cannot lock /etc/group") in err
