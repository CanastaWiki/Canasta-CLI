"""The installer's closing summary reports the directory it installed to.

main() is run with the install, platform-detection and upgrade steps
stubbed out, so only argument parsing and the summary are exercised.
"""

import os
import re
import subprocess

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INSTALLER = os.path.join(REPO_ROOT, "get-canasta.sh")


def _run_main(platform, *args, home="/home/tester"):
    with open(INSTALLER) as f:
        script = f.read()
    # Drop the trailing invocation so the functions can be sourced.
    body = re.sub(r'\nmain "\$@"\s*$', "\n", script)
    stubs = f"""
detect_platform() {{ PLATFORM={platform}; IS_WSL=false; }}
detect_existing_install() {{ EXISTING_INSTALL=""; }}
announce_upgrade() {{ :; }}
install_native_linux() {{ :; }}
install_native_macos() {{ :; }}
install_docker_mode() {{ :; }}
post_install_upgrade() {{ :; }}
main "$@"
"""
    env = {"PATH": os.environ["PATH"], "HOME": home}
    result = subprocess.run(
        ["bash", "-c", body + stubs, "get-canasta.sh", *args],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    match = re.search(r"Install dir:\s+([^\s\x1b]+)", result.stdout)
    assert match, result.stdout
    return match.group(1)


@pytest.mark.parametrize(
    "platform,default",
    [("linux", "/opt/canasta-ansible"), ("macos", "/home/tester/canasta-ansible")],
)
class TestInstallDirInSummary:
    def test_prefix_is_reported(self, platform, default):
        assert _run_main(platform, "--native", "--prefix", "/srv/canasta") == "/srv/canasta"

    def test_prefix_equals_form_is_reported(self, platform, default):
        assert _run_main(platform, "--native", "--prefix=/srv/canasta") == "/srv/canasta"

    def test_default_is_reported_without_prefix(self, platform, default):
        assert _run_main(platform, "--native") == default
