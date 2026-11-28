"""Parsing `git diff --name-status` for `gitops status`."""

import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "roles", "common", "module_utils"))

from canasta_gitops_pending import parse_name_status


def test_each_code_reads_as_its_label():
    raw = "A\tnew.txt\nM\tconfig/wikis.yaml\nD\tgone.php\nT\tlink\n"
    assert parse_name_status(raw) == [
        ("new file", "new.txt"),
        ("modified", "config/wikis.yaml"),
        ("deleted", "gone.php"),
        ("typechange", "link"),
    ]


def test_rename_and_copy_report_the_new_path():
    raw = "R100\told.php\tnew.php\nC075\tsrc.yaml\tdst.yaml"
    assert parse_name_status(raw) == [
        ("renamed", "new.php"),
        ("copied", "dst.yaml"),
    ]


def test_an_unknown_code_is_kept_raw():
    assert parse_name_status("X\tweird") == [("X", "weird")]


def test_blank_and_missing_input_is_no_changes():
    assert parse_name_status("") == []
    assert parse_name_status(None) == []
    assert parse_name_status("\n  \n") == []
