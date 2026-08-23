"""User-related mutations migrated from Graphene to Strawberry."""
from __future__ import annotations

import logging
from typing import Optional

import strawberry
from django.conf import settings
from django.contrib.auth import SESSION_KEY as AUTH_SESSION_KEY
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from graphql import GraphQLError
from strawberry.types import Info

from core.models import Profile, SignRequest
from core.schema.mutations.common import UtilityForm
from core.schema.common import GlobalIDUtils, MutationResult
from core.schema.mutations.base import resolve_instance_from_id
from core.schema.types.user import SignRequestType as StrawberrySignRequestType, UserType as StrawberryUserType

try:
    from django.contrib.auth import HASH_SESSION_KEY as AUTH_HASH_SESSION_KEY
except ImportError:
    AUTH_HASH_SESSION_KEY = '_auth_user_hash'

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: resolve user from global ID using Graphene registry
# (many mutations reference Graphene's UserType.get_object / SignRequestType)
# ---------------------------------------------------------------------------

def _get_user_object(info, global_id: str, raise_not_found: bool = True) -> User:
    """Resolve a User from a relay global ID.

    ``core.schema.user`` does not exist; these helpers have raised ImportError
    on every call since the Graphene migration (#1590). Resolution is a plain
    pk lookup because ``UserType`` carries no ``get_queryset`` hook to scope
    through, so every caller owns its own gate. All five were audited in #1593:

    * ``switch_user`` - ``_may_switch_to`` (superuser or shared switch group)
    * ``profile_request_pwd_change`` - ``P.PROFILE_CHANGE_RESET_PASSWORD_USERS``
    * ``profile_request_delete_user`` - ``P.PROFILE_DELETE_USERS``
    * ``pin_transaction`` - ``Profile.authenticate`` checks the PIN against the
      *proxy* user's own hash, so acting as them requires their credential
    * ``sign_request_user`` - ``P.SIGNREQUEST_CHANGE_SIGN``, checked against the
      target. That grants "may be asked to sign", not access to the asker's
      data, and is pre-existing semantics rather than a gate added here.
    """
    # tenancy: User is a core global model with no organization column, so
    # there is no org clause to add here. Authorization lives at each call
    # site, enumerated in the docstring above, not in this fetch.
    pk = GlobalIDUtils.get_pk_flexible(global_id, expected_type='UserType')
    user = User.objects.filter(pk=pk).first() if pk else None
    if user is None and raise_not_found:
        raise GraphQLError(f'Object id User:{global_id} not found')
    return user


def _get_sign_request_object(info, global_id: str, raise_not_found: bool = True) -> SignRequest:
    """Resolve a SignRequest from a relay global ID.

    The Graphene ``SignRequestType.get_object`` this replaced carried its own
    security (``sign_request_user`` says so in a comment, and deliberately
    avoided it). That module does not exist, so both callers have raised
    ImportError rather than enforcing anything. The enforcement they get back
    is the model's, which is stricter than a queryset filter would be:

    * ``sign_request_sign`` - ``SignRequest.sign`` checks
      ``P.SIGNREQUEST_CHANGE_SIGN``, that the caller is in
      ``users_allowed_to_sign()``, that they were actually requested, and that
      an active PinTransaction exists.
    * ``sign_request_cancel`` - ``SignRequest.cancel`` checks
      ``P.SIGNREQUEST_CHANGE_CANCEL``.
    """
    # tenancy: SignRequest is a core global model with no organization column;
    # affiliation, not org membership, is what bounds visibility (see the model
    # docstring). Every consumer permission-checks on its first line, so there
    # is no org clause to add to this fetch.
    pk = _get_sign_request_pk(global_id)
    sign_request = SignRequest.objects.filter(pk=pk).first() if pk else None
    if sign_request is None and raise_not_found:
        raise GraphQLError(f'Object id SignRequest:{global_id} not found')
    return sign_request


def _get_sign_request_pk(global_id: str) -> str:
    """Extract the PK from a SignRequest global ID."""
    return GlobalIDUtils.get_pk_flexible(global_id, expected_type='SignRequestType')


def _may_switch_to(user: User, other_user: User) -> bool:
    """Who may impersonate whom.

    ``Profile.switch_group`` is the rule the data model already states -
    "Users in the same group are allowed to switch between them" - and until
    now nothing read it. Membership is the gate; superusers keep an override
    because operator support work is what impersonation is for.

    A null ``switch_group`` is not a group. Two users who have both been left
    ungrouped must not be able to reach each other just because their columns
    match.
    """
    if user.pk == other_user.pk:
        return True
    if user.is_superuser:
        return True
    group_id = getattr(getattr(user, 'profile', None), 'switch_group_id', None)
    if group_id is None:
        return False
    other_group_id = getattr(getattr(other_user, 'profile', None), 'switch_group_id', None)
    return group_id == other_group_id


# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------

@strawberry.input
class ProfileInput:
    avatar: Optional[str] = None


@strawberry.input
class UserInput:
    id: Optional[strawberry.ID] = None
    username: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    profile: Optional[ProfileInput] = None


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------

@strawberry.type
class LoginResult:
    user: Optional[StrawberryUserType]


@strawberry.type
class SwitchUserResult:
    user: Optional[StrawberryUserType]


@strawberry.type
class UpsertUserResult:
    instance: Optional[StrawberryUserType]


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

@strawberry.type
class UserMutations:

    @strawberry.mutation(description="Authenticate a user with username and password.")
    def login(self, info: Info, username: str, password: str) -> LoginResult:
        user = authenticate(request=info.context.request, username=username, password=password)
        if not user:
            raise PermissionDenied('User not found')

        login(info.context.request, user)

        if hasattr(info.context, "user"):
            # Clear cached_property so subsequent access sees the new user
            try:
                del info.context.__dict__['user']
            except KeyError:
                pass

        return LoginResult(user=user)

    @strawberry.mutation(description="Logout the current user.")
    def logout(self, info: Info) -> bool:
        logout(info.context.request)
        return True

    @strawberry.mutation(description="Switch the active user (impersonation).")
    def switch_user(self, info: Info, id: strawberry.ID) -> SwitchUserResult:
        user: User = info.context.user
        request = info.context.request

        if id == '':
            # Return-to-self: the session already proved this identity when it
            # switched away, so it needs no further gate.
            if 'MAIN_USER_PK' not in request.session:
                return SwitchUserResult(user=info.context.user)
            other_user = User.objects.get(pk=request.session['MAIN_USER_PK'])
        else:
            other_user = _get_user_object(info, id, raise_not_found=True)
            if not _may_switch_to(user, other_user):
                # Indistinguishable from "no such user", deliberately:
                # confirming an id exists is its own disclosure.
                raise GraphQLError(f'Object id User:{id} not found')

        # Clear cached user so context sees the switched user
        try:
            del info.context.__dict__['user']
        except KeyError:
            pass

        request.session[AUTH_SESSION_KEY] = other_user.pk
        request.session[AUTH_HASH_SESSION_KEY] = other_user.get_session_auth_hash()

        if 'MAIN_USER_PK' not in request.session:
            request.session['MAIN_USER_PK'] = user.pk

        request.session.save()

        return SwitchUserResult(user=other_user)

    @strawberry.mutation(description="Upsert user profile via UtilityForm.apply_forms.")
    def upsert_user(self, info: Info, input: UserInput) -> UpsertUserResult:
        # Convert strawberry input to dict for UtilityForm
        input_data = {}
        if input.id is not None:
            input_data['id'] = input.id
        input_data['username'] = input.username
        if input.first_name is not None:
            input_data['first_name'] = input.first_name
        if input.last_name is not None:
            input_data['last_name'] = input.last_name
        if input.profile is not None:
            profile_data = {}
            if input.profile.avatar is not None:
                profile_data['avatar'] = input.profile.avatar
            input_data['profile'] = profile_data

        # Set the user's global ID as the input ID (same as Graphene version)
        input_data['id'] = GlobalIDUtils.to_global_id('UserType', info.context.user.pk)

        instance = UtilityForm.apply_forms(None, info, input_data)
        return UpsertUserResult(instance=instance)

    @strawberry.mutation(description="Update user profile via ProfileSerializer (restricted).")
    def profile(self, info: Info, input: strawberry.scalars.JSON) -> MutationResult:
        from core.serializers.profile import ProfileSerializer
        fields_provided = input.keys()
        user_id = info.context.user.id

        if 'user' in fields_provided and 'id' in input['user']:
            _, user_id = GlobalIDUtils.from_global_id(input['user']['id'])
            input['user']['id'] = user_id

        instance = Profile.objects.filter(user_id=user_id).first()

        if 'address' in fields_provided and 'state' in input['address']:
            # state enum value already comes as string from Strawberry
            pass

        if 'gender' in fields_provided and hasattr(input['gender'], 'value'):
            input['gender'] = input['gender'].value

        if 'preferred_contact' in fields_provided and hasattr(input['preferred_contact'], 'value'):
            input['preferred_contact'] = input['preferred_contact'].value

        if 'preferred_language' in fields_provided and hasattr(input['preferred_language'], 'value'):
            input['preferred_language'] = input['preferred_language'].value

        kwargs = {'data': input, 'partial': True, 'context': {'request': info.context.request}}
        if instance is not None:
            kwargs['instance'] = instance

        serializer = ProfileSerializer(**kwargs)
        if serializer.is_valid():
            serializer.save()
            return MutationResult.success()
        else:
            return MutationResult.from_serializer_errors(serializer.errors)

    @strawberry.mutation(
        description="Request a password reset email. "
                    "Requires PROFILE_CHANGE_RESET_PASSWORD_USERS permission to send to other users."
    )
    def profile_request_pwd_change(self, info: Info, user_gid: Optional[strawberry.ID] = None) -> bool:
        user = _get_user_object(info, user_gid, raise_not_found=True) if user_gid else info.context.user

        if user != info.context.user:
            from config.roles_gen import P
            P.PROFILE_CHANGE_RESET_PASSWORD_USERS.check(info.context.user, True)

        user.profile.request_reset_password()
        return True

    @strawberry.mutation(
        description="Request deletion of a user account. "
                    "Requires PROFILE_DELETE_USERS permission to delete other users."
    )
    def profile_request_delete_user(self, info: Info, user_gid: Optional[strawberry.ID] = None) -> bool:
        user = _get_user_object(info, user_gid, raise_not_found=True) if user_gid else info.context.user

        if user != info.context.user:
            from config.roles_gen import P
            P.PROFILE_DELETE_USERS.check(info.context.user, True)

        Profile.anonymize_user(user)
        if user == info.context.user:
            logout(info.context.request)

        return True

    @strawberry.mutation(description="Update or set the user's PIN.")
    def pin_update(self, info: Info, pin: str) -> bool:
        user = info.context.user
        user.profile.update_pin(pin)
        return True

    @strawberry.mutation(
        description="Authenticate a PIN transaction. "
                    "The proxy_user parameter allows acting on behalf of another user."
    )
    def pin_transaction(
        self,
        info: Info,
        pin: str,
        proxy_user: Optional[strawberry.ID] = None,
    ) -> bool:
        profile: Profile = info.context.user.profile
        resolved_proxy_user = None
        if proxy_user:
            resolved_proxy_user = _get_user_object(info, proxy_user, raise_not_found=True)

        profile.authenticate(pin, resolved_proxy_user, None)
        return True

    @strawberry.mutation(
        description="Request a sign from a user. "
                    "The user must have SIGNREQUEST_CHANGE_SIGN permission."
    )
    def sign_request_user(
        self,
        info: Info,
        gid: str,
        user_to_request: strawberry.ID,
    ) -> bool:
        # SignRequestType.get_object can not be used because of the security in SignRequestType
        # This assumes everyone can request a sign
        sign_request_pk = _get_sign_request_pk(gid)
        sign_request = SignRequest.objects.filter(pk=sign_request_pk).first()

        if not sign_request:
            raise GraphQLError(f'Object id SignRequest:{gid} not found')

        resolved_user = _get_user_object(info, user_to_request, raise_not_found=True)
        if sign_request.is_rejected:
            sign_request = sign_request.reset_sign_request(resolved_user)

        sign_request.request_user(resolved_user)
        return True

    @strawberry.mutation(
        description="Sign a sign request. Requires SIGNREQUEST_CHANGE_SIGN permission "
                    "and an active PIN transaction. Status must be SIGN_REQUIRED."
    )
    def sign_request_sign(self, info: Info, gid: str) -> bool:
        sign_request = _get_sign_request_object(info, gid, raise_not_found=True)
        sign_request.sign(info.context.user)
        return True

    @strawberry.mutation(
        description="Cancel a sign request. Requires SIGNREQUEST_CHANGE_CANCEL permission."
    )
    def sign_request_cancel(self, info: Info, gid: str, note: str) -> bool:
        sign_request = _get_sign_request_object(info, gid)
        sign_request.cancel(info.context.user, note)
        return True
