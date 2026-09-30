"""Public catalogue and authorized deployment observations (#2214)."""

import strawberry
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from graphql import GraphQLError
from strawberry.types import Info

from astrolift_identity.api_tokens import get_current_api_token, with_active_org_member
from astrolift_identity.models import ApiToken
from astrolift_services.hf_catalogue import (
    CatalogueFilters,
    HuggingFaceModelResult,
    HuggingFaceModelsPage,
    model_detail,
    search_models,
)
from core.tenancy import get_current_tenant


def _catalogue_audience(info: Info) -> str:
    user = getattr(getattr(info.context, "request", None), "user", None)
    if (
        user is None
        or not user.is_authenticated
        or not get_user_model().objects.filter(pk=user.pk, is_active=True).exists()
    ):
        raise GraphQLError("Authentication required", extensions={"code": "UNAUTHENTICATED"})
    tenant = get_current_tenant()
    token = get_current_api_token()
    if token is not None:
        active = with_active_org_member(
            ApiToken.objects.filter(
                pk=token.pk, user_id=user.pk, is_revoked=False, deleted_at__isnull=True
            ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())),
            user="user",
            organization="organization",
        ).first()
        if active is None or tenant is None or active.organization_id != tenant.organization_id:
            raise GraphQLError("Authentication required", extensions={"code": "UNAUTHENTICATED"})
    return f"{user.pk}:{tenant.organization_id if tenant else ''}:{token.pk if token is not None else ''}"


def _bad_input(exc):
    return GraphQLError(str(exc), extensions={"code": "BAD_INPUT"})


@strawberry.type
class ModelReadsQuery:
    @strawberry.field
    def astrolift_hugging_face_models(
        self,
        info: Info,
        search: str = "",
        author: str = "",
        pipeline_tag: str = "",
        library: str = "",
        license: str = "",
        gated: bool | None = None,
        sort_by: str = "downloads",
        first: int = 20,
        after: str | None = None,
    ) -> HuggingFaceModelsPage:
        audience = _catalogue_audience(info)
        try:
            return search_models(
                CatalogueFilters(
                    search=search,
                    author=author,
                    pipeline_tag=pipeline_tag,
                    library=library,
                    license=license,
                    gated=gated,
                    sort_by=sort_by,
                    first=first,
                ),
                after=after,
                audience=audience,
            )
        except ValueError as exc:
            raise _bad_input(exc) from exc

    @strawberry.field
    def astrolift_hugging_face_model(
        self, info: Info, repo_id: str, revision: str | None = None
    ) -> HuggingFaceModelResult:
        _catalogue_audience(info)
        try:
            return model_detail(repo_id, revision)
        except ValueError as exc:
            raise _bad_input(exc) from exc
