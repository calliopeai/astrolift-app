"""Admin-only local import API; write grants are explicit private capabilities."""

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType, PageType, clamp_limit, failure, keyset_page, success
from astrolift_identity.scopes import identity_organization_scope
from astrolift_services import local_model_artifacts as service
from astrolift_services.models.local_model_artifact import LocalModelArtifact
from astrolift_services.schema.model_mutation_audit import model_mutation_audit
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from providers._sdk.local_model_artifact import ArtifactStoreUnavailable


@strawberry.type(name="LocalModelArtifact")
class LocalModelArtifactType:
    id: GUID
    name: str
    version: int
    state: str
    manifest_sha256: str
    file_count: int
    size_bytes: str


def artifact_to_type(row):
    return LocalModelArtifactType(
        id=GUID(str(row.guid)),
        name=row.name,
        version=row.version,
        state=row.state,
        manifest_sha256=row.manifest_sha256,
        file_count=len(row.manifest),
        size_bytes=str(sum(file["size_bytes"] for file in row.manifest)),
    )


@strawberry.input
class LocalModelFileInput:
    name: str
    size_bytes: str
    sha256: str


@strawberry.input
class BeginLocalModelArtifactInput:
    organization_id: GUID
    name: str
    files: list[LocalModelFileInput]


@strawberry.input
class LocalModelArtifactIdentityInput:
    id: GUID
    expected_version: int


@strawberry.type
class LocalModelUploadHeader:
    name: str
    value: str


@strawberry.type
class LocalModelFileUpload:
    name: str
    size_bytes: str
    upload_url: str
    headers: list[LocalModelUploadHeader]


@strawberry.type
class LocalModelUploadAuthorization:
    artifact: LocalModelArtifactType
    files: list[LocalModelFileUpload]
    expires_in_seconds: int = 900


def _failure(error):
    if isinstance(error, ArtifactStoreUnavailable):
        return failure("NOT_CONFIGURED", str(error))
    return failure("PRECONDITION", str(error))


@strawberry.type
class ModelArtifactsQuery:
    @strawberry.field
    @require_permission(Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def astrolift_local_model_artifacts_page(
        self, info: Info, search: str | None = None, limit: int = 25, after: str | None = None
    ) -> PageType[LocalModelArtifactType]:
        org = service.import_authority(request=getattr(info.context, "request", None))
        rows = LocalModelArtifact.objects.filter(organization=org)
        if search:
            rows = rows.filter(name__icontains=search[:128])
        return keyset_page(rows, limit=clamp_limit(limit), cursor=after).map(artifact_to_type)


def artifact_audit_metadata(result):
    row = result.data if result.ok else None
    if isinstance(row, LocalModelUploadAuthorization):
        row = row.artifact
    return {"artifact_id": str(row.id), "state": row.state, "version": row.version} if row else None


@strawberry.type
class ModelArtifactsMutation:
    @strawberry.mutation
    @model_mutation_audit(action="model.local_artifact.begin", extras=artifact_audit_metadata)
    @require_permission(Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def begin_local_model_artifact(
        self, info: Info, input: BeginLocalModelArtifactInput
    ) -> MutationResultType[LocalModelArtifactType]:
        try:
            if any(
                not file.size_bytes.isascii() or not file.size_bytes.isdecimal() or len(file.size_bytes) > 12
                for file in input.files
            ):
                raise ValueError("Model file byte sizes must be bounded decimal strings.")
            row = service.begin_artifact(
                organization_id=str(input.organization_id),
                request=getattr(info.context, "request", None),
                name=input.name,
                files=[
                    {"name": file.name, "sha256": file.sha256, "size_bytes": int(file.size_bytes)}
                    for file in input.files
                ],
            )
            return success(artifact_to_type(row))
        except (ValueError, ArtifactStoreUnavailable) as error:
            return _failure(error)

    @strawberry.mutation
    @model_mutation_audit(action="model.local_artifact.authorize", extras=artifact_audit_metadata)
    @require_permission(Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def authorize_local_model_uploads(
        self, info: Info, input: LocalModelArtifactIdentityInput
    ) -> MutationResultType[LocalModelUploadAuthorization]:
        try:
            row, uploads = service.artifact_uploads(
                input.id, input.expected_version, request=getattr(info.context, "request", None)
            )
            return success(
                LocalModelUploadAuthorization(
                    artifact=artifact_to_type(row),
                    files=[
                        LocalModelFileUpload(
                            name=file["name"],
                            size_bytes=str(file["size_bytes"]),
                            upload_url=file["url"],
                            headers=[
                                LocalModelUploadHeader(name=name, value=value)
                                for name, value in file["headers"].items()
                            ],
                        )
                        for file in uploads
                    ],
                )
            )
        except (ValueError, ArtifactStoreUnavailable) as error:
            return _failure(error)

    @strawberry.mutation
    @model_mutation_audit(action="model.local_artifact.finalize", extras=artifact_audit_metadata)
    @require_permission(Permission.ORG_UPDATE, scope=identity_organization_scope(Permission.ORG_UPDATE))
    @tenant_scoped()
    def finalize_local_model_artifact(
        self, info: Info, input: LocalModelArtifactIdentityInput
    ) -> MutationResultType[LocalModelArtifactType]:
        try:
            return success(
                artifact_to_type(
                    service.finalize_artifact(
                        input.id, input.expected_version, request=getattr(info.context, "request", None)
                    )
                )
            )
        except (ValueError, ArtifactStoreUnavailable) as error:
            return _failure(error)
