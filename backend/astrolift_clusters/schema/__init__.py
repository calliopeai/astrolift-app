from astrolift_clusters.schema.agent_install import ClusterAgentInstallMutation, ClusterAgentInstallQuery
from astrolift_clusters.schema.auth_users import ClusterAuthUsersMutation, ClusterAuthUsersQuery
from astrolift_clusters.schema.log_collector import ClusterLogCollectorMutation, ClusterLogCollectorQuery
from astrolift_clusters.schema.mutations import ClustersMutation
from astrolift_clusters.schema.nitro import NitroTasksMutation, NitroTasksQuery
from astrolift_clusters.schema.nvke import NvkeMutation, NvkeQuery
from astrolift_clusters.schema.nvke_install_plan_charts import NvkeInstallPlanChartsMutation
from astrolift_clusters.schema.nvke_install_plan_contexts import NvkeInstallPlanContextMutation
from astrolift_clusters.schema.nvke_install_plan_image_bindings import NvkeInstallPlanImageBindingMutation
from astrolift_clusters.schema.nvke_install_plan_images import NvkeInstallPlanImageMutation
from astrolift_clusters.schema.nvke_install_plan_immutable import NvkeInstallPlanImmutableMutation
from astrolift_clusters.schema.nvke_install_plan_node_platforms import NvkeInstallPlanNodePlatformsMutation
from astrolift_clusters.schema.nvke_install_plan_pull_authority import NvkeInstallPlanPullAuthorityMutation
from astrolift_clusters.schema.nvke_install_plan_pull_runtime import NvkeInstallPlanPullRuntimeMutation
from astrolift_clusters.schema.nvke_install_plan_pull_secret_analysis import (
    NvkeInstallPlanPullSecretAnalysisMutation,
)
from astrolift_clusters.schema.nvke_install_plan_trees import NvkeInstallPlanTreeMutation
from astrolift_clusters.schema.nvke_install_plan_verifications import NvkeInstallPlanVerificationMutation
from astrolift_clusters.schema.nvke_install_plan_workloads import NvkeInstallPlanWorkloadMutation
from astrolift_clusters.schema.nvke_install_plans import NvkeInstallPlanMutation, NvkeInstallPlanQuery
from astrolift_clusters.schema.nvke_operator import NvkeOperatorMutation, NvkeOperatorQuery
from astrolift_clusters.schema.queries import ClustersQuery

__all__ = [
    "ClusterLogCollectorMutation",
    "ClusterLogCollectorQuery",
    "ClusterAgentInstallMutation",
    "ClusterAgentInstallQuery",
    "ClusterAuthUsersMutation",
    "ClusterAuthUsersQuery",
    "ClustersMutation",
    "ClustersQuery",
    "NvkeOperatorQuery",
    "NvkeInstallPlanQuery",
    "NvkeInstallPlanMutation",
    "NvkeInstallPlanChartsMutation",
    "NvkeInstallPlanTreeMutation",
    "NvkeInstallPlanContextMutation",
    "NvkeInstallPlanImageMutation",
    "NvkeInstallPlanWorkloadMutation",
    "NvkeInstallPlanImageBindingMutation",
    "NvkeInstallPlanImmutableMutation",
    "NvkeInstallPlanNodePlatformsMutation",
    "NvkeInstallPlanPullAuthorityMutation",
    "NvkeInstallPlanPullRuntimeMutation",
    "NvkeInstallPlanPullSecretAnalysisMutation",
    "NvkeInstallPlanVerificationMutation",
    "NvkeOperatorMutation",
    "NvkeQuery",
    "NvkeMutation",
    "NitroTasksMutation",
    "NitroTasksQuery",
]
