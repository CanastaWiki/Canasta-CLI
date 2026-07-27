"""The Dockerfile installs Ansible collections by downloading pinned
tarballs with curl rather than running `ansible-galaxy install -r
requirements.yml`, so the versions live in two places. Nothing at build
time reconciles them: a bump in requirements.yml alone would leave the
image on the old collection, and the image is what most operators run.

These tests fail when the two drift apart in either direction.
"""

import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
DOCKERFILE = os.path.join(REPO_ROOT, "Dockerfile")
REQUIREMENTS = os.path.join(REPO_ROOT, "requirements.yml")

# Collection name -> the Dockerfile shell variable pinning its version.
_VERSION_VARS = {
    "kubernetes.core": "K8S_CORE_VERSION",
    "ansible.posix": "ANSIBLE_POSIX_VERSION",
}


def _read(path):
    with open(path) as f:
        return f.read()


def _requirements_pins():
    """{collection name: version string} from requirements.yml."""
    data = yaml.safe_load(_read(REQUIREMENTS))
    return {
        c["name"]: str(c["version"])
        for c in data.get("collections", [])
        if isinstance(c, dict) and "version" in c
    }


def _dockerfile_pins():
    """{collection name: version} from the Dockerfile's version vars."""
    content = _read(DOCKERFILE)
    pins = {}
    for name, var in _VERSION_VARS.items():
        m = re.search(r'%s="([^"]+)"' % re.escape(var), content)
        if m:
            pins[name] = m.group(1)
    return pins


class TestDockerfileCollectionsMatchRequirements:
    def test_every_requirements_collection_is_installed_by_the_dockerfile(self):
        missing = sorted(set(_requirements_pins()) - set(_dockerfile_pins()))
        assert not missing, (
            "requirements.yml pins %s, but the Dockerfile has no matching "
            "version variable. Add the collection to the curl download block "
            "and to _VERSION_VARS in this test, or the image will ship "
            "without it." % missing
        )

    def test_versions_agree(self):
        req = _requirements_pins()
        docker = _dockerfile_pins()
        mismatched = {
            name: (req[name], docker[name])
            for name in set(req) & set(docker)
            if req[name] != docker[name]
        }
        assert not mismatched, (
            "requirements.yml and the Dockerfile disagree on collection "
            "versions (name: requirements.yml vs Dockerfile): %s" % mismatched
        )

    def test_pins_are_exact_versions(self):
        # The curl URLs interpolate the version directly, so a range
        # like ">=6.4.0" would build a 404 instead of failing loudly.
        loose = {
            name: ver for name, ver in _requirements_pins().items()
            if name in _VERSION_VARS
            and not re.fullmatch(r"\d+(\.\d+)*", ver)
        }
        assert not loose, (
            "collections fetched by curl must pin an exact version — a "
            "range would be interpolated into the download URL: %s" % loose
        )

    def test_each_pinned_collection_is_actually_downloaded_and_installed(self):
        content = _read(DOCKERFILE)
        for name, var in _VERSION_VARS.items():
            if name not in _dockerfile_pins():
                continue
            assert "${%s}" % var in content, (
                "%s is set but never interpolated into a download URL" % var
            )
            slug = name.replace(".", "-")
            assert re.search(r"%s\.tar\.gz" % re.escape(slug), content), (
                "no %s tarball referenced in the Dockerfile for %s"
                % (slug, name)
            )
