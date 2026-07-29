"""The integration suite must test the checked-out tree, not a release.

`canasta upgrade` self-updates before running its playbook. It decides
whether to move with `git merge-base --is-ancestor <latest tag> HEAD`,
which cannot succeed in CI's depth-1 clone -- the history is not there,
so the check fails even when HEAD genuinely descends from the tag. The
CLI concludes it is behind, checks out the release tag, and re-execs.

Two consequences, both silent: every test sequenced after `upgrade` runs
the released CLI, and `canasta upgrade` itself runs the released upgrade
path -- so the upgrade leg never exercised a branch's changes to it.

CANASTA_SELF_UPDATED short-circuits the guard at the top of
self_update_cli(). These are structural guards; the behavior itself is
only observable in a shallow clone.
"""

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
RUNNER = os.path.join(REPO_ROOT, "tests", "integration", "run_tests.py")
CLI = os.path.join(REPO_ROOT, "canasta.py")


def _runner():
    with open(RUNNER) as f:
        return f.read()


class TestHarnessPinsTheCheckout:
    def test_the_env_disables_self_update(self):
        assert 'CANASTA_SELF_UPDATED' in _runner(), (
            "the integration harness must set CANASTA_SELF_UPDATED, or "
            "`canasta upgrade` rewrites the checkout mid-run and later "
            "tests silently run the released CLI"
        )

    def test_every_cli_invocation_shares_that_env(self):
        # Both entry points must go through the one builder; a second
        # hand-rolled env dict is how the pin gets lost again.
        src = _runner()
        assert src.count("env = self.cli_env()") == 2, (
            "run() and run_quiet() must both build their environment via "
            "cli_env(), so the pin cannot be dropped from one of them"
        )
        assert "env[\"CANASTA_CONFIG_DIR\"]" in src
        assert src.count("env[\"CANASTA_CONFIG_DIR\"]") == 1, (
            "the environment should be assembled in exactly one place"
        )


class TestTheGuardStillExists:
    """The pin is worthless if the CLI stops honoring the variable."""

    def test_self_update_honors_the_env_var(self):
        with open(CLI) as f:
            src = f.read()
        assert re.search(
            r'if os\.environ\.get\(\s*["\']CANASTA_SELF_UPDATED["\']\s*\)', src
        ), (
            "self_update_cli() must keep its CANASTA_SELF_UPDATED early "
            "return; the integration harness relies on it to stay on the "
            "code under test"
        )
