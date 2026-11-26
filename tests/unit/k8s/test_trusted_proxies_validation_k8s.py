"""config set's TRUSTED_PROXIES check on a Kubernetes instance, where the
CDN-behind-proxies form is refused and a bare CDN name is accepted."""

from test_trusted_proxies_validation import _validate


def test_the_cdn_behind_proxies_form_is_refused_on_kubernetes(tmp_path):
    code, out = _validate(tmp_path, "imperva@203.0.113.10/32", "kubernetes")
    assert code != 0 and "Docker Compose only" in out, out
    code, out = _validate(tmp_path, "imperva", "kubernetes")
    assert code == 0, out
