#!/usr/bin/env python3
"""Every integration test must be run by CI, or be listed as deliberately not.

A test that no workflow names cannot fail. `gitops-fix-submodules` sat in
the registry for four months after the behavior it asserted changed
underneath it: the suite was never green, but nothing reported that,
because no job ran it. This makes the omission visible at validate time
instead of the next time someone runs the whole suite by hand.

Exemptions are declared below with a reason. The point is not to force
every test into CI — some are too slow or need hardware CI does not have
— but to make "not run" a decision someone wrote down.
"""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_TESTS = os.path.join(REPO_ROOT, "tests", "integration", "run_tests.py")
WORKFLOWS = os.path.join(REPO_ROOT, ".github", "workflows")

# name -> why CI does not run it. Keep the reason specific: "slow" is a
# fact about today's runner, "needs a second host" is a fact about the test.
NOT_IN_CI = {
    "upgrade-mysql-to-mariadb":
        "drives a full MySQL->MariaDB migration; too slow for a PR runner",
    "upgrade-mysql-to-mariadb-recovery":
        "same migration, plus a failure injection and recovery pass",
}


def registered_tests():
    """Names from the ALL_TESTS mapping in run_tests.py."""
    with open(RUN_TESTS) as f:
        source = f.read()
    start = source.index("ALL_TESTS = {")
    end = source.index("}", start)
    return set(re.findall(r'"([a-z0-9-]+)":', source[start:end]))


def tests_named_by_ci():
    """Every test name any workflow mentions."""
    named = set()
    for entry in os.listdir(WORKFLOWS):
        if not entry.endswith((".yml", ".yaml")):
            continue
        with open(os.path.join(WORKFLOWS, entry)) as f:
            text = f.read()
        # run_tests.py invocations, whether inline or via a bash array.
        for match in re.finditer(
                r"run_tests\.py([^\n|]*(?:\n\s+[a-z0-9][a-z0-9 -]*)*)", text):
            named.update(re.findall(r"[a-z][a-z0-9-]{2,}", match.group(1)))
        for match in re.finditer(r"tests=\(([^)]*)\)", text):
            named.update(re.findall(r"[a-z][a-z0-9-]{2,}", match.group(1)))
    return named


def main():
    registered = registered_tests()
    if not registered:
        print("could not read ALL_TESTS from run_tests.py", file=sys.stderr)
        return 1
    covered = tests_named_by_ci()
    missing = sorted(registered - covered - set(NOT_IN_CI))
    stale_exemptions = sorted(set(NOT_IN_CI) - registered)

    errors = []
    if missing:
        errors.append(
            "These integration tests are registered but no workflow runs "
            "them, so they cannot fail:\n  "
            + "\n  ".join(missing)
            + "\n\nAdd each to a job in .github/workflows/, or to NOT_IN_CI "
              "in this script with the reason.")
    if stale_exemptions:
        errors.append(
            "These NOT_IN_CI entries name tests that no longer exist:\n  "
            + "\n  ".join(stale_exemptions))

    if errors:
        print("\n\n".join(errors), file=sys.stderr)
        return 1
    print("CI test coverage: %d registered, %d exempt, all accounted for"
          % (len(registered), len(NOT_IN_CI)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
