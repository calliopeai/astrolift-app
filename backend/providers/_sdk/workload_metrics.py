"""Canonical runtime membership for read-only workload measurements."""

from dataclasses import dataclass

APP_ID_LABEL = "astrolift.dev/app-id"
ENVIRONMENT_ID_LABEL = "astrolift.dev/environment-id"
WORKLOAD_ID_LABEL = "astrolift.dev/workload-id"


@dataclass(frozen=True)
class MetricContainer:
    pod_name: str
    pod_uid: str
    container_name: str
    container_id: str
    started_at: float


class MetricMembershipUnavailable(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)
