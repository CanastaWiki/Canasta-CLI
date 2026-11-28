"""`canasta upgrade` restarts an instance, gitops-managed or not, when
something changed, and reports an untouched one as up to date."""

from _upgrade_play import _run

UP_TO_DATE = "Already up to date — no restart needed."


def test_gitops_instance_restarts(tmp_path):
    out, calls = _run(tmp_path)
    assert "stop.yml" in calls and "start.yml" in calls
    assert "staged in its gitops repo" not in out


def test_account_move_restart_is_not_up_to_date(tmp_path):
    out, _ = _run(tmp_path, gitops=False, images_updated=False,
                  _mig_db_redeployed=True)
    assert UP_TO_DATE not in out


def test_untouched_instance_is_up_to_date(tmp_path):
    out, _ = _run(tmp_path, gitops=False, images_updated=False,
                  _mig_db_redeployed=False)
    assert UP_TO_DATE in out
