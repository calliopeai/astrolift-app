"""Upload mutations migrated from Graphene to Strawberry.

Includes PreSignedUrlImageUploadMutation, ConfirmPreSignedUrlImageUploadMutation,
DataImportFileUploadMutation, ProcessFileMutation, FileUploadMutation,
and ProfileImageFieldUploadMutation.
"""

from __future__ import annotations

from typing import List, Optional

import logging
from datetime import date
from uuid import UUID, uuid4

import strawberry
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.utils import timezone
from graphql import GraphQLError
from strawberry.types import Info

from core.models import Profile, SharedDirectory, SharedFile
from core.models.process import DataProcess, DataProcessEntity, EntityType, FileType, ProcessStatus
from core.models.upload import FileUpload, Upload
from core.schema.common import GlobalIDUtils
from core.schema.types.upload import FileUploadType as StrawberryFileUploadType
from core.schema.types.upload import UploadType as StrawberryUploadType
from core.systems import AwsProcessSystem

# Optional import - Domain-specific functionality
try:
    from domain_app.models import Employee

    HAS_DOMAIN_APP = True
except ImportError:
    Employee = None
    HAS_DOMAIN_APP = False

logger = logging.getLogger(__name__)


def _upload_organization(info):
    """Uploads use legacy organizations; token organization PKs are independent."""
    from astrolift_identity.api_tokens import SCOPE_ADMIN, get_current_api_token, has_scope
    from core.permissions import is_platform_operator
    from organization.models import OrganizationMember

    user = info.context.user
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        raise GraphQLError("Not authorized to modify uploads.")
    profile = getattr(user, "profile", None)
    organization = profile.organization() if profile is not None else None
    if organization is None or organization.deleted_at is not None or (
        not is_platform_operator(user)
        and not OrganizationMember.objects.filter(
            organization=organization, member=user, is_active=True, deleted_at__isnull=True
        ).exists()
    ):
        raise GraphQLError("Not authorized to modify uploads.")
    token = get_current_api_token()
    if token is not None and (
        token.organization.guid != organization.guid or not has_scope(token, SCOPE_ADMIN)
    ):
        raise GraphQLError("Not authorized to modify uploads.")
    return organization


def _resolve_confirmable_upload(info, upload_id, public_url) -> Upload:
    from core.permissions import require_platform_operator

    organization = _upload_organization(info)
    if not upload_id and not public_url:
        raise GraphQLError("Requires either an upload id or a public URL to complete the update/confirmation.")

    uploads = Upload.objects.filter(organization=organization, deleted_at__isnull=True)
    if upload_id:
        pk = GlobalIDUtils.get_pk_flexible(upload_id, expected_type="UploadType")
        uploads = uploads.filter(pk=pk) if pk is not None else uploads.none()
    if public_url:
        uploads = uploads.filter(public_url=public_url)
    upload = uploads.first()
    if upload is None:
        raise GraphQLError("Upload not found.")
    if upload.created_by_id != info.context.user.pk:
        require_platform_operator(info.context.user)
    return upload


def _resolve_upload(info: Info, global_id: strawberry.ID) -> Upload:
    """Resolve an Upload global id through the type's scoped queryset.

    The scoping is the point. `UploadType.get_queryset` applies
    `with_view_permission_info(info)`, which is the mechanism the rest of
    the upload surface uses; resolving straight off `Upload.objects` would
    return any tenant's row, since the id is globally unique. That is the
    leak `core/tests/test_tenancy_guardrail_byid.py` exists to stop, and it
    flagged exactly that on the first draft of this fix.
    """
    pk = GlobalIDUtils.get_pk_flexible(global_id, expected_type="UploadType")
    if pk is None:
        raise GraphQLError("Not an upload id.")
    scoped = StrawberryUploadType.get_queryset(Upload.objects.all(), info)
    upload = scoped.filter(pk=pk, organization=_upload_organization(info), deleted_at__isnull=True).first()
    if upload is None:
        # Indistinguishable from "not visible to you", deliberately: telling
        # a caller a row exists in another tenant is the same leak in words.
        raise GraphQLError("Upload not found.")
    return upload


def _resolve_file_upload(info: Info, global_id: strawberry.ID) -> FileUpload:
    """Resolve a FileUpload global id through its own scoped queryset."""
    pk = GlobalIDUtils.get_pk_flexible(global_id, expected_type="FileUploadType")
    if pk is None:
        raise GraphQLError("Not a file upload id.")
    scoped = StrawberryFileUploadType.get_queryset(FileUpload.objects.all(), info)
    file_upload = scoped.filter(
        pk=pk,
        created_by=info.context.user,
        upload__organization=_upload_organization(info),
        deleted_at__isnull=True,
        upload__deleted_at__isnull=True,
    ).first()
    if file_upload is None:
        raise GraphQLError("File upload not found.")
    return file_upload


def _resolve_process(info: Info, global_id: strawberry.ID) -> DataProcess:
    """Resolve a DataProcess global id, scoped to the caller's uploads.

    `DataProcess` carries no organization column, so there is no org clause
    to add; it is reached through the upload it belongs to. Narrowing by
    `created_by` keeps a caller-supplied id from addressing someone else's
    process, which is the same protection an org clause would give here.
    """
    pk = GlobalIDUtils.get_pk_flexible(global_id, expected_type="DataProcessType")
    if pk is None:
        raise GraphQLError("Not a process id.")
    mine = DataProcess.objects.filter(
        created_by=info.context.user, organization=_upload_organization(info), deleted_at__isnull=True
    )
    process = mine.filter(pk=pk).first()
    if process is None:
        raise GraphQLError("Process not found.")
    return process


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

UploadLocationEnum = strawberry.enum(Upload.Location, name="UploadLocation")
EntityTypeEnum = strawberry.enum(EntityType, name="EntityType")
ProfileImageFieldEnum = strawberry.enum(Profile.ImageField, name="ProfileImageField")


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


@strawberry.type
class PreSignedUrlUploadResult:
    pre_signed_url: Optional[str] = None
    public_url: Optional[str] = None
    file_url: Optional[str] = None
    uuid: Optional[UUID] = None


@strawberry.type
class ConfirmUploadResult:
    ack: bool


@strawberry.type
class DataImportUploadResult:
    pre_signed_url: Optional[str] = None
    public_url: Optional[str] = None
    id: Optional[strawberry.ID] = None


@strawberry.type
class ProcessFileResult:
    errors: Optional[int] = None
    process_id: Optional[strawberry.ID] = None
    successful: Optional[int] = None
    status: Optional[str] = None


@strawberry.type
class FileUploadResult:
    pre_signed_url: Optional[str] = None
    public_url: Optional[str] = None
    id: Optional[strawberry.ID] = None


@strawberry.type
class ProfileImageFieldUploadResult:
    upload: Optional[StrawberryUploadType] = None


# ---------------------------------------------------------------------------
# Shared create_upload helper (mirrors PreSignedUrlImageUploadMutation.create_upload)
# ---------------------------------------------------------------------------


def _exists_global_id_guard(info, global_id: str):
    """Ensure the entity referenced by global_id exists."""
    type_name, pk = GlobalIDUtils.from_global_id(global_id)
    obj = GlobalIDUtils.find_object_by_global_id(global_id, raise_not_found=True)
    return type_name, pk


def create_upload(
    info,
    global_id: str,
    mimetype: str,
    uuid: Optional[UUID] = None,
    location: Optional[Upload.Location] = None,
    metadata: Optional[dict] = None,
    upload: Optional[Upload] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Upload:
    """Create an upload or retry the caller's live row in the active organization."""
    organization = _upload_organization(info)
    _exists_global_id_guard(info, global_id)
    own_uploads = Upload.objects.filter(
        organization=organization, created_by=info.context.user, deleted_at__isnull=True
    )
    upload_location: Upload.Location = location and Upload.Location(location) or Upload.Location.STATIC
    uuid = uuid or (upload and upload.id) or uuid4()
    path = Upload.generate_path(
        upload_location,
        organization=organization,
        target_global_id=global_id,
        uuid=uuid,
        mimetype=mimetype,
    )
    public_url = Upload.generate_pre_signed_url_for_get(path, content_type=mimetype)
    if upload_location == Upload.Location.PUBLIC:
        public_url = public_url.split("?")[0]
    if upload is None:
        pre_signed_url = Upload.generate_pre_signed_url_for_put(path, content_type=mimetype)
        try:
            upload, _created = own_uploads.update_or_create(
                id=uuid,
                defaults=dict(
                    pre_signed_url=pre_signed_url,
                    public_url=public_url,
                    content_type=mimetype,
                    target_global_id=global_id,
                    created_by=info.context.user,
                    organization=organization,
                    metadata=metadata,
                    location=upload_location.value,
                    path=path,
                    name=name,
                    description=description,
                ),
            )
        except IntegrityError as exc:
            raise GraphQLError("Not authorized to reuse the requested upload id.") from exc
    else:
        upload = own_uploads.filter(pk=upload.pk).first()
        if upload is None:
            raise GraphQLError("Upload not found.")
        upload.metadata = metadata
        upload.save()
    return upload


def _assert_caller_owns_target(info, instance) -> None:
    """Deny issuing a presigned URL / attaching an upload to a target the
    caller does not own (#1193).

    ``pre_signed_url_image_upload`` resolves an arbitrary ``(model, pk)`` from
    a caller-supplied global id and — via ``owner_container_property`` — can
    write an FK/M2M on that row. With no ownership check any authenticated
    user could mint an upload against, and mutate, a row in another tenant.

    Organization IDs belong to distinct model tables, so tenant targets
    must match the legacy organization's GUID. Modern targets additionally
    require the operator; global personal targets retain their owner check.
    """
    from core.permissions import is_platform_operator, require_platform_operator
    from organization.models import Organization as LegacyOrganization

    user = info.context.user
    organization = _upload_organization(info)
    target_org = instance if isinstance(instance, LegacyOrganization) else (
        getattr(instance, "organization", None) if hasattr(instance, "organization_id") else None
    )
    if target_org is not None:
        if target_org.deleted_at is not None or target_org.guid != organization.guid:
            raise GraphQLError("Not authorized to upload to the requested target.")
        if not isinstance(target_org, LegacyOrganization):
            require_platform_operator(user)
        return
    if hasattr(instance, "organization_id"):
        raise GraphQLError("Not authorized to upload to the requested target.")
    if is_platform_operator(user):
        require_platform_operator(user)
        return

    for owner_attr in ("user_id", "created_by_id"):
        owner_id = getattr(instance, owner_attr, None)
        if owner_id is not None and owner_id == user.id:
            return

    raise GraphQLError("Not authorized to upload to the requested target.")


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


@strawberry.type
class UploadMutations:
    @strawberry.mutation(
        description="Get a pre-signed URL for uploading an image or file. "
        "Optionally attach it to an entity via owner_container_property."
    )
    def pre_signed_url_image_upload(
        self,
        info: Info,
        global_id: strawberry.ID,
        mimetype: str,
        uuid: UUID | None = None,
        metadata: strawberry.scalars.JSON | None = None,
        location: UploadLocationEnum | None = None,
        description: str | None = None,
        name: str | None = None,
        owner_container_property: str | None = None,
    ) -> PreSignedUrlUploadResult:
        _upload_organization(info)
        # Ownership gate (#1193): the target (model, pk) is resolved from a
        # caller-supplied global id and — via owner_container_property — can be
        # mutated below. Verify the caller owns the target BEFORE minting a
        # presigned URL or attaching anything to it.
        target = GlobalIDUtils.find_object_by_global_id(global_id, raise_not_found=True)
        _assert_caller_owns_target(info, target)

        upload = create_upload(
            info,
            global_id,
            mimetype,
            uuid=uuid,
            location=location,
            metadata=metadata,
            name=name,
        )

        model_name, pk = GlobalIDUtils.from_global_id(global_id)
        model_name_lower: str = model_name.replace("Type", "").lower()

        ct = ContentType.objects.get(model=model_name_lower)
        model = ct.model_class()

        if owner_container_property:
            if not hasattr(model, owner_container_property):
                raise ValueError(
                    f"Property {owner_container_property} is not a member of type {model_name_lower}"
                )

            match type(model._meta.get_field(owner_container_property)).__name__:
                case "ManyToManyField" if model is SharedDirectory:
                    if name is None:
                        raise ValueError(f"Property {name} is required uploading a file to Shared Directory")
                    shared_directory: SharedDirectory = model.objects.filter(pk=pk).first()
                    shared_file = SharedFile(
                        file=upload,
                        parent=shared_directory,
                        created_by=info.context.user,
                    )
                    shared_file.clean()
                    shared_file.save()
                case "ManyToManyField":
                    owner_model = model.objects.filter(pk=pk).first()
                    target_property = getattr(owner_model, owner_container_property)
                    target_property.add(upload.id)
                    owner_model.save()
                case "ForeignKey":
                    owner_model = model.objects.filter(pk=pk).first()
                    setattr(owner_model, owner_container_property + "_id", upload.id)
                    owner_model.save()
                case _:
                    raise ValueError(
                        f"Created upload cannot be attach to target property {owner_container_property}"
                    )

        return PreSignedUrlUploadResult(
            public_url=upload.public_url,
            file_url=upload.public_url,
            pre_signed_url=upload.pre_signed_url,
            uuid=upload.id,
        )

    @strawberry.mutation(
        description="Confirm or update a previously uploaded file. Set delete=true to soft-delete the upload."
    )
    def confirm_pre_signed_url_image_upload(
        self,
        info: Info,
        public_url: Optional[str] = None,
        metadata: Optional[strawberry.scalars.JSON] = None,
        delete: Optional[bool] = None,
        upload_id: Optional[strawberry.ID] = None,
        expiration_date: Optional[date] = None,
    ) -> ConfirmUploadResult:
        upload = _resolve_confirmable_upload(info, upload_id, public_url)

        upload.updated_by = info.context.user
        upload.updated_at = timezone.now()
        upload.metadata = metadata or upload.metadata

        if expiration_date is not None:
            upload.employee_document_upload.update(expiration_date=expiration_date)
        if delete:
            upload.deleted_by = info.context.user
            upload.deleted_at = timezone.now()
            logger.info(f"Marking upload with id {upload.id} as deleted")

        upload.save()
        return ConfirmUploadResult(ack=True)

    @strawberry.mutation(description="Upload a data import file and get a pre-signed URL.")
    def upload_text_file(
        self,
        info: Info,
        mimetype: str,
        metadata: strawberry.scalars.JSON | None = None,
    ) -> DataImportUploadResult:
        _upload_organization(info)
        if not HAS_DOMAIN_APP:
            raise GraphQLError("Data import feature requires domain app")
        if mimetype not in FileType.labels:
            raise GraphQLError("Invalid mimetype, must be one of {}".format(", ".join(FileType.labels)))

        employee: Employee = Employee.objects.filter(
            membership__member_id=info.context.user.id,
            membership__is_active=True,
        ).first()

        if not employee:
            raise GraphQLError("No active employee found for current user")

        upload = create_upload(
            info=info,
            global_id=employee.global_id(),
            mimetype=mimetype,
            location=Upload.Location.STATIC,
            metadata=metadata,
        )
        employee.import_process_documents.add(upload.id)
        employee.save()

        return DataImportUploadResult(
            public_url=upload.public_url,
            pre_signed_url=upload.pre_signed_url,
            id=upload.global_id,
        )

    @strawberry.mutation(description="Process a previously uploaded data import file.")
    def process_file(
        self,
        info: Info,
        entity_type: EntityTypeEnum,
        uploaded_file_id: strawberry.ID,
        process_id: strawberry.ID | None = None,
    ) -> ProcessFileResult:
        _upload_organization(info)
        # Two broken imports on these two lines, not one: the package root
        # exports no `UploadType`, and `core.schema.process` does not exist
        # as a module at all. This mutation could never run.
        upload = _resolve_upload(info, uploaded_file_id)
        obj = upload.get_as_object()

        if process_id:
            # `DataProcessType` has no module to come from. Resolve the id
            # to the model directly, the same way the upload above does.
            process = _resolve_process(info, process_id)
        else:
            process = AwsProcessSystem.load_file_data(obj, upload.id, entity_type=entity_type)

        process = AwsProcessSystem.process(process, info.context.user)
        error_count = DataProcessEntity.objects.filter(
            process=process,
            status__exact=ProcessStatus.FAILED,
        ).count()
        success_count = DataProcessEntity.objects.filter(
            process=process,
            status__exact=ProcessStatus.DONE,
        ).count()

        return ProcessFileResult(
            process_id=process.global_id,
            status=process.status,
            errors=error_count,
            successful=success_count,
        )

    @strawberry.mutation(
        description="Upload a file and get a pre-signed URL. Creates a FileUpload wrapper around the Upload."
    )
    def file_upload(
        self,
        info: Info,
        mimetype: str,
        file_upload_gid: strawberry.ID | None = None,
        metadata: strawberry.scalars.JSON | None = None,
        name: str | None = None,
        description: str | None = None,
        is_public: bool | None = False,
    ) -> FileUploadResult:
        _upload_organization(info)
        # `core.schema.upload` does not exist; the module is
        # `core.schema.types.upload`, whose types carry no `get_object`.
        if file_upload_gid:
            file_upload = _resolve_file_upload(info, file_upload_gid)
        else:
            file_upload = FileUpload.objects.create(
                created_by=info.context.user,
            )
            file_upload_gid = file_upload.global_id

        upload = create_upload(
            info=info,
            global_id=file_upload_gid,
            mimetype=mimetype,
            location=Upload.Location.PUBLIC if is_public else Upload.Location.STATIC,
            metadata=metadata,
            name=name,
            description=description,
        )

        file_upload.upload = upload
        file_upload.save()

        return FileUploadResult(
            id=upload.global_id,
            public_url=upload.public_url,
            pre_signed_url=upload.pre_signed_url,
        )

    @strawberry.mutation(
        description="Upload an image for a specific profile image field (avatar, signature)."
    )
    def profile_image_field_upload(
        self,
        info: Info,
        global_id: strawberry.ID,
        mimetype: str,
        field: ProfileImageFieldEnum | None = None,
        metadata: strawberry.scalars.JSON | None = None,
    ) -> ProfileImageFieldUploadResult:
        from core.schema.legacy_access import require_account_access

        require_account_access(info, write=True)
        pk = GlobalIDUtils.get_pk_flexible(global_id, expected_type="ProfileType")
        profile_original = Profile.objects.filter(
            pk=pk, user=info.context.user, deleted_at__isnull=True
        ).first()
        # Ownership check (#1193): a profile-image upload is a self-service edit
        # of the caller's OWN profile (avatar / signature). Without this gate any
        # authenticated user could upload to — and, for whitelisted fields,
        # overwrite the avatar/signature of — another user's profile by supplying
        # its global id. Mirrors notification_read's owner check.
        if profile_original is None:
            raise GraphQLError("Profile does not belong to user")
        _upload_organization(info)
        whitelist: list[str] = Profile.whitelist_fields()

        if field.value in whitelist:
            # Skip approval request flow
            return ProfileImageFieldUploadResult(
                upload=self._save_profile_upload(
                    field,
                    global_id,
                    info,
                    metadata,
                    mimetype,
                    profile_original,
                )
            )

        original, draft = Profile.objects.get_draft(profile_original)

        # ``domain_app`` (the scaffold's approval workflow) is not part of
        # Astrolift, so the draft takes the upload directly.
        upload = self._save_profile_upload(
            field,
            global_id,
            info,
            metadata,
            mimetype,
            draft,
        )

        return ProfileImageFieldUploadResult(upload=upload)

    @staticmethod
    def _save_profile_upload(field, global_id, info, metadata, mimetype, profile):
        """Save an upload for a profile image field (avatar or signature)."""
        existing_upload: Optional[Upload] = None
        match field:
            case Profile.ImageField.AVATAR:
                existing_upload = profile.avatar
            case Profile.ImageField.SIGNATURE:
                existing_upload = profile.signature

        if existing_upload and existing_upload.content_type != mimetype:
            existing_upload = None

        upload = create_upload(
            info=info,
            global_id=global_id,
            mimetype=mimetype,
            location=Upload.Location.PUBLIC,
            metadata=metadata,
            upload=existing_upload,
        )

        match field:
            case Profile.ImageField.AVATAR:
                profile.avatar = upload
                if profile.user_id:
                    profile.draft.avatar = upload
            case Profile.ImageField.SIGNATURE:
                profile.signature = upload
                if profile.user_id:
                    profile.draft.signature = upload

        profile.save()
        if profile.user_id:
            profile.draft.save()

        return upload
