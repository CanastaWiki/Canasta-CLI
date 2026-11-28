# -*- coding: utf-8 -*-
"""Parsing of `git diff --name-status` for 'gitops status'."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

# git diff --name-status codes -> human labels (git's own vocabulary), so a
# staged/unstaged deletion doesn't read as content being pushed.
CHANGE_LABELS = {
    "A": "new file", "M": "modified", "D": "deleted",
    "R": "renamed", "C": "copied", "T": "typechange",
}


def parse_name_status(raw):
    """Parse `git diff --name-status` output into (label, path) pairs.

    Each line is `<CODE>\\t<path>` (rename/copy is `<CODE>\\t<old>\\t<path>` —
    take the last field for the current path). Unknown codes fall back to the
    raw code so nothing is silently dropped."""
    changes = []
    for ln in (raw or "").split("\n"):
        if not ln.strip():
            continue
        fields = ln.split("\t")
        code = fields[0][:1]
        path = fields[-1]
        changes.append((CHANGE_LABELS.get(code, fields[0]), path))
    return changes
