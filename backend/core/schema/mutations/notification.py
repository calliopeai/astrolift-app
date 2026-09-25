"""Notification mutations migrated from Graphene to Strawberry."""
from __future__ import annotations

import logging

import strawberry
from django.utils import timezone
from graphql import GraphQLError
from strawberry.types import Info

from core.schema.common import GlobalIDUtils, MutationResult, ValidationError

logger = logging.getLogger(__name__)


@strawberry.type
class NotificationMutations:

    @strawberry.mutation(description="Create or update a notification via NotificationSerializer.")
    def notification(self, info: Info, input: strawberry.scalars.JSON) -> MutationResult:
        from core.serializers.notification import NotificationSerializer

        if input.get('guid'):
            # `guid` names an existing row for an update. `Notification` has
            # no `guid` field (only `BaseCoreModel` subclasses do) and
            # `NotificationSerializer` is a plain ModelSerializer with
            # `user`/`subject`/`message` all writable, so letting this
            # through would let the caller re-address or rewrite a
            # notification they only own as its recipient (#1949). The
            # retired `FieldRestrictedSerializer` used to raise on this same
            # payload (KeyError on the undeclared `guid` field) before any
            # permission check ran, so updates never succeeded for anyone;
            # refuse them the same way, without the crash. Creates (no
            # `guid`) are unaffected.
            return MutationResult(
                ok=False,
                errors=[ValidationError(field='guid', messages=['Updating a notification is not supported.'])],
            )

        # A notification names its recipient (`user`), across orgs: a caller
        # may address only themselves, and addressing someone else is the
        # platform operator's (#1990). Nothing in the web app calls this.
        from core.permissions import require_platform_operator

        request = getattr(info.context, 'request', None)
        caller = getattr(request, 'user', None) or getattr(info.context, 'user', None)
        if str(input.get('user') or '') != str(getattr(caller, 'pk', '')):
            require_platform_operator(caller)
        serializer = NotificationSerializer(
            data=input, partial=True, context={'request': info.context.request}
        )
        if serializer.is_valid():
            serializer.save()
            return MutationResult.success()
        else:
            return MutationResult.from_serializer_errors(serializer.errors)

    @strawberry.mutation(description="Mark a notification as read.")
    def notification_read(self, info: Info, gid: strawberry.ID) -> bool:
        from core.models import Notification, NotificationStatus

        # `from core.schema import NotificationType` raised ImportError on
        # this line: the package root exports no type names (the real type
        # is `core.schema.types.notification.NotificationType`, and it has
        # no `get_object` -- that was a Graphene-era classmethod that did
        # not survive the Strawberry migration). So this mutation had never
        # succeeded. Same defect and same remedy as #1567.
        #
        # `expected_type` is load-bearing rather than decorative: resolving
        # the id without it would accept any model's global id and then
        # write `status` onto whatever came back.
        pk = GlobalIDUtils.get_pk_flexible(gid, expected_type='NotificationType')
        if pk is None:
            raise GraphQLError('Not a notification id')
        notification = Notification.objects.filter(pk=pk).first()
        if notification is None:
            raise GraphQLError('Notification not found')
        if notification.user != info.context.user:
            raise ValueError('Notification does not belong to user')

        notification.status = NotificationStatus.READ
        notification.status_date = timezone.now()
        notification.save()

        return True
