"""Output printed before canasta.py replaces its process must not be lost.

os.execv/os.execvp discard Python's stdio buffers, and stdout is
block-buffered when it is a pipe or file. Each test runs a child
interpreter with stdout and stderr captured as pipes, prints, then
reaches the real exec call with a target that just exits.
"""

import os
import subprocess
import sys
import textwrap

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TRUE = "/usr/bin/true" if os.path.exists("/usr/bin/true") else "/bin/true"


def _run_child(body, tmp_path):
    script = textwrap.dedent("""\
        import os
        import sys
        sys.path.insert(0, %r)
        import canasta
        """ % REPO) + textwrap.dedent(body)
    env = dict(os.environ, CANASTA_CONFIG_DIR=str(tmp_path))
    env.pop("CANASTA_SELF_UPDATED", None)
    env.pop("CANASTA_FORCE_ANSIBLE", None)
    env.pop("PYTHONUNBUFFERED", None)
    return subprocess.run(
        [sys.executable, "-c", script],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        env=env, timeout=60, check=False,
    )


def test_self_update_reexec_flushes_pending_output(tmp_path):
    result = _run_child("""
        real_execv = os.execv
        os.execv = lambda path, argv: real_execv(%r, ["true"])
        print("Updated Canasta CLI from 1.0.0 (aaa) to 1.1.0 (bbb).")
        print("dep warning", file=sys.stderr)
        canasta._self_update_reexec()
    """ % TRUE, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Updated Canasta CLI from 1.0.0 (aaa) to 1.1.0 (bbb)." \
        in result.stdout
    assert "dep warning" in result.stderr


def test_ansible_handoff_flushes_self_update_output(tmp_path):
    result = _run_child("""
        canasta.acquire_instance_lock = lambda *a: None
        canasta.check_docker_mode_can_reach_runtime = lambda a: None
        canasta.confirm_destructive_command = lambda *a: None
        canasta.find_ansible_playbook = lambda: %r
        canasta.build_ansible_args = lambda pb, name, args, data: [pb]
        canasta.self_update_cli = lambda **kw: print(
            "Canasta CLI is already up to date.")
        sys.argv = ["canasta", "upgrade"]
        canasta.main()
    """ % TRUE, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Canasta CLI is already up to date." in result.stdout
