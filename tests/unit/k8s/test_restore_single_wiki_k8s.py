"""Structural guards for single-wiki restore (`canasta backup restore -w`) on
Kubernetes: restic is scoped to the target wiki's paths with include filters,
the host copies of shared state are gated off, the shared .env is not
rewritten, the wiki's own settings and public assets are restored, and a
wiki absent from the snapshot fails."""

import os

from test_restore_single_wiki import (
    REPO_ROOT,
    _by_name,
    _env_password_preserves,
    _when,
)


K8S = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_restore_instance.yml")


def test_k8s_builds_include_filters_for_single_wiki():
    inc = _by_name(K8S, "Build restic include filters for single-wiki restore")
    assert inc is not None and "wiki is defined" in _when(inc)
    val = inc["ansible.builtin.set_fact"]["_restore_include"]
    for path in ("config/settings/wikis/", "config/backup/db_",
                 "images/", "public_assets/"):
        assert path in val, "include filter must cover %s" % path


def test_k8s_restic_restore_uses_the_include_filter():
    with open(K8S) as f:
        text = f.read()
    assert "restore {{ _resolved_snapshot }}{{ _restore_include }}" in text, (
        "restic restore must apply the single-wiki include filter")


def test_k8s_full_host_copies_are_gated_off_for_single_wiki():
    for name in ("Copy restored config to host",
                 "Copy restored .env to host",
                 "Restore extensions/skins/public_assets to host"):
        t = _by_name(K8S, name)
        assert t is not None, "missing task: %s" % name
        assert "wiki is not defined" in _when(t), (
            "%s must be gated off for single-wiki restore" % name)


def test_k8s_has_scoped_single_wiki_restores():
    settings = _by_name(K8S, "Copy restored single wiki settings to host")
    assert settings is not None and "wiki is defined" in _when(settings)
    assets = _by_name(K8S, "Restore single wiki public assets to host")
    assert assets is not None and "wiki is defined" in _when(assets)


def test_k8s_fails_when_wiki_absent_from_snapshot():
    fail = _by_name(K8S, "Fail when the target wiki is absent from the snapshot")
    assert fail is not None, (
        "a wiki missing from the snapshot must fail, not silently no-op")


def test_k8s_env_password_preserve_gated_off_for_single_wiki():
    preserves = _env_password_preserves("Restore (Kubernetes)")
    assert len(preserves) == 1
    assert all("wiki is not defined" in _when(t) for t in preserves), (
        "single-wiki restore must not rewrite the shared .env")
