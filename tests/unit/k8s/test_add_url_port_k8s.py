"""`canasta add` on a Kubernetes instance records the new wiki's domain in
values.yaml without the port that wikis.yaml carries."""

from test_add_url_port import ADD


def test_k8s_values_domains_never_carry_a_port():
    with open(ADD) as f:
        text = f.read()
    assert "union([_add_domain | trim | regex_replace(':[0-9]+$', '')])" in text
