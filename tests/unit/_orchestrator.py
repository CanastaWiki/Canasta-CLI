"""canasta.yml's play-level `instance_is_k8s` var, for tests that run task
files in a playbook of their own.

Tasks test the orchestrator through `instance_is_k8s`, which canasta.yml
defines once from `instance_orchestrator`. A test playbook that sets
`instance_orchestrator` merges in INSTANCE_IS_K8S_VARS so the tasks see the
same definition a real run does. A test that renders an expression with
Templar merges in TEMPLAR_VARS instead.
"""

import os

import yaml
from ansible.template import trust_as_template

CANASTA_YML = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "canasta.yml"))

with open(CANASTA_YML) as _f:
    INSTANCE_IS_K8S = yaml.safe_load(_f)[0]["vars"]["instance_is_k8s"]

INSTANCE_IS_K8S_VARS = {"instance_is_k8s": INSTANCE_IS_K8S}
TEMPLAR_VARS = {"instance_is_k8s": trust_as_template(INSTANCE_IS_K8S)}
