"""`canasta delete --keep-data` leaves a Kubernetes instance's Argo CD
Application in place, so its namespace and volumes stay as the way back."""

from test_delete_keep_data import _tasks


def test_keep_data_takes_the_deregister_only_path():
    names = [t.get("name") for t in _tasks()]
    step = "Remove Argo CD Application if present"
    assert names.index(step) > names.index("Deregister only"), step
