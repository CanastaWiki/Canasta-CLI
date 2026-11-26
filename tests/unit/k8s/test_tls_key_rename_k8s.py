"""The TLS key rename migration on a Kubernetes instance drops
CADDY_AUTO_HTTPS=off rather than turning it into TLS_MODE=off."""

import os

from test_tls_key_rename import ROOT, _playbook


def test_the_migration_drops_plain_http_on_kubernetes(tmp_path):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text("CADDY_AUTO_HTTPS=off\nCADDY_TRUSTED_PROXIES=cloudflare\n")
    proc = _playbook(tmp_path, [{"ansible.builtin.include_tasks": os.path.join(
        ROOT, "roles", "upgrade", "tasks", "migrations", "rename_tls_keys.yml")}],
        {"instance_path": str(inst), "instance_is_k8s": True})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (inst / ".env").read_text() == "TRUSTED_PROXIES=cloudflare\n"
