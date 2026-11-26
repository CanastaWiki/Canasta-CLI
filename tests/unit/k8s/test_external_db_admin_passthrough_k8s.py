"""Database administration on an external database of a Kubernetes instance
runs in the pod as the pod's own account."""

from test_external_db_admin_passthrough import (
    DB_ADMIN, EXTERNAL_ENV, SAVE, _instance, _play)


class TestDbAdmin:
    def test_external_kubernetes_uses_the_pod_account(self, tmp_path):
        inst, fake = _instance(tmp_path, EXTERNAL_ENV)
        out, _ = _play(tmp_path, inst, fake, [DB_ADMIN, SAVE], k8s=True)
        assert out["pwd"] == 'MYSQL_PWD="$MYSQL_PASSWORD"'
        assert '-u "${MYSQL_USER:-root}"' in out["args"]
        assert out["dotenv"] == {}
        assert out["exec"].startswith("kubectl exec")
