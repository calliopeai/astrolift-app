from astrolift_clusters.schema.agent_install import ClusterAgentInstallMutation, ClusterAgentInstallQuery
from astrolift_clusters.schema.auth_users import ClusterAuthUsersMutation, ClusterAuthUsersQuery
from astrolift_clusters.schema.log_collector import ClusterLogCollectorMutation, ClusterLogCollectorQuery
from astrolift_clusters.schema.mutations import ClustersMutation
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
]
