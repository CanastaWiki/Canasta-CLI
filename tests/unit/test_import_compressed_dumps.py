"""import, create -d and add -d load gzip, xz and zstd dumps.

The dump's leading bytes decide how it is read, so an xz dump is no longer
fed to mariadb as SQL. The load command runs here in bash against a stub
`mariadb` that records what it reads.
"""

import gzip
import lzma
import os
import shutil
import subprocess

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from filter_plugins.canasta_databases import canasta_extra_dump_plan

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
IMPORT = os.path.join(REPO_ROOT, "roles", "mediawiki", "tasks",
                      "import_database.yml")
SQL = b"CREATE TABLE t (id INT);\n"


def _command(path):
    with open(IMPORT) as f:
        tasks = yaml.safe_load(f)
    block = next(t for t in tasks if "block" in t)["block"]
    task = next(t for t in block if t.get("name") == "Import the database dump")
    templar = Templar(loader=DataLoader(), variables={
        "_import_container_file": str(path), "_db_admin_pwd": "",
        "_db_admin_args": "", "import_wiki_id": "docs",
        "_db_admin_service": "db"})
    return str(templar.template(trust_as_template(
        task["vars"]["exec_command"])))


def _bin(tmp_path, tools):
    """A PATH holding only the named tools, plus a recording mariadb."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in tools:
        os.symlink(shutil.which(tool), bin_dir / tool)
    mariadb = bin_dir / "mariadb"
    mariadb.write_text('#!/bin/sh\necho "$@" > "$OUT.args"; cat > "$OUT"\n')
    mariadb.chmod(0o755)
    return str(bin_dir)


def _load(tmp_path, data, tools=("head", "od", "tr", "cat", "gzip", "gunzip",
                                 "xz", "zstd")):
    dump = tmp_path / "dump.sql.gz"
    dump.write_bytes(data)
    out = tmp_path / "loaded"
    path = _bin(tmp_path, [t for t in tools if shutil.which(t)])
    proc = subprocess.run(["/bin/bash", "-c", _command(dump)], capture_output=True,
                          text=True, env={"PATH": path, "OUT": str(out)})
    return proc, (out.read_bytes() if out.exists() else None)


def test_plain_sql_is_loaded_as_is(tmp_path):
    proc, loaded = _load(tmp_path, SQL)
    assert proc.returncode == 0, proc.stderr
    assert loaded == SQL


def test_gzip_is_decompressed(tmp_path):
    proc, loaded = _load(tmp_path, gzip.compress(SQL))
    assert proc.returncode == 0, proc.stderr
    assert loaded == SQL


def test_xz_is_decompressed_whatever_its_name(tmp_path):
    proc, loaded = _load(tmp_path, lzma.compress(SQL))
    assert proc.returncode == 0, proc.stderr
    assert loaded == SQL


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not installed")
def test_zstd_is_decompressed(tmp_path):
    data = subprocess.run(["zstd", "-q", "-c"], input=SQL,
                          capture_output=True, check=True).stdout
    proc, loaded = _load(tmp_path, data)
    assert proc.returncode == 0, proc.stderr
    assert loaded == SQL


def test_a_missing_tool_is_named_and_nothing_is_loaded(tmp_path):
    proc, loaded = _load(tmp_path, lzma.compress(SQL),
                         tools=("head", "od", "tr", "cat", "gunzip"))
    assert proc.returncode == 3
    assert "xz-compressed" in proc.stderr and "db" in proc.stderr
    assert loaded is None


def test_a_corrupt_dump_fails_the_load(tmp_path):
    proc, _ = _load(tmp_path, lzma.compress(SQL)[:20])
    assert proc.returncode != 0


def test_extra_dumps_of_every_format_are_found():
    plan = canasta_extra_dump_plan(
        ["/d/docs.a.sql.xz", "/d/docs.b.sql.zst", "/d/docs.c.sql.gz",
         "/d/docs.e.sql", "/d/docs.f.sql.bz2"], "/d/docs", "docs")
    assert plan == {"load": ["a", "b", "c", "e"], "skipped": []}
