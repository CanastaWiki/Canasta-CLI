"""The Helm chart's CrowdSec sidecar runs the shared start wrapper, so a
refused or unreachable Central API leaves the local API up on Kubernetes
as it does on Compose."""

import os
import re

from test_crowdsec_capi_refusal import CHART


class TestWrapperIsShared:
    def test_chart_local_api_runs_the_wrapper(self):
        with open(os.path.join(CHART, "templates",
                               "deployment-crowdsec.yaml")) as f:
            template = f.read()
        sidecar = template[template.index("- name: crowdsec\n"):]
        assert re.search(
            r'command:\s*- /bin/sh\s*- -c\s*'
            r'- \{\{ \.Files\.Get "files/crowdsec-start\.sh" \| toJson \}\}',
            sidecar[:sidecar.index("env:")])
