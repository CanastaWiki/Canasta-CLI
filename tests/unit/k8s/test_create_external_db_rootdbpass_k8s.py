"""On Kubernetes, MediaWiki uses an external database's administrator account.

The real _envfile.yml runs against a copy of the instance template with
instance_is_k8s set.
"""

from test_create_external_db_rootdbpass import EXTERNAL, FILE_PW, _run


class TestExternalDb:
    def test_kubernetes_uses_the_administrator_account(self, tmp_path):
        facts = _run(tmp_path, EXTERNAL, k8s=True)
        assert facts == {"root": FILE_PW, "wiki": FILE_PW, "user": "admin"}
