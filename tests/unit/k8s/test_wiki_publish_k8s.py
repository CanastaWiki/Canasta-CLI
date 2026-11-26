"""The generated command reference labels a Kubernetes-only flag
'Kubernetes' in its Orchestrator column."""

from test_wiki_publish import wp


class TestOrchestratorColumn:
    def _create_page(self):
        data = wp.load_definitions()
        cmd = next(c for c in data["commands"] if c["name"] == "create")
        return wp.gen_wikitext(cmd, global_flags=data["global_flags"])

    def test_kubernetes_only_param_labelled_kubernetes(self):
        page = self._create_page()
        # --storage-class has orchestrator_only: kubernetes. Its row
        # must include the 'Kubernetes' label.
        for line in page.splitlines():
            if "<code>--storage-class</code>" in line:
                assert "Kubernetes" in line
                return
        raise AssertionError("--storage-class row not found")
