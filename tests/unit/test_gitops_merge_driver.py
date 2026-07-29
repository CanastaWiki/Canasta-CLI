"""The git-crypt merge driver must merge encrypted paths without leaking them.

hosts/** is stored as ciphertext, which git cannot merge, so every concurrent
edit to a shared vars file was an unresolvable binary conflict. A merge driver
fixes that, but only if it re-encrypts what it hands back: the clean filter
does NOT run on a merge result, so a driver that returns plaintext commits the
secrets in cleartext. Everything here guards that one property.
"""
import os
import stat

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GITOPS = os.path.join(REPO_ROOT, "roles", "gitops")
DRIVER = os.path.join(GITOPS, "files", "git-crypt-merge")
ENSURE = os.path.join(GITOPS, "tasks", "_ensure_merge_driver.yml")
ATTRS = os.path.join(GITOPS, "files", "gitattributes.default")
VARS = os.path.join(GITOPS, "vars", "main.yml")


def _read(path):
    with open(path) as fh:
        return fh.read()


def _driver_name():
    return yaml.safe_load(_read(VARS))["gitops_merge_driver"]


def test_driver_is_executable():
    assert os.path.isfile(DRIVER)
    assert os.stat(DRIVER).st_mode & stat.S_IXUSR, (
        "git invokes the driver directly; a non-executable one fails every merge"
    )


def test_driver_re_encrypts_its_result():
    body = _read(DRIVER)
    assert "git-crypt clean" in body, (
        "the clean filter does not run on a merge result — a driver that "
        "returns plaintext commits the secrets in cleartext"
    )
    # The re-encryption must gate the exit status, not run best-effort.
    assert "if ! git-crypt clean" in body


def test_driver_fails_closed_on_a_decrypt_failure():
    body = _read(DRIVER)
    assert "if ! git-crypt smudge" in body
    assert body.count("exit 1") >= 3, (
        "a locked checkout, a failed encrypt, and a missing tool must all "
        "leave the conflict rather than stage a guess"
    )


def test_driver_refuses_to_run_without_its_tools():
    body = _read(DRIVER)
    assert "command -v" in body, (
        "a missing 'head' makes the magic-header test answer 'not encrypted', "
        "which merges ciphertext and re-encrypts the garbage under exit 0"
    )
    for tool in ("git-crypt", "head", "od", "tr", "grep"):
        assert tool in body


def test_driver_only_decrypts_what_is_encrypted():
    body = _read(DRIVER)
    assert "is_encrypted" in body, (
        "an empty ancestor or a pre-encryption commit arrives as plaintext; "
        "smudging it twice corrupts it"
    )
    assert "00474954435259505400" in body, "expected the git-crypt magic header"


def test_driver_labels_the_conflict_sides():
    body = _read(DRIVER)
    assert "-L remote -L base -L local" in body, (
        "unlabeled markers name temp files, which tells the operator nothing"
    )
    assert "--marker-size" in body


def test_attribute_names_the_driver():
    name = _driver_name()
    line = _read(ATTRS).strip()
    assert line.startswith("hosts/**")
    assert "filter=git-crypt" in line and "merge=%s" % name in line


def test_every_host_registers_the_driver_locally():
    tasks = yaml.safe_load(_read(ENSURE))
    body = _read(ENSURE)
    # merge.<name>.driver lives in .git/config, which no clone carries.
    assert "merge.{{ gitops_merge_driver }}.driver" in body
    assert "%O %A %B %L" in body, "the driver needs all three stages"
    copied = [t for t in tasks if "ansible.builtin.copy" in t]
    assert copied and copied[0]["ansible.builtin.copy"]["mode"] == "0755"


def test_the_driver_is_registered_everywhere_it_is_needed():
    # init and join cover new hosts; pull and push let a repo that predates
    # the driver self-heal without an operator visiting each host.
    for flow in ("init_compose.yml", "join.yml", "pull_compose.yml",
                 "push_compose.yml"):
        body = _read(os.path.join(GITOPS, "tasks", flow))
        assert "_ensure_merge_driver.yml" in body, "%s never registers it" % flow


def test_registration_precedes_the_pull_and_touches_no_tracked_file():
    body = _read(os.path.join(GITOPS, "tasks", "pull_compose.yml"))
    assert body.index("_ensure_merge_driver.yml") < body.index("git -c commit.gpgsign=false pull"), (
        "registering after the pull would not help that pull"
    )
    # pull refuses to run on a dirty tree, so registration must stay local.
    ensure = _read(ENSURE)
    assert "git add" not in ensure and "git commit" not in ensure


def test_the_attribute_is_named_host_locally_not_committed():
    body = _read(ENSURE)
    # Git only calls the driver if an attribute names it. A repo created
    # before the driver existed has no merge= in its tracked .gitattributes,
    # so registering the driver alone would leave every existing repo still
    # conflicting until some host committed that line.
    assert ".git/info/attributes" in body, (
        "the attribute has to be supplied host-locally, or existing repos "
        "stay broken until someone pushes"
    )
    assert "merge={{ gitops_merge_driver }}" in body
    # Committing it instead would have two hosts editing one tracked line
    # from divergent bases — a conflict over the fix for conflicts.
    push = _read(os.path.join(GITOPS, "tasks", "push_compose.yml"))
    assert "git add -- .gitattributes" not in push
