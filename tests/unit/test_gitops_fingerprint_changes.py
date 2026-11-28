"""Which rendered files a `gitops pull` changed, and so whether it restarts.

The baseline is the fingerprint recorded before the pull and the current one
is taken after it. A key the baseline lacks was recorded by a CLI that did
not fingerprint that file yet, so it is not a change, except for .env and
wikis.yaml, which every baseline has: a missing one there means the file did
not exist.
"""

import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))

from canasta_gitops import canasta_fingerprint_changes

SAME = {"env": "e1", "wikis": "w1", "mycnf": "m1", "media_hosts": "h1"}


def test_nothing_changed():
    assert canasta_fingerprint_changes(dict(SAME), dict(SAME)) == []


def test_each_rendered_file_is_reported_by_path():
    current = {"env": "e2", "wikis": "w2", "mycnf": "m2", "media_hosts": "h2"}
    assert canasta_fingerprint_changes(dict(SAME), current) == [
        ".env", "config/wikis.yaml", "my.cnf", "config/media-hosts.yaml"]


def test_a_key_the_baseline_predates_is_not_a_change():
    baseline = {"env": "e1", "wikis": "w1"}
    assert canasta_fingerprint_changes(baseline, dict(SAME)) == []


def test_env_or_wikis_missing_from_the_baseline_means_the_file_is_new():
    assert canasta_fingerprint_changes({}, {"env": "e1", "wikis": "w1"}) == [
        ".env", "config/wikis.yaml"]


def test_per_file_fingerprints_report_added_removed_and_changed_files():
    baseline = dict(SAME, files={"a.php": "1", "b.php": "1", "c.php": "1"})
    current = dict(SAME, files={"a.php": "1", "b.php": "2", "d.php": "1"})
    assert canasta_fingerprint_changes(baseline, current) == [
        "b.php", "c.php", "d.php"]


def test_per_file_fingerprints_are_ignored_when_the_baseline_has_none():
    current = dict(SAME, files={"a.php": "1"})
    assert canasta_fingerprint_changes(dict(SAME), current) == []


def test_missing_fingerprints_are_empty():
    assert canasta_fingerprint_changes(None, None) == []
