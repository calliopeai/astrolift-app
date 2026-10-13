"""Brain read tokens (#2320): connect a client to a brain the caller can read."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_registry.schema.mutations.helpers import _actor
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission


@strawberry.type(name="AstroliftBrainConnection")
class BrainConnectionType:
    """What a client stores to mount a brain: its endpoints, the header to send
    and a token for this caller and this brain, valid until ``expires_at``."""

    id: GUID
    slug: str
    endpoints: list[str]
    header: str
    token: str
    expires_at: str


def _target(*args, **kwargs):
    slug = kwargs.get("slug")
    return ("RegisteredApp", slug) if slug else None


@strawberry.type
class BrainMutations:
    @strawberry.field(
        description=(
            "A 15-minute read token for one brain the caller can read (#2320), with its endpoints "
            "and the header to send it in. The cluster edge admits it to that brain only; a revoked "
            "share stops new tokens at once. The token is never written to the audit log."
        )
    )
    @mutation_audit(action="brain.token.issue", target=_target)
    @require_permission(Permission.APP_READ, any_scope=True)
    @tenant_scoped()
    def issue_brain_read_token(self, info: Info, slug: str) -> MutationResultType[BrainConnectionType]:
        from astrolift_registry.brain_tokens import BrainTokenError, connection_descriptor
        from astrolift_registry.brains import visible_brains

        # Not visible and not a brain read the same, so existence never leaks.
        brain = visible_brains().filter(slug=slug.strip()).first() if slug.strip() else None
        if brain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "brain not found", field="slug")
        user = _actor()
        if user is None:
            return gql_failure(ErrorCode.PERMISSION_DENIED.value, "a brain token is issued to a person")
        try:
            row = connection_descriptor(user=user, brain=brain)
        except BrainTokenError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, str(exc))
        return gql_success(
            BrainConnectionType(
                id=GUID(row["id"]),
                slug=row["slug"],
                endpoints=[env["url"] for env in row["environments"]],
                header=row["header"],
                token=row["token"],
                expires_at=row["expires_at"],
            )
        )
