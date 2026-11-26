"""MW_SITEMAP_URL_PATH on Kubernetes: the key reaches the web pods through the
pod env allowlist, the Caddyfile rewrites each wiki's sitemap path onto the
images path and warns when a rewrite is left out for overlapping wikis, and
on a gitops instance `sitemap generate --identifier` only stages the
identifier."""

import os

import pytest

from test_sitemap_url_path import (
    KEY,
    PATH_FARM,
    ROLES,
    _caddyfile,
    _generate,
    _load,
)


K8S_SYNC = os.path.join(ROLES, "orchestrator", "tasks", "k8s_sync_config.yml")


class TestSetting:
    def test_kubernetes_passes_it_to_the_web_pods(self):
        allow = next(t["ansible.builtin.set_fact"]["_k8s_pod_env_allowlist"]
                     for t in _load(K8S_SYNC)
                     if "_k8s_pod_env_allowlist" in t.get("ansible.builtin.set_fact", {}))
        assert KEY in allow


class TestCaddy:
    @pytest.mark.parametrize("k8s", [True])
    def test_each_wiki_is_rewritten_onto_the_images_path(self, tmp_path, k8s):
        code, out, path = _caddyfile(tmp_path, ["MW_SITEMAP_URL_PATH=/wiki/sitemap"], k8s=k8s)
        assert code == 0, out
        text = path.read_text()
        assert ("    @sitemap1 {\n"
                "        host example.com\n"
                "        path /en/wiki/sitemap/*\n"
                "    }\n"
                "    uri @sitemap1 path_regexp ^/en/wiki/sitemap/ /en/public_assets/sitemap/\n"
                ) in text
        assert ("        host docs.example.org\n"
                "        path /wiki/sitemap/*\n") in text
        assert "uri @sitemap3 path_regexp ^/wiki/sitemap/ /public_assets/sitemap/\n" in text

    @pytest.mark.parametrize("k8s", [True])
    def test_an_overlapping_wikis_rewrite_is_left_out(self, tmp_path, k8s):
        code, out, path = _caddyfile(tmp_path, ["MW_SITEMAP_URL_PATH=/docs/sitemap"],
                                     wikis=PATH_FARM, k8s=k8s)
        assert code == 0, out
        text = path.read_text()
        assert "path /docs/docs/sitemap/*" in text
        assert "path /docs/sitemap/*" not in text
        warning = ("Warning: MW_SITEMAP_URL_PATH=/docs/sitemap: the sitemaps of wiki "
                   "'main' are not served at example.com/docs/sitemap, which is inside "
                   "the URL of wiki 'docs' (example.com/docs).")
        # On Compose the edge render reports it.
        assert (warning in out) == k8s, out


class TestGenerate:
    def test_a_gitops_kubernetes_instance_only_stages_the_identifier(self, tmp_path):
        code, out, _, log = _generate(tmp_path, {"wiki": "en", "identifier": "mediawiki"},
                                      k8s=True, gitops=True)
        assert code == 0, out
        assert log == ["captured", "check_running"]
        assert "staged in its gitops repo but not deployed" in out
