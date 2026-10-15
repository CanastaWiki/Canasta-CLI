"""gitops status on Kubernetes reports gitignored inputs that the next
'gitops push' renders into the deployed values (config/wikis.yaml after
'backup databases add', .env after 'config set', ...)."""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from direct_commands import gitops  # noqa: E402

ARGOCD = ("Synced", "Healthy", "2026-10-14T10:00:00Z", "abcdef1")
HEADING = "Changes the next push will render"

WIKIS = """wikis:
- id: main
  url: wiki.example.com
  name: Main
"""
ENV = "MW_SITE_FQDN=wiki.example.com\nMYSQL_PASSWORD=s3cret\nMW_ENABLE_JOB_RUNNER=true\n"


def _write(root, rel, content):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _render(root):
    """The configData a push renders from the instance's current files."""
    env = "\n".join(
        ln for ln in (root / ".env").read_text().splitlines()
        if not ln.startswith("MYSQL_PASSWORD")) + "\n"
    web = {".env": env, "wikis.yaml": (root / "config/wikis.yaml").read_text()}
    settings = root / "config/settings"
    for f in sorted(settings.rglob("*")):
        if f.is_file() and f.name != "README" and not f.name.startswith("."):
            rel = str(f.relative_to(settings)).replace("/", "--")
            web["settings--" + rel] = f.read_text()
    return {
        "configData": {
            "web": web,
            "caddy": {"Caddyfile": (root / "config/Caddyfile").read_text()},
            "varnish": {"default.vcl": (root / "config/default.vcl").read_text()},
        },
        "appSecretEnv": [],
    }


def _instance(tmp_path, multi_env=True):
    root = tmp_path / "inst"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _write(root, ".gitignore",
           ".env\nconfig/wikis.yaml\nconfig/Caddyfile\nconfig/default.vcl\n"
           "config/secrets-web\nconfig/settings/global/*.local.php\n")
    _write(root, ".gitops-host", "h1\n")
    _write(root, ".env", ENV)
    _write(root, "config/wikis.yaml", WIKIS)
    _write(root, "config/Caddyfile", "wiki.example.com {\n}\n")
    _write(root, "config/default.vcl", "vcl 4.1;\n")
    _write(root, "config/settings/global/Main.php", "<?php\n")
    _write(root, "config/settings/global/README", "readme\n")
    _rerender(root, multi_env)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
         "-c", "commit.gpgsign=false", "commit", "-qm", "init"], check=True)
    return root


def _rerender(root, multi_env=True):
    rendered = yaml.safe_dump(_render(root))
    if multi_env:
        _write(root, "values.template.yaml", rendered)
        _write(root, "hosts/h1/rendered-values.yaml", rendered)
    else:
        _write(root, "values.yaml", rendered)


def _status(root):
    script = gitops._gitops_status_script(str(root), k8s=True)
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(root.parent))
    out = subprocess.run(["bash", "-c", script], capture_output=True,
                         text=True, timeout=30, env=env).stdout
    return gitops._parse_gitops_status_k8s(out, "mysite", ARGOCD)


def _pending(result):
    lines = result.split("\n")
    if not any(ln.startswith(HEADING) for ln in lines):
        return []
    start = next(i for i, ln in enumerate(lines) if ln.startswith(HEADING))
    out = []
    for ln in lines[start + 1:]:
        if not ln.startswith("  ") or ln.startswith("  'canasta"):
            break
        out.append(ln.strip())
    return out


class TestPendingRender:
    def test_clean_instance_reports_nothing_to_render(self, tmp_path):
        root = _instance(tmp_path)
        assert HEADING not in _status(root)

    def test_backup_database_added_to_wikis_yaml(self, tmp_path):
        root = _instance(tmp_path)
        _write(root, "config/wikis.yaml",
               WIKIS + "  extra_databases:\n  - main_cargo\n")
        result = _status(root)
        assert _pending(result) == ["config/wikis.yaml"]
        assert "canasta gitops push" in result

    def test_render_clears_the_report(self, tmp_path):
        root = _instance(tmp_path)
        _write(root, "config/wikis.yaml",
               WIKIS + "  extra_databases:\n  - main_cargo\n")
        _rerender(root)
        assert HEADING not in _status(root)

    def test_non_secret_env_change(self, tmp_path):
        root = _instance(tmp_path)
        _write(root, ".env", ENV.replace("JOB_RUNNER=true", "JOB_RUNNER=false"))
        assert _pending(_status(root)) == [".env"]

    def test_secret_env_change_is_not_rendered(self, tmp_path):
        # Secrets are stripped from the rendered .env, so a push commits
        # nothing for them.
        root = _instance(tmp_path)
        _write(root, ".env", ENV.replace("s3cret", "other"))
        assert HEADING not in _status(root)

    def test_gitignored_settings_file(self, tmp_path):
        root = _instance(tmp_path)
        _write(root, "config/settings/global/Host.local.php", "<?php\n")
        assert _pending(_status(root)) == [
            "config/settings/global/Host.local.php"]

    def test_file_git_already_lists_is_not_repeated(self, tmp_path):
        root = _instance(tmp_path)
        _write(root, "config/settings/global/New.php", "<?php\n")
        result = _status(root)
        assert "config/settings/global/New.php" in result
        assert HEADING not in result

    def test_removed_settings_file(self, tmp_path):
        root = _instance(tmp_path)
        _write(root, "config/settings/global/Host.local.php", "<?php\n")
        _rerender(root)
        (root / "config/settings/global/Host.local.php").unlink()
        assert _pending(_status(root)) == [
            "config/settings/global/Host.local.php"]

    def test_web_secret_name_added(self, tmp_path):
        root = _instance(tmp_path)
        _write(root, "config/secrets-web", "# names\nAPI_TOKEN\n")
        assert _pending(_status(root)) == ["config/secrets-web"]

    def test_single_env_compares_values_yaml(self, tmp_path):
        root = _instance(tmp_path, multi_env=False)
        assert HEADING not in _status(root)
        _write(root, "config/default.vcl", "vcl 4.1;\n# edited\n")
        assert _pending(_status(root)) == ["config/default.vcl"]

    def test_no_values_file_reports_nothing(self, tmp_path):
        root = _instance(tmp_path)
        (root / "hosts/h1/rendered-values.yaml").unlink()
        _write(root, "config/wikis.yaml", WIKIS + "# edit\n")
        assert HEADING not in _status(root)


def test_compose_script_has_no_render_section():
    script = gitops._gitops_status_script("/srv/mysite")
    assert "@basis" not in script
    assert "@basis" in gitops._gitops_status_script("/srv/mysite", k8s=True)


def test_cmd_gitops_status_requests_render_inputs_on_k8s(monkeypatch):
    seen = []
    monkeypatch.setattr(gitops._helpers, "_resolve_instance",
                        lambda args: ("mysite", {
                            "path": "/srv/mysite",
                            "orchestrator": "kubernetes",
                            "host": "admin@k8s-control",
                        }))

    def fake_ssh(host, cmd):
        seen.append(cmd)
        return (0, "h1\n")
    monkeypatch.setattr(gitops._helpers, "_ssh_run", fake_ssh)
    monkeypatch.setattr(gitops, "_gitops_argocd_status",
                        lambda inst_id, host: ARGOCD)
    args = type("Args", (), {"id": "mysite"})()
    assert gitops.cmd_gitops_status(args) == 0
    assert any("@basis" in cmd for cmd in seen)


def test_env_secret_regex_matches_the_ansible_definition():
    import jinja2

    with open(os.path.join(REPO_ROOT, "vars", "secret_classification.yml")) as f:
        data = yaml.safe_load(f)
    rendered = jinja2.Template(data["canasta_secret_key_regex"]).render(**data)
    assert gitops._env_line_secret_re().pattern == rendered
