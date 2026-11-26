"""Kubernetes instances take no part in wiki alias hostnames.

doctor stays quiet about Caddyfile.global blocks on Kubernetes, and the
Helm deploy refuses wikis that list aliases.
"""

import yaml

from test_edge_aliases import WIKIS, _instance, _lines, _task, g


def test_doctor_is_quiet_on_kubernetes(tmp_path):
    overlap = _instance(tmp_path / "c", WIKIS, "www.example.com {\n}\n")
    assert _lines(overlap, "k8s") == []


def test_wikis_listing_aliases_are_found_for_the_kubernetes_refusal():
    doc = yaml.safe_dump({"wikis": [
        {"id": "a", "url": "a.example.com", "aliases": ["www.a.example.com"]},
        {"id": "b", "url": "b.example.com", "aliases": ["", None]},
        {"id": "c", "url": "c.example.com"}]})
    assert g.canasta_wikis_with_aliases(doc) == ["a"]
    task = _task("orchestrator/tasks/helm_deploy.yml", "Refuse wiki aliases on Kubernetes")
    assert "canasta_wikis_with_aliases" in task["vars"]["_hd_alias_wikis"]
