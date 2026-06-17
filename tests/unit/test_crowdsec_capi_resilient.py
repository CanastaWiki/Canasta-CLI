"""Guards for CrowdSec CAPI registration resilience (#709).

`cscli capi register` reaches CrowdSec's Central API over the network.
A transient outage must NOT abort `canasta create`: the registration is
retried, a sustained failure is tolerated via rescue (the instance works
without the community blocklist and the next start re-attempts), and the
credentials file is written atomically so a failed attempt never leaves
an empty/corrupt online_api_credentials.yaml behind.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ENSURE_CAPI = os.path.join(
    REPO_ROOT, "roles", "crowdsec", "tasks", "ensure_capi.yml"
)


def _load():
    with open(ENSURE_CAPI) as f:
        return yaml.safe_load(f)


def _iter_tasks(tasks):
    """Yield every task, descending into block/rescue/always wrappers."""
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _iter_tasks(t[nested])


def _find(name_substring):
    for t in _iter_tasks(_load()):
        if name_substring in t.get("name", ""):
            return t
    return None


def test_register_task_retries():
    """The register call must retry to ride out a transient CAPI blip."""
    reg = _find("Register CAPI and write the credentials file")
    assert reg is not None, "register task must exist"
    assert int(reg.get("retries", 0)) >= 2, (
        "CAPI registration must set retries to survive a transient "
        "Central API outage (#709)"
    )
    assert "rc == 0" in str(reg.get("until", "")), (
        "registration must retry until the command succeeds"
    )


def test_register_is_in_a_rescued_block():
    """A sustained CAPI failure must be tolerated, not abort create."""
    block_task = _find("Register CAPI (retry")
    assert block_task is not None and "block" in block_task, (
        "the register call must live in a block:"
    )
    assert "rescue" in block_task, (
        "the register block must have a rescue: so a sustained CAPI "
        "failure does not abort 'canasta create' (#709)"
    )
    # The rescue must not itself hard-fail.
    rescue_names = [t.get("name", "") for t in block_task["rescue"]]
    assert any("Warn" in n for n in rescue_names), (
        "rescue should warn that registration was skipped"
    )


def test_credentials_written_atomically():
    """Write to a temp file and move into place on success, so a failed
    register never corrupts online_api_credentials.yaml."""
    reg = _find("Register CAPI and write the credentials file")
    cmd = reg["ansible.builtin.command"]["cmd"]
    assert "online_api_credentials.yaml.tmp" in cmd, (
        "register must write to a temp file, not directly to the real "
        "credentials file (#709)"
    )
    assert "mv" in cmd and "&&" in cmd, (
        "the temp file must be moved into place only on success (&&)"
    )


def test_restart_only_on_success():
    """The engine restart belongs in the success block, not the rescue,
    so it does not run when registration failed."""
    block_task = _find("Register CAPI (retry")
    block_names = [t.get("name", "") for t in block_task["block"]]
    rescue_names = [t.get("name", "") for t in block_task["rescue"]]
    assert any("Restart the engine" in n for n in block_names), (
        "engine restart must be in the success block"
    )
    assert not any("Restart the engine" in n for n in rescue_names), (
        "engine restart must not run on the failure path"
    )
