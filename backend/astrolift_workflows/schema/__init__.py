"""GraphQL schema for the Temporal workflow viewer (#437).

Exposes per-instance read + admin write surfaces so operators can drill
into async tasks without leaving for the Temporal UI. Kept as its own
schema module (separate from ``workflows.schema`` which owns the
StateMachine WorkflowDefinition) so the wiring in ``config.schema``
stays narrow and the two domains can move independently.
"""

from astrolift_workflows.schema.manifest import WorkflowManifestQuery
from astrolift_workflows.schema.mutations import (
    TemporalWorkflowsMutation,
    WorkflowsMutation,
)
from astrolift_workflows.schema.queries import (
    TemporalWorkflowsQuery,
    WorkflowsQuery,
)

__all__ = [
    "TemporalWorkflowsQuery",
    "TemporalWorkflowsMutation",
    "WorkflowsQuery",
    "WorkflowsMutation",
    "WorkflowManifestQuery",
]
