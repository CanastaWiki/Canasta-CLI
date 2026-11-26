"""Database administration on Kubernetes runs the client with kubectl exec
in the database pod, or in the web pod for an external database."""

import pytest

from test_db_admin_args import _db_admin


@pytest.mark.parametrize("env_text, target", [
    ("MYSQL_PASSWORD=pw\n", "statefulset/canasta-inst-db -c db"),
    ("USE_EXTERNAL_DB=true\nMYSQL_HOST=db.example.com\n",
     "deployment/canasta-inst-web -c web"),
])
def test_kubernetes_runs_in_the_pods(tmp_path, env_text, target):
    result = _db_admin(tmp_path, env_text, instance_is_k8s=True,
                       instance_id="inst")
    assert " ".join(result["exec"].split()) == (
        "kubectl exec -i -n canasta-inst %s --" % target)
    # The pod's own environment supplies the password, as on Compose.
    assert result["service"] == ("web" if "EXTERNAL" in env_text else "db")
