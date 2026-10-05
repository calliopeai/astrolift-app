"""Identity captured by the worker that opens a stage, never inferred later."""

import json
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID


@dataclass(frozen=True)
class StageIncarnation:
    namespace: str
    workflow_id: str
    run_id: str
    activity_id: str

    def __post_init__(self):
        for name, limit in (("namespace", 200), ("workflow_id", 512), ("run_id", 200), ("activity_id", 200)):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError("Invalid stage activity identity")
        UUID(self.run_id)

    def fields(self) -> dict[str, str]:
        identity = (self.namespace, self.workflow_id, self.run_id, self.activity_id)
        return {
            "temporal_namespace": self.namespace,
            "temporal_workflow_id": self.workflow_id,
            "temporal_run_id": self.run_id,
            "temporal_activity_id": self.activity_id,
            "temporal_activity_key": sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest(),
        }
