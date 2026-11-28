"""`canasta upgrade` in Docker mode stays within the installed major.

The wrapper picks the CLI image itself, before canasta.py runs, so the
native self-update's major cap and the checks and backups of
`--allow-major` apply only if the wrapper applies them too.
"""

import os
from urllib.parse import urlparse

import pytest

from test_canasta_docker import assert_env_var, run_dry

REPO = "ghcr.io/canastawiki/canasta-ansible:"
TAGS_JSON = '[{"name": "v5.0.0"}, {"name": "v5.0.0-rc.1"}, {"name": "v4.34.1"}, {"name": "v4.30.0"}]'


@pytest.fixture
def stubs(tmp_path):
    """docker and curl stubs. curl answers the tags API with $TAGS_JSON and
    logs every URL it is asked for; docker succeeds and prints nothing."""
    bin_dir = str(tmp_path / "bin")
    os.makedirs(bin_dir)
    log = str(tmp_path / "curl.log")
    with open(os.path.join(bin_dir, "curl"), "w") as f:
        f.write('#!/bin/sh\n'
                'for a in "$@"; do case "$a" in http*) echo "$a" >> "%s" ;; esac; done\n'
                'case "$*" in *api.github.com*) printf \'%%s\' "$TAGS_JSON" ;; *) exit 1 ;; esac\n'
                % log)
    with open(os.path.join(bin_dir, "docker"), "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    for name in ("curl", "docker"):
        os.chmod(os.path.join(bin_dir, name), 0o755)
    return bin_dir, log


def _upgrade(stubs, tmp_path, installed, args=(), tags=TAGS_JSON):
    bin_dir, log = stubs
    config = str(tmp_path / "config")
    os.makedirs(config)
    with open(os.path.join(config, "cli_image_tag"), "w") as f:
        f.write(installed + "\n")
    out, err = run_dry(["upgrade"] + list(args), env={
        "CANASTA_CONFIG_DIR": config, "TAGS_JSON": tags,
        "PATH": bin_dir + os.pathsep + os.environ["PATH"]})
    with open(os.path.join(config, "cli_image_tag")) as f:
        pinned = f.read().strip()
    urls = open(log).read().split() if os.path.exists(log) else []
    return out, err, pinned, urls


class TestTheDefaultStaysWithinTheMajor:
    def test_a_newer_major_is_not_taken(self, stubs, tmp_path):
        out, _, pinned, _ = _upgrade(stubs, tmp_path, "4.30.0")
        assert pinned == "4.34.1"
        assert REPO + "4.34.1" in out
        assert not any("CANASTA_MAJOR_STEP" in a for a in out)

    def test_the_newer_major_is_named(self, stubs, tmp_path):
        out, _, _, _ = _upgrade(stubs, tmp_path, "4.30.0")
        assert any("A newer major release (5.0.0)" in a
                   and "--allow-major" in a for a in out)

    def test_a_pre_release_is_never_picked(self, stubs, tmp_path):
        _, _, pinned, _ = _upgrade(stubs, tmp_path, "5.0.0")
        assert pinned == "5.0.0"


class TestAPreReleaseInstall:
    """An install of X.0.0-rc.N has no X.x release to move to yet."""

    TAGS = '[{"name": "v5.0.0-rc.1"}, {"name": "v4.34.1"}]'

    def test_it_keeps_the_installed_cli(self, stubs, tmp_path):
        out, _, pinned, _ = _upgrade(stubs, tmp_path, "5.0.0-rc.1",
                                     tags=self.TAGS)
        assert pinned == "5.0.0-rc.1"
        assert REPO + "5.0.0-rc.1" in out
        assert any("no 5.x release yet" in a for a in out)

    def test_it_keeps_its_wrapper(self, stubs, tmp_path):
        _, _, _, urls = _upgrade(stubs, tmp_path, "5.0.0-rc.1",
                                 tags=self.TAGS)
        assert "raw.githubusercontent.com" not in {urlparse(u).hostname for u in urls}

    def test_allow_major_does_not_move_it_back(self, stubs, tmp_path):
        out, _, pinned, _ = _upgrade(stubs, tmp_path, "5.0.0-rc.1",
                                     ["--allow-major"], tags=self.TAGS)
        assert pinned == "5.0.0-rc.1"
        assert not any("CANASTA_MAJOR_STEP" in a for a in out)


class TestAllowMajorGoesInSteps:
    def test_it_runs_the_checks_on_the_latest_of_the_installed_major(
            self, stubs, tmp_path):
        out, _, pinned, _ = _upgrade(stubs, tmp_path, "4.30.0",
                                     ["--allow-major"])
        # The pin moves to 5.0.0 only after the checks pass, which the
        # dry-run harness stops before.
        assert pinned == "4.34.1"
        assert REPO + "4.34.1" in out
        assert_env_var(out, "CANASTA_MAJOR_STEP", "5")

    def test_within_the_latest_major_there_is_no_step(self, stubs, tmp_path):
        out, _, pinned, _ = _upgrade(stubs, tmp_path, "5.0.0",
                                     ["--allow-major"])
        assert pinned == "5.0.0"
        assert not any("CANASTA_MAJOR_STEP" in a for a in out)


def test_the_move_follows_the_checks():
    with open(os.path.join(os.path.dirname(__file__), "..", "..",
                           "canasta-docker")) as f:
        text = f.read()
    block = text[text.index('if [[ -n "$MAJOR_TAG" ]]; then\n    _major_rc=0'):]
    block = block[:block.index("\nfi\n")]
    assert block.index('"$_major_rc" -ne 0') < block.index('> "$PINNED_TAG_FILE"')
    assert block.index('"$DRY_RUN" == "true"') < block.index('> "$PINNED_TAG_FILE"')
    assert block.index("pull") < block.index('> "$PINNED_TAG_FILE"')
