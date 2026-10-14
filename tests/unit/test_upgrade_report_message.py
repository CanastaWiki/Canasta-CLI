"""The upgrade summary prints on one line.

A folded scalar keeps the line break after a more-indented line, so a
Jinja expression continued on an indented line split "Upgraded N" from
"instance(s)." in the output.
"""

import os
import re

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
UPGRADE = os.path.join(REPO_ROOT, "playbooks", "upgrade.yml")


def _find(items, name):
    for t in items or []:
        if not isinstance(t, dict):
            continue
        if t.get("name") == name:
            return t
        for key in ("block", "rescue", "always"):
            found = _find(t.get(key), name)
            if found:
                return found
    return None


def test_upgrade_complete_message_is_one_line():
    with open(UPGRADE) as f:
        task = _find(yaml.safe_load(f), "Report upgrade complete")
    msg = task["ansible.builtin.debug"]["msg"]
    assert "\n" not in msg, repr(msg)
    text = re.sub(r"\{%.*?%\}.*?\{%\s*endif\s*%\}", "", msg, flags=re.S)
    text = re.sub(r"\{\{.*?\}\}", "N", text, flags=re.S)
    assert text == "Upgraded N instance(s).", repr(msg)
