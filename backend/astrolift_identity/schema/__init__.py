from astrolift_identity.anonymize import IdentityAnonymizeUserMutation
from astrolift_identity.connected_accounts import (
    MyConnectedAccountsMutation,
    MyConnectedAccountsQuery,
)
from astrolift_identity.schema.mutations import IdentityMutation as _BaseIdentityMutation
from astrolift_identity.schema.queries import IdentityQuery as _BaseIdentityQuery
from astrolift_identity.schema.types import (
    AppSummaryType,
    NavTreeProjectType,
    NavTreeTeamType,
    NavTreeType,
    OrganizationType,
    ProjectType,
    TeamType,
)


# Compose the per-user connected-accounts surface into the identity
# Query / Mutation roots so ``config.schema`` doesn't need to learn
# about the new domain — it already merges ``IdentityQuery`` and
# ``IdentityMutation`` into the root schema.
class IdentityQuery(_BaseIdentityQuery, MyConnectedAccountsQuery):
    pass


class IdentityMutation(
    _BaseIdentityMutation,
    MyConnectedAccountsMutation,
    IdentityAnonymizeUserMutation,
):
    pass


__all__ = [
    "AppSummaryType",
    "IdentityMutation",
    "IdentityQuery",
    "NavTreeProjectType",
    "NavTreeTeamType",
    "NavTreeType",
    "OrganizationType",
    "ProjectType",
    "TeamType",
]
