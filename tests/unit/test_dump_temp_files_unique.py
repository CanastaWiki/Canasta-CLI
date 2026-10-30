"""Overlapping exports or imports must not share a temp file in the container.

export_database.yml and import_database.yml run against a stub
canasta_root whose exec, db_admin and container copies only log, so each
run's container paths can be compared.
"""

import os
import re
import subprocess
import sys

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "mediawiki", "tasks")
EXPORT = os.path.join(TASKS, "export_database.yml")
IMPORT = os.path.join(TASKS, "import_database.yml")

EXPORT_TMP = re.compile(r"/tmp/canasta-export-[a-z0-9]{16}\.sql")
IMPORT_TMP = re.compile(r"/tmp/canasta-import-[a-z0-9]{16}\.sql(\.gz)?")


def _stub_root(tmp_path):
    log = tmp_path / "log"
    tasks = tmp_path / "root" / "roles" / "orchestrator" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "db_admin.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.set_fact": {
            "_db_admin_service": "db",
            "_db_admin_pwd": "PWD",
            "_db_admin_args": "-u root",
        }}]))
    (tasks / "exec.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.shell": "printf 'EXEC %s\\n' \"$CMD\" >> " + str(log),
        "environment": {"CMD": "{{ exec_command }}"},
    }]))
    for name, var in (("copy_into_container.yml", "copy_dest"),
                      ("copy_from_container.yml", "copy_src")):
        (tasks / name).write_text(yaml.safe_dump([{
            "ansible.builtin.shell": "printf 'COPY %s\\n' \"$P\" >> " + str(log),
            "environment": {"P": "{{ %s }}" % var},
        }]))
    with open(tasks / "copy_into_container.yml", "a") as f:
        f.write(yaml.safe_dump([{
            "ansible.builtin.shell": "printf 'SRC %s\\n' \"$P\" >> " + str(log),
            "environment": {"P": "{{ copy_src }}"},
        }]))
    return str(tmp_path / "root"), log


def _run(tmp_path, task_file, runs, extra=(), output=False):
    """Include task_file once per entry of runs (each a vars dict)."""
    root, log = _stub_root(tmp_path)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"canasta_root": root,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": task_file, "vars": v}
                  for v in runs],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_REMOTE_TEMP=str(tmp_path / "remote-tmp"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run(
        [playbook, "-vvv", "-i", "localhost,", str(play)] + list(extra),
        capture_output=True, text=True, env=env, cwd=tmp_path,
        stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = log.read_text().splitlines()
    return (lines, proc.stdout) if output else lines


def _paths(lines, pattern):
    return [m.group(0) for ln in lines for m in pattern.finditer(ln)]


class TestExport:
    def _export(self, tmp_path, gzip):
        return [{"instance_path": str(tmp_path), "export_db": "primo",
                 "export_dest": str(tmp_path / ("out%d.sql" % i)),
                 "export_gzip": gzip} for i in range(2)]

    def test_runs_use_different_temp_files(self, tmp_path):
        lines = _run(tmp_path, EXPORT, self._export(tmp_path, True))
        dumps = [ln for ln in lines if "mariadb-dump" in ln]
        assert len(dumps) == 2
        first, second = (EXPORT_TMP.search(d).group(0) for d in dumps)
        assert first != second

    def test_one_run_uses_one_temp_file(self, tmp_path):
        lines = _run(tmp_path, EXPORT, self._export(tmp_path, True)[:1])
        assert len(set(_paths(lines, EXPORT_TMP))) == 1
        tmp = _paths(lines, EXPORT_TMP)[0]
        assert "COPY %s.gz" % tmp in lines
        assert any(ln.startswith("EXEC gzip -f %s" % tmp) for ln in lines)
        assert any(ln.startswith("EXEC rm -f %s %s.gz" % (tmp, tmp))
                   for ln in lines)

    def test_no_shared_name_derived_from_the_database(self, tmp_path):
        lines = _run(tmp_path, EXPORT, self._export(tmp_path, False)[:1])
        assert not any("/tmp/primo.sql" in ln for ln in lines)


class TestImport:
    def _imports(self, tmp_path, name):
        dump = tmp_path / name
        dump.write_text("-- dump\n")
        return [{"instance_path": str(tmp_path), "instance_id": "inst",
                 "import_wiki_id": "docs",
                 "import_database_path": str(dump)} for _ in range(2)]

    def test_runs_use_different_temp_files(self, tmp_path):
        lines = _run(tmp_path, IMPORT, self._imports(tmp_path, "d.sql.gz"))
        copied = [ln.split(" ", 1)[1] for ln in lines if ln.startswith("COPY ")]
        assert len(copied) == 2 and copied[0] != copied[1]
        assert all(IMPORT_TMP.fullmatch(c) and c.endswith(".sql.gz")
                   for c in copied)

    def test_local_dump_is_copied_from_where_it_is(self, tmp_path):
        inst = tmp_path / "inst"
        inst.mkdir()
        runs = self._imports(tmp_path, "d.sql.gz")[:1]
        dump = runs[0]["import_database_path"]
        link = tmp_path / "link.sql.gz"
        link.symlink_to(dump)
        runs = [dict(runs[0], instance_path=str(inst),
                     import_database_path=str(link))]
        lines, out = _run(tmp_path, IMPORT, runs, output=True)
        staged = [ln.split(" ", 1)[1] for ln in lines if ln.startswith("SRC ")]
        # The symlink is resolved, since docker cp would copy the link itself.
        assert staged == [dump]
        assert not re.findall(r"PUT \S+ TO \S+/\.source", out)
        assert os.path.exists(dump)
        assert os.listdir(inst) == []

    def test_remote_dump_is_staged_next_to_the_instance(self, tmp_path):
        inst = tmp_path / "inst"
        inst.mkdir()
        runs = [dict(r, instance_path=str(inst))
                for r in self._imports(tmp_path, "d.sql.gz")[:1]]
        # The remote path runs over the local connection with the
        # local-connection check overridden.
        lines, out = _run(tmp_path, IMPORT, runs, output=True,
                          extra=["-e", '{"_import_local": false}'])
        staged = [ln.split(" ", 1)[1] for ln in lines if ln.startswith("SRC ")]
        assert len(staged) == 1
        stage = os.path.dirname(staged[0])
        assert os.path.dirname(stage) == str(inst)
        assert os.path.basename(stage).startswith(".canasta-import-")
        assert staged[0].endswith(".sql.gz")
        # The upload is staged in the same dir, not in ansible_remote_tmp.
        puts = re.findall(r"PUT \S+ TO (\S+/\.source\S*)", out)
        assert puts and all(p.startswith(stage + os.sep) for p in puts)
        assert os.listdir(inst) == []
        assert os.path.exists(runs[0]["import_database_path"])

    def test_load_and_cleanup_use_the_copied_file(self, tmp_path):
        for name, load in (("d.sql.gz", "gunzip -c %s |"), ("d.sql", "< %s")):
            sub = tmp_path / name.replace(".", "_")
            sub.mkdir()
            lines = _run(sub, IMPORT, self._imports(sub, name)[:1])
            copied = [ln.split(" ", 1)[1] for ln in lines
                      if ln.startswith("COPY ")]
            assert len(copied) == 1
            tmp = copied[0]
            assert any(load % tmp in ln for ln in lines), lines
            assert "EXEC rm -f %s" % tmp in lines
            assert not any("/tmp/import.sql" in ln for ln in lines)
