"""canasta-native records the CLI path that cron entries call.

In docker mode it is the CLI container's entrypoint, and canasta-docker has
already passed its host path; that one must survive, since the host's cron
cannot run a path inside the container.
"""

import os
import subprocess

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _cli_bin(tmp_path, env_extra):
    # Run canasta-native from a copy whose canasta.py only prints the variable.
    (tmp_path / "canasta.py").write_text(
        "import os\nprint(os.environ.get('CANASTA_CLI_BIN', ''))\n")
    wrapper = tmp_path / "canasta-native"
    with open(os.path.join(REPO_ROOT, "canasta-native")) as f:
        wrapper.write_text(f.read())
    wrapper.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if k not in ("CANASTA_CLI_BIN", "CANASTA_RUN_MODE")}
    env.update(env_extra)
    out = subprocess.run([str(wrapper)], capture_output=True, text=True,
                         env=env, check=True)
    return out.stdout.strip(), str(wrapper.resolve())


def test_native_records_its_own_path(tmp_path):
    got, wrapper = _cli_bin(tmp_path, {})
    assert got == wrapper


def test_native_ignores_a_stray_value_outside_docker_mode(tmp_path):
    got, wrapper = _cli_bin(tmp_path, {"CANASTA_CLI_BIN": "/elsewhere/canasta"})
    assert got == wrapper


def test_docker_mode_keeps_the_wrappers_host_path(tmp_path):
    got, _ = _cli_bin(tmp_path, {"CANASTA_RUN_MODE": "docker",
                                 "CANASTA_CLI_BIN": "/usr/local/bin/canasta"})
    assert got == "/usr/local/bin/canasta"


def test_docker_mode_without_a_passed_path_falls_back(tmp_path):
    got, wrapper = _cli_bin(tmp_path, {"CANASTA_RUN_MODE": "docker"})
    assert got == wrapper
