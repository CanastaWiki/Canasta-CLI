"""Structural guards for the bundled docker-compose.yml."""

import os
import re
import subprocess

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
COMPOSE_PATH = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "compose", "docker-compose.yml"
)


def _load_compose():
    with open(COMPOSE_PATH) as f:
        return yaml.safe_load(f)


class TestComposeUserRoot:
    def test_web_service_runs_as_root(self):
        services = _load_compose()["services"]
        assert services["web"].get("user") == "root", (
            "web service must declare `user: root` so rootless Podman "
            "doesn't run the canasta entrypoint as a non-root user"
        )

    def test_varnish_service_runs_as_root(self):
        services = _load_compose()["services"]
        assert services["varnish"].get("user") == "root", (
            "varnish service must declare `user: root` so rootless "
            "Podman doesn't lose read access to the bind-mounted VCL"
        )


# NAME=${OTHER_NAME} entries that are deliberate, not typos. The database
# image expects MYSQL_ROOT_PASSWORD, while Canasta stores that password in
# .env as MYSQL_PASSWORD.
CROSS_NAMED_ENV = {
    ("db", "MYSQL_ROOT_PASSWORD"): "MYSQL_PASSWORD",
    # web's client variables carry MediaWiki's own account, not the
    # administrator account .env stores as MYSQL_USER / MYSQL_PASSWORD.
    ("web", "MYSQL_USER"): "WIKI_DB_USER",
    ("web", "MYSQL_PASSWORD"): "WIKI_DB_PASSWORD",
}

_SELF_INTERPOLATED = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)=\$\{([A-Za-z_][A-Za-z0-9_]*)([:\-?].*)?\}$"
)


class TestComposeEnvInterpolation:
    def test_env_entries_interpolate_from_their_own_key(self):
        """A NAME=${OTHER} entry makes `config set NAME` a silent no-op.

        The container gets OTHER's value however NAME is set, so the key
        looks settable — it is accepted, written to .env, and read back by
        `config get` — while never reaching the container.
        """
        offenders = []
        for service, body in _load_compose()["services"].items():
            for entry in body.get("environment") or []:
                if not isinstance(entry, str):
                    continue
                match = _SELF_INTERPOLATED.match(entry)
                if not match:
                    continue
                name, source = match.group(1), match.group(2)
                if name == source:
                    continue
                if CROSS_NAMED_ENV.get((service, name)) == source:
                    continue
                offenders.append("%s: %s" % (service, entry))

        assert not offenders, (
            "compose environment entries must interpolate from the .env key "
            "of the same name, or be listed in CROSS_NAMED_ENV as "
            "deliberate:\n  " + "\n  ".join(offenders)
        )


class TestVarnishWaitsForWeb:
    """Varnish resolves `web` once at VCL load, so it must not start while
    the name resolves only through public DNS (127.0.53.53)."""

    def _run(self, tmp_path, answers):
        script = _load_compose()["services"]["varnish"]["entrypoint"][2]
        started = tmp_path / "started"
        image_entrypoint = tmp_path / "docker-varnish-entrypoint"
        image_entrypoint.write_text('#!/bin/sh\necho "$@" > %s\n' % started)
        script = script.replace("$$", "$").replace(
            "/usr/local/bin/docker-varnish-entrypoint", str(image_entrypoint))
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        calls = tmp_path / "calls"
        calls.write_text("")
        answers_file = tmp_path / "answers"
        answers_file.write_text("".join(a + "\n" for a in answers))
        (bin_dir / "getent").write_text(
            '#!/bin/sh\necho >> %s\nsed -n "$(wc -l < %s)p" %s\n'
            % (calls, calls, answers_file))
        (bin_dir / "sleep").write_text("#!/bin/sh\n")
        for f in (image_entrypoint, bin_dir / "getent", bin_dir / "sleep"):
            f.chmod(0o755)
        env = dict(os.environ, PATH="%s:%s" % (bin_dir, os.environ["PATH"]))
        proc = subprocess.run(["sh", "-c", script, "varnish", "-p", "x=1"],
                              env=env, capture_output=True, text=True)
        return proc, started, len(calls.read_text().splitlines())

    def test_waits_until_web_has_a_real_address(self, tmp_path):
        proc, started, lookups = self._run(
            tmp_path, ["", "127.0.53.53     web  web", "172.18.0.3      web"])
        assert proc.returncode == 0
        assert lookups == 3
        assert started.read_text().strip() == "-p x=1"

    def test_gives_up_after_a_minute_so_the_restart_policy_retries(self, tmp_path):
        proc, started, lookups = self._run(tmp_path, ["127.0.53.53     web  web"] * 61)
        assert proc.returncode == 1
        assert lookups == 60
        assert not started.exists()
