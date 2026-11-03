"""Staged-change checks must not run git-crypt's textconv.

Encrypted paths carry `diff=git-crypt`, so a plain `git diff --cached
--quiet` compares both sides through `git-crypt diff`. git-crypt 0.7.0
converts both to nothing, the check sees no change, and a push of a
change to an encrypted file reports "No changes to push".
"""

import os
import re

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CHECK = re.compile(r"git diff --cached --quiet\b[^\n\"']*")


def _staged_checks():
    for top in ("roles", "playbooks"):
        for dirpath, _, files in os.walk(os.path.join(REPO_ROOT, top)):
            for name in files:
                if not name.endswith((".yml", ".yaml")):
                    continue
                path = os.path.join(dirpath, name)
                with open(path) as f:
                    for match in CHECK.finditer(f.read()):
                        yield os.path.relpath(path, REPO_ROOT), match.group(0)


def test_checks_exist():
    assert len(list(_staged_checks())) >= 4


def test_every_check_skips_textconv():
    missing = [(path, cmd) for path, cmd in _staged_checks()
               if "--no-textconv" not in cmd]
    assert missing == []
