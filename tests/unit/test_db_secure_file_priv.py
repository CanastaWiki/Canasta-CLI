"""MariaDB confines server-side file I/O to an empty directory.

Unset (the mariadb image's default) or empty, secure_file_priv lets any
account with FILE read and write files anywhere the mysql user can, through
INTO OUTFILE, LOAD DATA INFILE and LOAD_FILE(). Both orchestrators point it
at /var/lib/mysql-files, which must exist or the server refuses to start.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
COMPOSE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "compose", "docker-compose.yml")
FILES_DIR = "/var/lib/mysql-files"


def test_compose_db_sets_and_creates_the_directory():
    with open(COMPOSE) as f:
        command = yaml.safe_load(f)["services"]["db"]["command"]
    assert "--secure-file-priv=%s " % FILES_DIR in command
    assert "--secure-file-priv=''" not in command
    create = "install -d -o mysql -g mysql -m 0750 %s;" % FILES_DIR
    # Created in the foreground, before the server starts.
    assert command.index(create) < command.index("&")
    assert command.index(create) < command.index("exec docker-entrypoint.sh")
