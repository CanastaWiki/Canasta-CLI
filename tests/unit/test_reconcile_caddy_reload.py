"""Structural guards for the Caddy reload on `canasta reconcile`.

Compose bind-mounts config/Caddyfile and config/Caddyfile.site, so `docker
compose up -d` never recreates the container on a content change and a
regenerated Caddyfile is left unread. reconcile must therefore reload Caddy
explicitly, after the converge and only when Caddy is actually running.
Kubernetes rolls the caddy pod off the ConfigMap checksum annotation and must
not be reloaded this way.
"""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RECONCILE = os.path.join(
    REPO_ROOT, "roles", "instance_lifecycle", "tasks", "reconcile.yml"
)
RELOAD_CADDY = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "reload_caddy.yml"
)
CADDY_DEPLOYMENT = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "helm", "canasta",
    "templates", "deployment-caddy.yaml",
)


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _tasks_from(task):
    inc = task.get("ansible.builtin.include_role") or task.get("include_role")
    return inc.get("tasks_from", "") if isinstance(inc, dict) else ""


def _includes(task):
    inc = (task.get("ansible.builtin.include_tasks")
           or task.get("include_tasks") or "")
    return inc if isinstance(inc, str) else str(inc)


def _walk(tasks):
    """Yield every task, descending into block/rescue/always."""
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key))


class TestReconcileReloadsCaddy:
    def test_reconcile_reloads_caddy(self):
        froms = [_tasks_from(t) for t in _load(RECONCILE)]
        assert "reload_caddy.yml" in froms, (
            "reconcile must reload Caddy; a bind-mounted Caddyfile change is "
            "invisible to `docker compose up -d`, so the regenerated config "
            "would never reach the running server"
        )

    def test_reload_comes_after_the_converge(self):
        froms = [_tasks_from(t) for t in _load(RECONCILE)]
        assert froms.index("reload_caddy.yml") > froms.index("start.yml"), (
            "reload must follow start.yml so a Caddy the converge had to "
            "bring up is already listening"
        )

    def test_reload_comes_after_config_regeneration(self):
        froms = [_tasks_from(t) for t in _load(RECONCILE)]
        assert froms.index("reload_caddy.yml") > froms.index(
            "update_config.yml"
        ), "reload must follow the Caddyfile regeneration it delivers"

    def test_reconcile_is_still_non_disruptive(self):
        froms = [_tasks_from(t) for t in _load(RECONCILE)]
        assert "stop.yml" not in froms, (
            "the reload must not have introduced a stop — reconcile stays the "
            "non-disruptive converge"
        )


class TestReloadCaddyTask:
    def test_reloads_the_caddy_service(self):
        execs = [
            t for t in _walk(_load(RELOAD_CADDY))
            if _includes(t).endswith("exec.yml")
        ]
        assert execs, "reload_caddy must exec through the exec.yml abstraction"
        variables = execs[0].get("vars", {})
        assert variables.get("exec_service") == "caddy", (
            "the reload must target the caddy service, not the default (web)"
        )
        assert "caddy reload" in variables.get("exec_command", ""), (
            "must issue `caddy reload`"
        )

    def test_reload_is_gated_on_caddy_running(self):
        execs = [
            t for t in _walk(_load(RELOAD_CADDY))
            if _includes(t).endswith("exec.yml")
        ]
        assert "_reload_caddy" in str(execs[0].get("when", "")), (
            "reloading a Caddy that isn't up would fail on a stopped or "
            "partially converged instance"
        )

    def test_gate_requires_compose_and_a_running_caddy(self):
        gate = next(
            (t for t in _walk(_load(RELOAD_CADDY))
             if "_reload_caddy" in (t.get("ansible.builtin.set_fact") or {})),
            None,
        )
        assert gate is not None, "reload_caddy must compute a _reload_caddy gate"
        expr = gate["ansible.builtin.set_fact"]["_reload_caddy"]
        assert "compose" in expr, (
            "K8s rolls the caddy pod off the ConfigMap checksum annotation; "
            "it must not be reloaded through the Compose path"
        )
        assert "caddy" in expr and "_running_services" in expr, (
            "the gate must check that caddy is among the running services"
        )

    def test_lists_running_services_before_gating(self):
        tasks = _load(RELOAD_CADDY)
        listed = next(
            i for i, t in enumerate(tasks)
            if _includes(t).endswith("list_running_services.yml")
        )
        gated = next(
            i for i, t in enumerate(tasks)
            if "_reload_caddy" in (t.get("ansible.builtin.set_fact") or {})
        )
        assert listed < gated, (
            "_running_services must be populated before the gate reads it"
        )

    def test_rejected_config_fails_with_a_reassuring_message(self):
        rescues = [
            t for task in _load(RELOAD_CADDY)
            for t in _walk(task.get("rescue"))
            if "ansible.builtin.fail" in t
        ]
        assert rescues, (
            "a rejected config must be rescued into a clear failure, not a "
            "raw shell error"
        )
        msg = rescues[0]["ansible.builtin.fail"]["msg"]
        assert "Caddyfile.site" in msg, "must point at the file to fix"
        assert "previously loaded" in msg, (
            "must say the old config is still serving — `caddy reload` is "
            "atomic, so the site is not down"
        )


class TestKubernetesStillRollsOnChecksum:
    """The reload is Compose-only because K8s already handles it. If that
    annotation ever goes away, K8s loses config delivery silently."""

    def test_caddy_deployment_has_a_config_checksum_annotation(self):
        with open(CADDY_DEPLOYMENT) as f:
            body = f.read()
        assert "checksum/caddy-config" in body, (
            "the caddy deployment must keep its config checksum annotation — "
            "it is what rolls the pod on a ConfigMap change, and the reason "
            "reload_caddy.yml is Compose-only"
        )


class TestCaddyfileIsWrittenInPlace:
    """A reload can only apply what the container can read.

    The compose stack bind-mounts the file itself
    (./config/Caddyfile:/etc/caddy/Caddyfile), and a file mount resolves
    to an inode at container start. Writing the Caddyfile by rename gives
    it a new inode, so the container keeps reading the old one for its
    whole lifetime and `caddy reload` applies nothing — silently, since
    `caddy validate` in the container reads the same stale inode.
    """

    REWRITE = os.path.join(
        REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_caddy.yml")

    def _tasks(self):
        with open(self.REWRITE) as f:
            return list(_walk(yaml.safe_load(f)))

    def _by_name(self, name):
        return next(
            (t for t in self._tasks() if t.get("name") == name), None)

    def test_no_task_writes_the_mounted_caddyfile_directly(self):
        for task in self._tasks():
            for module in ("ansible.builtin.copy", "copy",
                           "ansible.builtin.template", "template"):
                args = task.get(module)
                if isinstance(args, dict) and str(
                        args.get("dest", "")).endswith("config/Caddyfile"):
                    raise AssertionError(
                        "%r writes config/Caddyfile with %s, which renames a "
                        "temp file into place and strands the container's "
                        "bind mount" % (task.get("name"), module))

    def test_the_render_goes_to_a_staging_file(self):
        render = self._by_name(
            "Write the rendered Caddyfile (rendered + global blocks melded)")
        assert render is not None
        dest = render["ansible.builtin.copy"]["dest"]
        assert "_caddy_staging" in dest, (
            "the render must land on a staging file, not the mounted path")

    def test_the_install_uses_cp_to_keep_the_inode(self):
        install = self._by_name("Install the Caddyfile in place")
        assert install is not None, "install task missing/renamed"
        cmd = install["ansible.builtin.command"]["cmd"]
        assert cmd.strip().startswith("cp "), (
            "cp truncates the destination and keeps its inode; mv/install "
            "would replace it and strand the mount")
        assert "config/Caddyfile" in cmd

    def test_the_install_is_skipped_when_the_content_is_unchanged(self):
        install = self._by_name("Install the Caddyfile in place")
        when = str(install.get("when"))
        assert "_caddy_render.checksum" in when and "stat.checksum" in when, (
            "an unchanged render must report ok, not changed")
