"""Every update.php run the CLI starts passes --nopurge: the purge empties
the objectcache table, which holds the sessions."""

import os
import re

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SOURCES = ["roles", "playbooks", "direct_commands"]


def _update_php_commands():
    found = []
    for top in SOURCES:
        for root, _dirs, files in os.walk(os.path.join(REPO_ROOT, top)):
            for name in files:
                if not name.endswith((".yml", ".yaml", ".py")):
                    continue
                path = os.path.join(root, name)
                with open(path) as f:
                    for line in f:
                        if re.search(r"php maintenance/update\.php", line):
                            found.append((os.path.relpath(path, REPO_ROOT), line.strip()))
    return found


def test_update_php_runs_are_found():
    assert len(_update_php_commands()) >= 3


def test_every_update_php_run_passes_nopurge():
    missing = [(p, l) for p, l in _update_php_commands() if "--nopurge" not in l]
    assert not missing, missing
