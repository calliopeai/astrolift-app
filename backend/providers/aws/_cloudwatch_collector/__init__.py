"""Staged collector profile, copied byte-for-byte from opscode 4e8d0841.

Not in the installable recipe: the current prereq activity does not enforce
preparation/ingestion admission. Node DaemonSets do not collect Fargate logs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from _sdk.cluster import BootstrapComponent

PROFILE = Path(__file__).parent
PIN = json.loads((PROFILE / "upstream-chart.json").read_text())
COMPONENT_KEY = "fluent-bit-cloudwatch"
NAMESPACE = "astrolift-system"
SERVICE_ACCOUNT = "fluent-bit"


def staged_component(*, irsa_role_arn: str, region: str, log_group: str) -> BootstrapComponent:
    """Render stage values only; callers still owe guarded installation proof."""
    if not all(isinstance(value, str) and value.strip() for value in (irsa_role_arn, region, log_group)):
        raise ValueError("Collector infrastructure preparation is required")
    values: dict[str, Any] = yaml.safe_load((PROFILE / "values-aws.yaml").read_text())
    values["serviceAccount"]["annotations"]["eks.amazonaws.com/role-arn"] = irsa_role_arn
    values["cloudwatch"] = {"region": region, "logGroup": log_group}
    return BootstrapComponent(
        key=COMPONENT_KEY,
        title="CloudWatch pod logs (EC2 nodes only)",
        default_enabled=False,
        rationale=(
            "Prepared stage only. Requires guarded install, readiness and verified ingestion. Does not cover Fargate."
        ),
        helm_values=values,
        requires=["collector:current-admission", "collector:prepared-infrastructure"],
        chart_name=PIN["name"],
        chart_repo_url=PIN["repository"],
        chart_version=PIN["version"],
    )
