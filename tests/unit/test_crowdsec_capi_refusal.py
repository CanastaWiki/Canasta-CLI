"""A Central API (CAPI) refusal must not take CrowdSec's local API down.

The engine will not start while CAPI refuses its credentials. The start
wrapper the crowdsec container (Compose) and sidecar (K8s) both run keeps
the refused credentials aside, runs the engine with the local API only,
and puts the credentials back once CAPI accepts them. The wrapper is run
here under /bin/sh with stub `crowdsec` entrypoint and `cscli` executables,
and ensure_capi.yml is run against a stub cscli.
"""

import os
import re
import shutil
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROLES = os.path.join(REPO_ROOT, "roles")
CHART = os.path.join(ROLES, "orchestrator", "files", "helm", "canasta")
WRAPPER = os.path.join(CHART, "files", "crowdsec-start.sh")
COMPOSE = os.path.join(ROLES, "orchestrator", "files", "compose",
                       "docker-compose.yml")
CREDS = "/etc/crowdsec/online_api_credentials.yaml"
START = "/bin/bash /docker_start.sh"

# Stands in for the image's entrypoint: a missing credentials file is a
# first start whose CAPI registration failed, and credentials CAPI refuses
# (or cannot be reached for) stop the engine. Otherwise it runs and exits 0.
FAKE_ENGINE = """#!/bin/sh
creds="$1"
echo "run:$(cat "$creds" 2>/dev/null)" >> "$(dirname "$0")/calls.log"
if [ ! -e "$creds" ]; then : > "$creds"; exit 1; fi
grep -qE 'login: (refused|flaky)' "$creds" && exit 1
exit 0
"""

FAKE_CSCLI = """#!/bin/sh
echo "cscli:$*" >> "$(dirname "$0")/calls.log"
[ "$*" = "capi status" ] || exit 1
creds="$(cat "$(dirname "$0")/creds_path")"
if grep -q 'login: good' "$creds"; then
  echo "You can successfully interact with Central API (CAPI)"; exit 0
fi
if grep -q 'login: flaky' "$creds"; then
  echo "Error: failed to authenticate to Central API (CAPI): could not get jwt token: dial tcp: i/o timeout"
  exit 1
fi
echo "Error: failed to authenticate to Central API (CAPI): API error: Forbidden"
exit 1
"""


def _wrapper():
    with open(WRAPPER) as f:
        return f.read()


def _run_wrapper(tmp_path, creds=None, refused=None):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("engine", FAKE_ENGINE), ("cscli", FAKE_CSCLI)):
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    creds_path = tmp_path / "online_api_credentials.yaml"
    (bindir / "creds_path").write_text(str(creds_path))
    if creds is not None:
        creds_path.write_text(creds)
    if refused is not None:
        (tmp_path / "online_api_credentials.yaml.refused").write_text(refused)
    script = _wrapper()
    assert CREDS in script and START in script and "sleep 600" in script
    script = (script.replace(CREDS, str(creds_path))
              .replace(START, "/bin/sh %s %s" % (bindir / "engine", creds_path))
              .replace("sleep 600", "sleep 0"))
    env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]))
    proc = subprocess.run(["/bin/sh", "-c", script], capture_output=True,
                          text=True, env=env, timeout=30)
    calls = (bindir / "calls.log").read_text().splitlines()
    kept = tmp_path / "online_api_credentials.yaml.refused"
    return (proc, calls, creds_path.read_text() if creds_path.exists() else None,
            kept.read_text() if kept.exists() else None)


def _restored(tmp_path):
    return (tmp_path / "online_api_credentials.yaml.restored").exists()


class TestStartWrapper:
    def test_refused_credentials_are_kept_and_the_engine_runs_local_only(
            self, tmp_path):
        proc, calls, creds, kept = _run_wrapper(tmp_path,
                                                creds="login: refused\n")
        assert proc.returncode == 0, proc.stderr
        assert kept == "login: refused\n"
        assert creds == ""
        assert calls == ["run:login: refused", "cscli:capi status", "run:"]
        assert "community blocklist is unavailable" in proc.stderr
        assert "waiting 10 minutes" not in proc.stderr

    def test_accepted_credentials_start_normally(self, tmp_path):
        proc, calls, creds, kept = _run_wrapper(tmp_path, creds="login: good\n")
        assert proc.returncode == 0, proc.stderr
        assert calls == ["run:login: good"]
        assert creds == "login: good\n"
        assert kept is None
        assert proc.stderr == ""

    def test_kept_credentials_are_restored_once_capi_accepts_them(
            self, tmp_path):
        proc, calls, creds, kept = _run_wrapper(tmp_path, creds="",
                                                refused="login: good\n")
        assert proc.returncode == 0, proc.stderr
        assert creds == "login: good\n"
        assert kept is None
        assert calls == ["cscli:capi status", "run:login: good"]
        assert "community blocklist is restored" in proc.stderr
        assert _restored(tmp_path)

    def test_still_refused_credentials_stay_aside(self, tmp_path):
        proc, calls, creds, kept = _run_wrapper(tmp_path, creds="",
                                                refused="login: refused\n")
        assert proc.returncode == 0, proc.stderr
        assert creds == ""
        assert kept == "login: refused\n"
        assert calls == ["cscli:capi status", "run:"]
        assert not any("register" in c for c in calls)
        assert "community blocklist is unavailable" in proc.stderr
        assert not _restored(tmp_path)

    def test_unreachable_capi_is_not_a_refusal(self, tmp_path):
        proc, calls, creds, kept = _run_wrapper(tmp_path, creds="login: flaky\n")
        assert proc.returncode == 1
        assert creds == "login: flaky\n"
        assert kept is None
        assert calls == ["run:login: flaky", "cscli:capi status"]
        assert "waiting 10 minutes" in proc.stderr

    def test_an_existing_kept_copy_is_not_overwritten(self, tmp_path):
        proc, _, creds, kept = _run_wrapper(tmp_path, creds="login: refused\n",
                                            refused="")
        assert proc.returncode == 0, proc.stderr
        assert creds == ""
        assert kept == ""

    def test_failed_first_registration_retries_local_only(self, tmp_path):
        proc, calls, creds, kept = _run_wrapper(tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert calls == ["run:", "run:"]
        assert creds == ""
        assert kept is None
        assert "could not register with the Central API" in proc.stderr


class TestWrapperIsShared:
    def test_compose_runs_the_chart_wrapper(self):
        with open(COMPOSE) as f:
            svc = yaml.safe_load(f)["services"]["crowdsec"]
        assert svc["entrypoint"][:2] == ["/bin/sh", "-c"]
        assert svc["entrypoint"][2].replace("$$", "$") == _wrapper()

    def test_chart_sidecar_runs_the_wrapper(self):
        with open(os.path.join(CHART, "templates",
                               "deployment-caddy.yaml")) as f:
            template = f.read()
        sidecar = template[template.index("- name: crowdsec\n"):]
        assert re.search(
            r'command:\s*- /bin/sh\s*- -c\s*'
            r'- \{\{ \.Files\.Get "files/crowdsec-start\.sh" \| toJson \}\}',
            sidecar[:sidecar.index("env:")])

    def test_chart_wrapper_has_no_compose_escaping(self):
        assert "$$" not in _wrapper()


# --- ensure_capi.yml ---------------------------------------------------------

FAKE_PREFIX = """#!/bin/sh
d="$(dirname "$0")"
echo "$*" >> "$d/prefix.log"
case "$*" in
  "cscli lapi status") exit 0 ;;
  "test -e /etc/crowdsec/online_api_credentials.yaml.refused")
    [ -f "$d/refused" ] ;;
  "cscli capi status") [ -f "$d/capi_ok" ] ;;
  "rm /etc/crowdsec/online_api_credentials.yaml.restored")
    rm "$d/restored" 2>/dev/null ;;
  "grep -qs ^login: /etc/crowdsec/online_api_credentials.yaml")
    [ -f "$d/creds" ] ;;
  "sh -c "*) exit 0 ;;
  *) exit 1 ;;
esac
"""


def _run_ensure_capi(tmp_path, refused=False, capi_ok=False, restored=False,
                     creds=True, runs=1):
    roles = tmp_path / "roles"
    cs = roles / "crowdsec" / "tasks"
    cs.mkdir(parents=True)
    shutil.copy(os.path.join(ROLES, "crowdsec", "tasks", "ensure_capi.yml"),
                cs / "ensure_capi.yml")
    prefix = tmp_path / "prefix"
    prefix.write_text(FAKE_PREFIX)
    prefix.chmod(0o755)
    orch = roles / "orchestrator" / "tasks"
    orch.mkdir(parents=True)
    (orch / "crowdsec_cscli_prefix.yml").write_text(
        "- ansible.builtin.set_fact:\n    _cscli_prefix: %s\n" % prefix)
    (orch / "crowdsec_restart_engine.yml").write_text(
        "- ansible.builtin.debug:\n    msg: engine restarted\n")
    if refused:
        (tmp_path / "refused").touch()
    if capi_ok:
        (tmp_path / "capi_ok").touch()
    if restored:
        (tmp_path / "restored").touch()
    if creds:
        (tmp_path / "creds").touch()
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"canasta_root": str(tmp_path),
                 "instance_path": str(tmp_path / "instance"),
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_role": {
            "name": "crowdsec", "tasks_from": "ensure_capi.yml"}}] * runs,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_ROLES_PATH=str(roles))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    return proc, (tmp_path / "prefix.log").read_text().splitlines()


class TestEnsureCapi:
    def test_local_only_engine_is_not_reregistered(self, tmp_path):
        proc, calls = _run_ensure_capi(tmp_path, refused=True)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert not any("capi" in c for c in calls), calls
        assert "community blocklist is" in out
        assert "unavailable" in out
        assert "API error" not in out

    def test_restart_within_one_command_warns_once(self, tmp_path):
        # Bouncer enrollment restarts the instance, which runs this file again.
        proc, _ = _run_ensure_capi(tmp_path, refused=True, runs=2)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert out.count("runs with its local API only") == 1, out

    def test_each_command_warns(self, tmp_path):
        first, _ = _run_ensure_capi(tmp_path, refused=True)
        shutil.rmtree(tmp_path / "roles")
        second, _ = _run_ensure_capi(tmp_path, refused=True)
        assert "runs with its local API only" in first.stdout
        assert "runs with its local API only" in second.stdout

    def test_restored_credentials_are_reported_once(self, tmp_path):
        proc, calls = _run_ensure_capi(tmp_path, capi_ok=True, restored=True,
                                       runs=2)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert out.count("community blocklist is restored") == 1, out
        assert not (tmp_path / "restored").exists()

    def test_restored_credentials_are_not_checked_again(self, tmp_path):
        proc, calls = _run_ensure_capi(tmp_path, restored=True)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "cscli capi status" not in calls, calls
        assert not any(c.startswith("sh -c") for c in calls), calls
        assert "community blocklist is restored" in out
        assert "next start checks it again" not in out

    def test_unanswered_registered_engine_keeps_its_registration(
            self, tmp_path):
        proc, calls = _run_ensure_capi(tmp_path)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "cscli capi status" in calls, calls
        assert not any(c.startswith("sh -c") for c in calls), calls
        assert out.count("next start checks it again") == 1, out
        assert "online_api_credentials.yaml.refused" in out

    def test_unregistered_engine_registers(self, tmp_path):
        proc, calls = _run_ensure_capi(tmp_path, creds=False)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert any(c.startswith("sh -c cscli capi register") for c in calls), calls
        assert "engine restarted" in out
        assert "next start checks it again" not in out

    def test_registered_engine_is_left_alone(self, tmp_path):
        proc, calls = _run_ensure_capi(tmp_path, capi_ok=True)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert calls[-1] == "cscli capi status"
        assert not any(c.startswith("sh -c") for c in calls), calls
        assert "local API only" not in out
        assert "community blocklist is restored" not in out


def test_bouncer_enroll_reports_a_dead_lapi_without_raw_cscli_output():
    with open(os.path.join(ROLES, "crowdsec", "tasks",
                           "bouncer_enroll.yml")) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name", "") for t in tasks]
    wait = tasks[names.index("Wait for the CrowdSec LAPI to be ready")]
    assert wait.get("failed_when") is False
    fail = tasks[names.index("Fail when the CrowdSec LAPI did not come up")]
    assert "_crowdsec_lapi" not in fail["ansible.builtin.fail"]["msg"]
    assert fail["when"] == "_crowdsec_lapi.rc != 0"
