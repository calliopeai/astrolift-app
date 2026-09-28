"""Assembled Strawberry GraphQL schema.

Merges Query and Mutation types from all apps into a single schema.
Disabled features are automatically excluded via config.features.
"""

import strawberry
from strawberry_django.optimizer import DjangoOptimizerExtension

# ---------------------------------------------------------------------------
# Feature-gated imports
# ---------------------------------------------------------------------------
import astrolift_billing.schema as AstroliftBillingSchema  # noqa: E402
import astrolift_clusters.schema as AstroliftClustersSchema  # noqa: E402
import astrolift_forms.schema as AstroliftFormsSchema  # noqa: E402
import astrolift_identity.schema as AstroliftIdentitySchema  # noqa: E402
import astrolift_lifecycle.schema as AstroliftLifecycleSchema  # noqa: E402
import astrolift_observability.schema as AstroliftObservabilitySchema  # noqa: E402
import astrolift_operations.schema as AstroliftOperationsSchema  # noqa: E402
import astrolift_pipelines.schema as AstroliftPipelinesSchema  # noqa: E402
import astrolift_registry.schema as AstroliftRegistrySchema  # noqa: E402
import astrolift_scm.schema as AstroliftScmSchema  # noqa: E402
import astrolift_services.schema as AstroliftServicesSchema  # noqa: E402
import astrolift_workflows.schema as AstroliftTemporalWorkflowsSchema  # noqa: E402

# ---------------------------------------------------------------------------
# Always-on imports (core infrastructure)
# ---------------------------------------------------------------------------
import core.schema.mutations as CoreMutations
import organization.schema as OrganizationSchema
from config.features import Feature, is_enabled
from core.schema.types.audit import AuditLogQuery
from core.schema.types.permission_analysis import PermissionAnalysisQuery
from core.schema.types.server_info import AstroliftServerInfoQuery
from core.schema.types.user import UserType

_query_bases = [
    PermissionAnalysisQuery,
    AuditLogQuery,
    AstroliftServerInfoQuery,
    OrganizationSchema.Query,
    AstroliftIdentitySchema.IdentityQuery,
    AstroliftOperationsSchema.OperationsQuery,
    AstroliftRegistrySchema.RegistryQuery,
    AstroliftRegistrySchema.AppAccessQuery,
    AstroliftLifecycleSchema.LifecycleQuery,
    AstroliftClustersSchema.ClustersQuery,
    AstroliftClustersSchema.ClusterAuthUsersQuery,
    AstroliftBillingSchema.BillingQuery,
    AstroliftScmSchema.ScmQuery,
    AstroliftServicesSchema.ServicesQuery,
    AstroliftObservabilitySchema.GoldenSignalsQuery,
    AstroliftObservabilitySchema.LogHistoryQuery,
    AstroliftTemporalWorkflowsSchema.TemporalWorkflowsQuery,
    AstroliftFormsSchema.FormsQuery,
    AstroliftPipelinesSchema.PipelinesQuery,
]
_mutation_bases = [
    CoreMutations.Mutation,
    OrganizationSchema.Mutation,
    AstroliftIdentitySchema.IdentityMutation,
    AstroliftRegistrySchema.RegistryMutation,
    AstroliftRegistrySchema.AppAccessMutation,
    AstroliftOperationsSchema.OperationsMutation,
    AstroliftClustersSchema.ClustersMutation,
    AstroliftClustersSchema.ClusterAuthUsersMutation,
    AstroliftLifecycleSchema.LifecycleMutation,
    AstroliftScmSchema.ScmMutation,
    AstroliftServicesSchema.ServicesMutation,
    AstroliftTemporalWorkflowsSchema.TemporalWorkflowsMutation,
    AstroliftBillingSchema.BillingMutation,
    AstroliftFormsSchema.FormsMutation,
    AstroliftPipelinesSchema.PipelinesMutation,
]

if is_enabled(Feature.AGENTS):
    import astrolift_agents.schema as AstroliftAgentsSchema

    _query_bases.append(AstroliftAgentsSchema.AgentsQuery)
    _mutation_bases.append(AstroliftAgentsSchema.AgentsMutation)

if is_enabled(Feature.WORKFLOWS):
    import workflows.schema as WorkflowsSchema

    _query_bases.append(WorkflowsSchema.Query)
    _query_bases.append(AstroliftTemporalWorkflowsSchema.WorkflowManifestQuery)
    _mutation_bases.append(WorkflowsSchema.Mutation)
    # Visual-flow importer (Langflow/Flowise → WorkflowDefinition, #984-#986).
    _mutation_bases.append(AstroliftTemporalWorkflowsSchema.WorkflowImportMutation)
    # Native TOML manifest import (#970/#972).
    _mutation_bases.append(AstroliftTemporalWorkflowsSchema.WorkflowManifestMutation)

    # Configured-Workflow surface (spec 40 §6, #968) — lives under
    # astrolift_workflows for the #164 tenancy guardrail but needs the
    # workflows app's models, so it's wired alongside the legacy surface.
    _query_bases.append(AstroliftTemporalWorkflowsSchema.WorkflowsQuery)
    _mutation_bases.append(AstroliftTemporalWorkflowsSchema.WorkflowsMutation)


# ---------------------------------------------------------------------------
# Root Query
# ---------------------------------------------------------------------------

Query = strawberry.type(
    type("Query", tuple(_query_bases), {"__annotations__": {}}),
)


# ---------------------------------------------------------------------------
# Root Mutation
# ---------------------------------------------------------------------------

Mutation = strawberry.type(
    type("Mutation", tuple(_mutation_bases), {"__annotations__": {}}),
)


# ---------------------------------------------------------------------------
# Schema instance
# ---------------------------------------------------------------------------

from core.schema.audit import MutationAuditExtension, SecretSafeSchema
from core.schema.subscriptions import Subscription

schema = SecretSafeSchema(
    query=Query,
    mutation=Mutation,
    subscription=Subscription,
    extensions=[DjangoOptimizerExtension, MutationAuditExtension],
)


# ---------------------------------------------------------------------------
# Auth schema (limited -- login only, no auth required)
# ---------------------------------------------------------------------------


@strawberry.type
class AuthQuery:
    @strawberry.field
    def ok(self) -> str:
        return "ok"


@strawberry.type
class AuthMutation:
    @strawberry.mutation
    def login(self, info: strawberry.types.Info, username: str, password: str) -> UserType | None:
        from django.contrib.auth import authenticate, login

        user = authenticate(username=username, password=password)
        if user is not None:
            login(info.context.request, user)
            return user
        return None

    @strawberry.mutation
    def logout(self, info: strawberry.types.Info) -> bool:
        from django.contrib.auth import logout

        logout(info.context.request)
        return True


schema_auth = SecretSafeSchema(
    query=AuthQuery,
    mutation=AuthMutation,
)
