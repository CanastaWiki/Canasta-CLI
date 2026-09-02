"""Guards for my.cnf as a gitops-rendered, host-specific file.

my.cnf carries innodb_buffer_pool_size, which must fit the host's RAM: a
value correct on a large host cannot be allocated on a smaller one and
MariaDB exits 1 at startup with an empty log. It therefore belongs with
.env and wikis.yaml in the rendered-per-host category, not tracked and
shared.

These tests pin the three properties that make that safe:

  * my.cnf is ignored, so a repo never tracks one host's value again;
  * both render paths (render_compose and pull_compose) render it, so a
    pull and a post-restore re-render agree;
  * an absent value leaves the existing file alone rather than writing
    one without the setting, which would silently drop the server to the
    128 MB compiled default.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GITOPS_TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
RENDER_MYCNF = os.path.join(GITOPS_TASKS, "_render_mycnf.yml")
BACKFILL = os.path.join(GITOPS_TASKS, "backfill_mycnf_template.yml")
GITIGNORE_DEFAULT = os.path.join(
    REPO_ROOT, "roles", "gitops", "files", "gitignore.default"
)


def _walk_tasks(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _load_tasks(path):
    with open(path) as f:
        return list(_walk_tasks(yaml.safe_load(f)))


def _cmd(task):
    c = task.get("ansible.builtin.command") or task.get("command") or {}
    return c.get("cmd", "") if isinstance(c, dict) else str(c)


def _read(path):
    with open(path) as f:
        return f.read()


class TestMycnfIsIgnored:
    def test_gitignore_default_ignores_my_cnf(self):
        """my.cnf is rendered per host, so the repo must not track it.

        A tracked my.cnf cannot hold a correct value for hosts of
        different sizes — the whole point of #1552.
        """
        rules = [
            ln.strip()
            for ln in _read(GITIGNORE_DEFAULT).splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        assert "my.cnf" in rules

    def test_the_template_itself_is_not_ignored(self):
        """my.cnf.template is the shared, tracked half of the pair."""
        rules = [
            ln.strip()
            for ln in _read(GITIGNORE_DEFAULT).splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        assert "my.cnf.template" not in rules


class TestBothRenderPathsRenderMycnf:
    """render_compose and pull_compose each render independently.

    pull_compose does not delegate to render_compose, so a change made in
    one silently misses the other. render_compose is also what runs after
    'canasta backup restore', which is how a cross-host restore gets a
    my.cnf sized for the destination (#1550).
    """

    def _includes_mycnf(self, path):
        for t in _load_tasks(path):
            inc = t.get("ansible.builtin.include_tasks")
            target = inc.get("file", "") if isinstance(inc, dict) else str(inc or "")
            if "_render_mycnf.yml" in target:
                return True
        return False

    def test_render_compose_renders_my_cnf(self):
        path = os.path.join(GITOPS_TASKS, "render_compose.yml")
        assert self._includes_mycnf(path)

    def test_pull_compose_renders_my_cnf(self):
        path = os.path.join(GITOPS_TASKS, "pull_compose.yml")
        assert self._includes_mycnf(path)


class TestMissingValueLeavesMycnfAlone:
    def test_install_is_gated_on_nothing_missing(self):
        """Writing my.cnf without the buffer pool would drop MariaDB to
        128 MB — a silent, severe regression (#1455). The install block
        must be conditional on no placeholder being unresolved."""
        src = _read(RENDER_MYCNF)
        assert "_mycnf_missing | length == 0" in src

    def test_a_missing_value_warns(self):
        """The skip must be visible, not silent."""
        src = _read(RENDER_MYCNF)
        assert "_mycnf_missing | length > 0" in src

    def test_fails_when_no_value_and_no_file_to_fall_back_on(self):
        """After the untrack is committed, a host pulling that commit has
        my.cnf removed from its working tree before the render runs. With
        no value for that host there is nothing to keep, so skipping would
        leave it with no my.cnf and MariaDB back at 128 MB. Refuse instead.
        """
        fails = []
        for t in _load_tasks(RENDER_MYCNF):
            f = t.get("ansible.builtin.fail") or t.get("fail") or {}
            if isinstance(f, dict) and f.get("msg"):
                fails.append(str(f["msg"]))
        assert any("Refusing to leave this host without a my.cnf" in m
                   for m in fails), (
            "the no-value, no-file case must fail loudly"
        )


class TestMycnfWrittenInPlace:
    def test_installed_with_cp_not_an_atomic_write(self):
        """my.cnf is bind-mounted as a single file (./my.cnf:/etc/my.cnf).

        An atomic write (temp + rename) replaces the inode and strands the
        container on the old one — the #1456 failure that cost a day on the
        Caddyfile. cp truncates the destination and keeps its inode.
        """
        installs = [
            _cmd(t) for t in _load_tasks(RENDER_MYCNF) if _cmd(t).strip().startswith("cp ")
        ]
        assert installs, "my.cnf must be installed with cp to preserve its inode"

    def test_no_copy_module_writes_directly_to_my_cnf(self):
        """The copy module writes atomically; it may only target the
        staging file, never the bind-mounted my.cnf itself."""
        for t in _load_tasks(RENDER_MYCNF):
            c = t.get("ansible.builtin.copy") or t.get("copy") or {}
            if isinstance(c, dict):
                dest = str(c.get("dest", ""))
                assert not dest.endswith("/my.cnf"), (
                    f"copy writes atomically and would strand the bind mount: {dest}"
                )


class TestBackfillIsCreateOnly:
    def test_skips_when_a_template_already_exists(self):
        """Never overwrite a hand-customized template."""
        src = _read(BACKFILL)
        assert "not (_bmt_stats.results[2].stat.exists | default(false))" in src

    def test_handles_a_my_cnf_that_sets_no_buffer_pool(self):
        """The shipped my.cnf is [client] and nothing else, so the search
        for a buffer pool finds nothing on most instances.

        regex_search returns None there. Piping that straight into `first`
        raises, which fails the task and with it the whole upgrade — the
        backfill runs on every gitops instance, not just ones with tuning.
        """
        src = _read(BACKFILL)
        assert "| default([], true) }}" in src, (
            "the no-match case must be defaulted before it is indexed"
        )
        assert "| first" not in src, (
            "`first` raises on None and on an empty list; index with a "
            "default instead"
        )

    def test_untracks_my_cnf_with_cached_only(self):
        """The migration untracks my.cnf itself rather than telling the
        operator to run raw git. --cached only: the file stays on disk so
        this host keeps serving from it until the render replaces it."""
        rms = [_cmd(t) for t in _load_tasks(BACKFILL) if "git rm" in _cmd(t)]
        assert rms, "the migration must untrack my.cnf itself"
        for cmd in rms:
            assert "--cached" in cmd, (
                "must not delete my.cnf from disk: %s" % cmd
            )

    def test_stages_but_does_not_commit(self):
        """Staged for 'canasta gitops push', consistent with the other
        gitops migrations. Committing here would push without review."""
        cmds = [_cmd(t) for t in _load_tasks(BACKFILL) if "git" in _cmd(t)]
        assert any("git add" in c for c in cmds), "the migration must stage"
        assert not any("git commit" in c or "git push" in c for c in cmds), (
            "push is a separate, deliberate step"
        )

    def test_records_the_value_as_a_shared_default(self):
        """The value goes in hosts/_shared/vars.yaml, not this host's.

        Shared vars merge with the host winning, so every host inherits a
        working value the moment the untrack lands. Writing it per-host
        would leave every other host with no value and — after the untrack
        — no my.cnf either.
        """
        dests = []
        for t in _load_tasks(BACKFILL):
            c = t.get("ansible.builtin.copy") or t.get("copy") or {}
            if isinstance(c, dict) and c.get("dest"):
                dests.append(str(c["dest"]))
        assert any("hosts/_shared/vars.yaml" in d for d in dests), (
            "the buffer pool must be recorded as the shared default: %s"
            % dests
        )

    def test_skips_when_git_crypt_is_locked(self):
        """hosts/** is encrypted; a locked checkout cannot be read or
        written. Skip with a warning rather than failing the upgrade."""
        src = _read(BACKFILL)
        assert "_bmt_locked" in src
        assert "00474954435259505400" in src, (
            "must detect the git-crypt header"
        )
