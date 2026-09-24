"""Django model permissions no longer authorize app code (#1864).

Four things are pinned here.

* **Guardrail.** No first-party module outside the Django admin imports
  ``config.roles_gen`` / ``config.permissions`` or asks Django whether a
  user holds a model permission. Those two modules are gone; this keeps
  them gone.
* **Equivalence.** Every legacy check that read a Django model permission
  now asks :func:`core.permissions.is_platform_operator`. The matrix below
  runs each old rule (reproduced from the deleted code, against the same
  Django permission rows) and the new code path for the same users, and
  requires the same allow or deny. The answers that change are refusals
  and are asserted explicitly rather than skipped: someone holding Django
  permissions through a legacy organization group (what the issue
  retires), and a deactivated superuser.
* **Coverage.** The model hooks are checked on every model that carries
  them and the field gates on every field that has one, not on a sample.
  The generic delete keeps to the seven models it could delete before.
* **Refusals that used to crash.** Sign requests and proxy-PIN
  authentication named permissions ``config.roles_gen`` never generated,
  so they raised ``AttributeError`` for everyone. They now refuse.

The ``profile`` mutation and the ``/app/core/core/*`` tooling changed on
purpose, not by equivalence; ``test_legacy_surfaces_closed_1864.py``
covers them.
"""

from __future__ import annotations

import ast
import inspect
import itertools
import pathlib
from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.auth.models import Permission as DjangoPermission
from django.core.exceptions import PermissionDenied

from core.models import Address, Profile
from core.permissions import is_platform_operator
from core.schema.common import GlobalIDUtils

User = get_user_model()
BACKEND = pathlib.Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))


# ---------------------------------------------------------------------------
# Guardrail
# ---------------------------------------------------------------------------

# Trees that are not first-party app code: an independently installed
# package, vendored upstream code, and migrations (history, never run as
# authorization).
_SKIP_PARTS = {"providers", "vendor", "migrations", "node_modules", "__pycache__"}

# The Django admin authorizes on Django permissions by design; that is the
# one place the issue keeps them. ``core/views.py`` holds the admin's
# group-and-permission tooling (linked from admin actions) AND
# ``download_file``, an unrelated streaming-export view -- exempting the
# whole file would leave that one unguarded if an offence ever landed
# there (#1949). Only ``admin_tooling`` itself (the gate) and the views it
# decorates are exempt; see ``_admin_tooling_exempt_names``.
_ADMIN_TOOLING_FILE = pathlib.Path("core/views.py")

_RETIRED_MODULES = {"config.roles_gen", "config.permissions"}
_RETIRED_NAMES = {"AbstractPermissions", "FieldRestrictedSerializer", "ModelPermissions", "FieldPermissions"}
_DJANGO_PERMISSION_CALLS = {
    "has_perm",
    "has_perms",
    "has_module_perms",
    "get_all_permissions",
    "get_group_permissions",
    "get_user_permissions",
}
# Django ORM lookups that traverse to a permission relation. Prefixes, not
# exact names: real code spells them ``user_permissions__codename``,
# ``groups__permissions__x``, etc. (#1949).
_PERMISSION_LOOKUP_PREFIXES = ("user_permissions__", "groups__permissions")
_RETIRED_IMPORT_NAMES = {
    "django.contrib.auth.decorators": {"permission_required"},
    "django.contrib.auth.mixins": {"PermissionRequiredMixin"},
}
_RETIRED_IMPORT_MODULES = ("rest_framework.permissions", "rolepermissions")


def _matches_retired_import_module(module: str) -> bool:
    return any(module == m or module.startswith(f"{m}.") for m in _RETIRED_IMPORT_MODULES)


def _admin_tooling_exempt_names(tree: ast.AST) -> frozenset[str]:
    """``core/views.py`` functions allowed to hold the admin's own Django
    permission checks: ``admin_tooling`` (the gate itself) and whatever it
    decorates. Derived from the decorator, not hand-copied, so a new
    ``@admin_tooling`` view is exempt automatically and anything else --
    ``download_file`` included -- is scanned like any other function (#1949).
    """

    exempt = {"admin_tooling"}
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Name) and target.id == "admin_tooling":
                exempt.add(node.name)
    return frozenset(exempt)


def _walk_skipping(tree: ast.AST, exempt_functions: frozenset[str]):
    """Like ``ast.walk``, but does not descend into a ``def`` named in
    ``exempt_functions`` -- the guardrail must not see inside it."""

    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in exempt_functions:
            continue
        stack.extend(ast.iter_child_nodes(node))


def _is_groups_permission_filter(call: ast.Call) -> bool:
    """``<expr>.groups.filter(permissions=...)`` / ``.exclude`` / ``.get`` --
    Django's ``Group.permissions`` read straight through a ``.groups``
    relation. One hop is unambiguous and needs no bare ``permissions=``
    keyword match: that also matches Astrolift's own ``Role.permissions``
    and every ``Permission`` tuple/dataclass field of the same name,
    dozens of times over, across the codebase (#1949)."""

    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in {"filter", "exclude", "get"}
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "groups"
    )


def _app_sources():
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if _SKIP_PARTS & set(rel.parts) or "tests" in rel.parts or rel.name.startswith("test_"):
            continue
        if rel.name == "admin.py":
            continue
        yield rel, path


def _offences(tree: ast.AST, *, exempt_functions: frozenset[str] = frozenset()) -> list[str]:
    found = []
    for node in _walk_skipping(tree, exempt_functions):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = {a.name for a in node.names}
            if node.level and module == "roles_gen":
                found.append("imports roles_gen")
            if module in _RETIRED_MODULES:
                found.append(f"imports {module}")
            if module == "config" and names & {"roles_gen", "permissions"}:
                found.append("imports config.roles_gen / config.permissions")
            if module in _RETIRED_IMPORT_NAMES:
                hit = names & _RETIRED_IMPORT_NAMES[module]
                if hit:
                    found.append(f"imports {sorted(hit)[0]} from {module}")
            if _matches_retired_import_module(module):
                found.append(f"imports {module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _RETIRED_MODULES:
                    found.append(f"imports {alias.name}")
                if _matches_retired_import_module(alias.name):
                    found.append(f"imports {alias.name}")
        elif isinstance(node, ast.Name) and node.id in _RETIRED_NAMES:
            found.append(f"uses {node.id}")
        elif isinstance(node, ast.Attribute):
            # Any reference, not just a call: `f = user.has_perm` hands out
            # the same authority as `user.has_perm(...)` (#1949).
            if node.attr in _DJANGO_PERMISSION_CALLS or node.attr in _RETIRED_NAMES:
                found.append(f"references .{node.attr}")
        elif isinstance(node, ast.Call) and _is_groups_permission_filter(node):
            for kw in node.keywords:
                if kw.arg and (kw.arg == "permissions" or kw.arg.startswith("permissions__")):
                    found.append(f"filters .groups.{node.func.attr}(...) on {kw.arg}")
        elif isinstance(node, ast.keyword) and node.arg and node.arg.startswith(_PERMISSION_LOOKUP_PREFIXES):
            found.append(f"filters on {node.arg}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Dynamic dispatch spells the same name as a plain string:
            # `getattr(user, 'has_perm')`, `importlib.import_module('config.roles_gen')`,
            # and a lookup key built via `**{...}` or `Q((...))` rather than
            # a keyword argument (#1949).
            value = node.value
            if value in _DJANGO_PERMISSION_CALLS:
                found.append(f"string constant names .{value}")
            if value in _RETIRED_MODULES:
                found.append(f"string constant names {value}")
            if any(prefix in value for prefix in _PERMISSION_LOOKUP_PREFIXES):
                found.append(f"string constant references {value}")
    return found


def test_no_app_module_authorizes_on_django_model_permissions():
    offenders = {}
    for rel, path in _app_sources():
        tree = ast.parse(path.read_text(), filename=str(rel))
        exempt = _admin_tooling_exempt_names(tree) if rel == _ADMIN_TOOLING_FILE else frozenset()
        found = _offences(tree, exempt_functions=exempt)
        if found:
            offenders[str(rel)] = sorted(set(found))
    assert offenders == {}, (
        "Django model permissions authorize the Django admin only (#1864). App code asks "
        "core.permissions (a Permission + scope, or is_platform_operator for legacy "
        f"boilerworks surfaces): {offenders}"
    )


def test_the_retired_modules_are_gone():
    assert not (BACKEND / "config" / "roles_gen.py").exists()
    assert not (BACKEND / "config" / "permissions.py").exists()


def test_the_guardrail_sees_an_offence():
    """A scan that reads the wrong thing would pass forever; prove it bites
    on the original shape and on every one #1949's review added -- a bound
    reference, dynamic dispatch through a plain string, the ``**{...}`` /
    ``Q(())`` string-keyed lookup forms, a direct
    ``.groups.filter(permissions=...)``, and the newly retired imports --
    each on its own line so a broken probe names exactly what regressed.
    The last two entries are negative: the same shapes real RBAC code uses
    legitimately (a bare ``permissions=`` keyword, ``login_required``) must
    not trip the guardrail, per #1949's own false-positive sweep.
    """
    probes = {
        "call": (
            "def f(user):\n"
            "    return user.has_perm('app.x') or User.objects.filter(groups__permissions__codename='x')\n",
            ["references .has_perm", "filters on groups__permissions__codename"],
        ),
        "import": ("from config.roles_gen import P\n", ["imports config.roles_gen"]),
        "bound reference": ("f = user.has_perm\n", ["references .has_perm"]),
        "getattr dynamic dispatch": (
            "dyn = getattr(user, 'has_perm')\n",
            ["string constant names .has_perm"],
        ),
        "dynamic module import": (
            "mod = importlib.import_module('config.roles_gen')\n",
            ["string constant names config.roles_gen"],
        ),
        "qualified retired name": (
            "cls = s.FieldRestrictedSerializer\n",
            ["references .FieldRestrictedSerializer"],
        ),
        "user_permissions__ keyword": (
            "User.objects.filter(user_permissions__codename='x')\n",
            ["filters on user_permissions__codename"],
        ),
        "dict-splat lookup key": (
            "User.objects.filter(**{'groups__permissions__x': 1})\n",
            ["string constant references groups__permissions__x"],
        ),
        "Q-tuple lookup key": (
            "User.objects.filter(Q(('groups__permissions__x', 1)))\n",
            ["string constant references groups__permissions__x"],
        ),
        "groups.filter(permissions=)": (
            "user.groups.filter(permissions=p)\n",
            ["filters .groups.filter(...) on permissions"],
        ),
        "groups.exclude(permissions=)": (
            "user.groups.exclude(permissions=p)\n",
            ["filters .groups.exclude(...) on permissions"],
        ),
        "permission_required import": (
            "from django.contrib.auth.decorators import permission_required\n",
            ["imports permission_required from django.contrib.auth.decorators"],
        ),
        "PermissionRequiredMixin import": (
            "from django.contrib.auth.mixins import PermissionRequiredMixin\n",
            ["imports PermissionRequiredMixin from django.contrib.auth.mixins"],
        ),
        "rest_framework.permissions import": (
            "from rest_framework.permissions import IsAuthenticated\n",
            ["imports rest_framework.permissions"],
        ),
        "rolepermissions import": ("import rolepermissions\n", ["imports rolepermissions"]),
        "unrelated permissions= keyword": ("Role.objects.create(permissions=[1, 2])\n", []),
        "unrelated login_required import": (
            "from django.contrib.auth.decorators import login_required\n",
            [],
        ),
    }
    for label, (source, expected) in probes.items():
        assert sorted(_offences(ast.parse(source))) == sorted(expected), label


def test_core_views_exemption_covers_only_admin_tooling_views():
    """#1949: exempting the whole file would leave ``download_file`` --
    an unrelated streaming-export view with no Django permission logic of
    its own -- unguarded if an offence were ever added there. Only
    ``admin_tooling`` and the views it decorates are exempt."""
    path = BACKEND / _ADMIN_TOOLING_FILE
    source = path.read_text()
    tree = ast.parse(source, filename=str(_ADMIN_TOOLING_FILE))
    exempt = _admin_tooling_exempt_names(tree)

    assert "download_file" not in exempt
    assert exempt == {
        "admin_tooling",
        "user_permissions_tree",
        "compare_user_permissions",
        "search_users",
        "compare_user_permissions_data",
        "search_groups",
        "toggle_group_membership",
        "compare_group_permissions_data",
        "toggle_permission_in_group",
    }

    # The file passes the guardrail with the exemption applied (also
    # exercised end to end via `test_no_app_module_authorizes_...` above)...
    assert _offences(tree, exempt_functions=exempt) == []

    # ...but an offence added to the one function the exemption does not
    # cover is still caught, proving the narrowing is real and not just
    # theoretical.
    injected = ast.parse(
        source.replace(
            "def download_file(request):\n",
            "def download_file(request):\n    request.user.has_perm('x')\n",
            1,
        ),
        filename=str(_ADMIN_TOOLING_FILE),
    )
    injected_exempt = _admin_tooling_exempt_names(injected)
    assert _offences(injected, exempt_functions=injected_exempt) == ["references .has_perm"]


# ---------------------------------------------------------------------------
# Equivalence: old rule vs new code, per user
# ---------------------------------------------------------------------------


def _django_permission(app_label: str, model: str, codename: str) -> DjangoPermission:
    """The row the deleted ``AllPermissions.get("<model>.<codename>")`` returned."""
    return DjangoPermission.objects.get(
        content_type__app_label=app_label, content_type__model=model, codename=codename
    )


def _old_model_permission_check(user, app_label: str, model: str, codename: str) -> bool:
    """``AbstractPermissions.check`` / ``.by`` before #1864, without the cache and log.

    Superuser short-circuit, otherwise a Django group that holds the
    permission and hangs off the user's membership in their (legacy)
    active organization.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(
        memberships__organization=user.profile.organization(),
        permissions=_django_permission(app_label, model, codename),
    ).exists()


def _old_django_auth_check(user, *codenames: str) -> bool:
    """``AbstractPermissions.check_django_auth_permission`` before #1864."""
    return bool(user.is_authenticated and all(user.has_perm(c) for c in codenames))


@pytest.fixture
def people():
    from astrolift_identity.models import Organization, Role, RoleBinding
    from astrolift_identity.system_roles import SYSTEM_ROLES
    from organization.models import Organization as LegacyOrganization
    from organization.models.organization import OrganizationMember

    operator = User.objects.create_superuser(username="op-1864", email="op-1864@t.test", password="x")
    inactive_operator = User.objects.create_superuser(
        username="gone-op-1864", email="gone-op-1864@t.test", password="x", is_active=False
    )
    plain = User.objects.create_user(username="plain-1864", email="plain-1864@t.test", password="x")

    # Holds every Astrolift permission in an Astrolift org, and no Django
    # permission: RBAC grants must not leak into the legacy checks.
    owner = User.objects.create_user(username="owner-1864", email="owner-1864@t.test", password="x")
    astro_org = Organization.objects.create(name="Acme 1864", slug="acme-1864")
    slug, level, name, description, perms = next(r for r in SYSTEM_ROLES if r[0] == "org_owner")
    role, _ = Role.objects.update_or_create(
        slug=slug,
        is_system=True,
        organization=None,
        defaults={
            "name": name,
            "description": description,
            "scope_level": level,
            "permissions": [p.value for p in perms],
        },
    )
    RoleBinding.objects.create(user=owner, role=role, scope_kind="ORG", scope_id=astro_org.pk)

    # The one user the retirement is aimed at: Django permissions through a
    # group on a legacy organization membership, the chain the old checks read.
    holder = User.objects.create_user(username="holder-1864", email="holder-1864@t.test", password="x")
    legacy = LegacyOrganization.objects.create(name="Legacy 1864")
    group = Group.objects.create(name="legacy-everything-1864")
    group.permissions.set(DjangoPermission.objects.all())
    legacy.groups.add(group)
    membership = OrganizationMember.objects.create(member=holder, organization=legacy, is_active=True)
    membership.groups.add(group)

    return {
        "anonymous": AnonymousUser(),
        "plain": plain,
        "org_owner": owner,
        "operator": operator,
        "inactive_operator": inactive_operator,
        "legacy_holder": User.objects.get(pk=holder.pk),
    }


def _allowed(fn) -> bool:
    try:
        fn()
    except PermissionDenied:
        return False
    return True


def _info(user):
    from core.schema.context import StrawberryContext

    class _Request:
        def __init__(self, u):
            self.user = u
            self.session = {}
            self.headers = {}

    return SimpleNamespace(context=StrawberryContext(_Request(user)))


_serial = itertools.count()


def _target_user():
    n = next(_serial)
    return User.objects.create_user(username=f"target-{n}-1864", email=f"target-{n}@t.test", password="x")


def _legacy_org():
    from organization.models import Organization as LegacyOrganization

    return LegacyOrganization.objects.create(name=f"Row {next(_serial)} 1864")


def _new_checks(monkeypatch):
    """One callable per retired check; each runs the real post-#1864 code path."""
    from core.schema.mutations.permissions import GroupOperationInput, PermissionMutations
    from core.schema.mutations.user import UserMutations
    from core.schema.types.address import AddressType
    from core.schema.types.user import ProfileType
    from organization.models import Organization as LegacyOrganization

    monkeypatch.setattr(Profile, "request_reset_password", lambda self: None)
    monkeypatch.setattr(Profile, "anonymize_user", classmethod(lambda cls, user: None))

    def field(strawberry_type, name):
        return next(
            f for f in strawberry_type.__strawberry_definition__.fields if f.name == name
        ).base_resolver.wrapped_func

    def group_operation(user):
        target = _target_user()
        group, _ = Group.objects.get_or_create(name="ops-target-1864")
        return PermissionMutations().permission_group_operation(
            info=_info(user),
            input=GroupOperationInput(user_ids=[str(target.pk)], group_id=str(group.pk), operation="add"),
        )

    def address_city(user):
        address = Address.objects.create(city="Springfield")
        return field(AddressType, "city")(address, info=_info(user)) == "Springfield"

    def profile_email(user):
        someone = _target_user()
        return field(ProfileType, "email")(someone.profile, info=_info(user)) == someone.email

    return {
        # (old rule, new code path)
        "Model.get_queryset": (
            lambda u: _old_model_permission_check(u, "organization", "organization", "view_organization"),
            lambda u: _allowed(lambda: LegacyOrganization.get_queryset(LegacyOrganization.objects.all(), u)),
        ),
        "Model.can_add": (
            lambda u: _old_model_permission_check(u, "organization", "organization", "add_organization"),
            lambda u: _allowed(lambda: LegacyOrganization.can_add(u)),
        ),
        "Model.can_change": (
            lambda u: _old_model_permission_check(u, "organization", "organization", "change_organization"),
            lambda u: _allowed(lambda: LegacyOrganization.can_change(u)),
        ),
        "Model.delete_check (delete)": (
            lambda u: _old_model_permission_check(u, "organization", "organization", "delete_organization"),
            lambda u: _allowed(
                lambda: _legacy_org().delete_check(SimpleNamespace(context=SimpleNamespace(user=u)))
            ),
        ),
        "profileRequestPwdChange(other user)": (
            lambda u: _old_model_permission_check(u, "core", "profile", "change_reset_password_users"),
            lambda u: _allowed(
                lambda: UserMutations().profile_request_pwd_change(
                    info=_info(u), user_gid=GlobalIDUtils.to_global_id("UserType", _target_user().pk)
                )
            ),
        ),
        "profileRequestDeleteUser(other user)": (
            lambda u: _old_model_permission_check(u, "core", "profile", "delete_users"),
            lambda u: _allowed(
                lambda: UserMutations().profile_request_delete_user(
                    info=_info(u), user_gid=GlobalIDUtils.to_global_id("UserType", _target_user().pk)
                )
            ),
        ),
        "permissionGroupOperation": (
            lambda u: _old_django_auth_check(u, "auth.change_user", "auth.change_group"),
            lambda u: _allowed(lambda: group_operation(u)),
        ),
        "AddressType.city visible": (
            lambda u: _old_model_permission_check(u, "core", "address", "view_city"),
            address_city,
        ),
        "ProfileType.email visible": (
            lambda u: _old_model_permission_check(u, "core", "profile", "view_email"),
            profile_email,
        ),
    }


# The two answers that change, both refusals. ``legacy_holder`` held Django
# model permissions through a legacy organization group, which is what the
# issue retires. ``inactive_operator`` is a deactivated superuser: the old
# superuser short-circuit never looked at ``is_active``, the resolver does.
_INTENDED_DIVERGENCE = {"legacy_holder", "inactive_operator"}


def test_every_retired_check_keeps_its_answer_except_for_legacy_django_permission_holders(
    people, monkeypatch
):
    checks = _new_checks(monkeypatch)
    table = {}
    for site, (old, new) in checks.items():
        for who, user in people.items():
            table[(site, who)] = (old(user), new(user))

    same = {k: v for k, v in table.items() if k[1] not in _INTENDED_DIVERGENCE}
    assert {k: v for k, v in same.items() if v[0] != v[1]} == {}, "a retired check changed its answer"

    # Everyone keeps the answer the old rule gave; the operator is let in
    # and everyone else (RBAC org owner included) is refused, as before.
    for site in checks:
        assert table[(site, "operator")] == (True, True), site
        for who in ("anonymous", "plain", "org_owner"):
            assert table[(site, who)] == (False, False), (site, who)
        # The intended change: allowed by Django permissions before, refused now.
        assert table[(site, "legacy_holder")] == (True, False), site
        assert table[(site, "inactive_operator")][1] is False, site


def test_the_platform_operator_is_an_active_authenticated_superuser():
    active = User(username="a", is_superuser=True, is_active=True)
    assert is_platform_operator(active)
    assert not is_platform_operator(User(username="b", is_superuser=True, is_active=False))
    assert not is_platform_operator(User(username="c", is_superuser=False, is_active=True))
    assert not is_platform_operator(AnonymousUser())
    assert not is_platform_operator(None)


# ---------------------------------------------------------------------------
# Every model that carries the hooks, and every gated field, not a sample
# ---------------------------------------------------------------------------

# The delete permissions ``config.roles_gen`` carried, reproduced from the
# deleted file. Its ids were ``<model>.<codename>`` with no app label, so
# they are keyed by model name. ``delete_check`` worked on a model only when
# its ``delete_<model>`` permission was one of these. On any other model it
# read a permission that did not exist and raised AttributeError for everyone.
_RETIRED_DELETE_PERMISSIONS = frozenset(
    {
        "address",
        "admintools",
        "application",
        "authentication",
        "constance",
        "contenttype",
        "devicetoken",
        "fileupload",
        "gqllog",
        "group",
        "interval",
        "link",
        "logentry",
        "notification",
        "organization",
        "permission",
        "profile",
        "session",
        "sesstat",
        "taskresult",
        "user",
        "userinfo",
    }
)


def _models_with_the_hooks():
    from django.apps import apps

    from core.models.common import ModelPermissionsMixin

    return sorted(
        (m for m in apps.get_models() if issubclass(m, ModelPermissionsMixin)),
        key=lambda m: m._meta.label_lower,
    )


def _generic_delete(model, user) -> tuple[bool, bool]:
    """``(allowed, deleted)`` for ``delete_check`` on an unsaved ``model`` row."""
    instance = model()
    deleted = []
    instance.delete = lambda *args, **kwargs: deleted.append(True)
    allowed = _allowed(lambda: instance.delete_check(SimpleNamespace(context=SimpleNamespace(user=user))))
    return allowed, bool(deleted)


def test_generic_delete_reaches_only_the_models_it_could_delete_before(people):
    """Operator-only on its own would have let the operator hard-delete rows
    of every model with the hook, most of which the old check never let
    through. The allowed set has to match what the retired catalogue covered,
    and any other model with the hook, now or later, is refused."""
    from core.models.common import GENERIC_DELETE_MODELS

    models = _models_with_the_hooks()
    covered = {m._meta.label_lower for m in models if m._meta.model_name in _RETIRED_DELETE_PERMISSIONS}
    assert covered == GENERIC_DELETE_MODELS
    assert len(models) > len(covered)

    for model in models:
        for who, user in people.items():
            expected = who == "operator" and model._meta.label_lower in covered
            assert _generic_delete(model, user) == (expected, expected), (model._meta.label_lower, who)


def _hook_allows(model, hook: str, user) -> bool:
    if hook == "get_queryset":
        return _allowed(lambda: model.get_queryset(model._default_manager.none(), user))
    return _allowed(lambda: getattr(model, hook)(user))


@pytest.mark.parametrize("hook", ["get_queryset", "can_add", "can_change"])
def test_every_model_hook_admits_only_the_operator(people, hook):
    """``get_queryset`` admitted a superuser, plus a legacy Django permission
    holder on the models the retired catalogue covered. ``can_add`` and
    ``can_change`` checked nothing on the models it did not cover, because a
    missing permission short-circuited the check, so they are stricter there
    now. The only caller of those two is ``restricted_serializer_mutate``."""
    for model in _models_with_the_hooks():
        for who, user in people.items():
            assert _hook_allows(model, hook, user) is (who == "operator"), (model._meta.label_lower, who)


def _gated_resolvers() -> set[tuple[str, str]]:
    """Every ``(type, field)`` resolver in ``core/schema/types`` that asks ``is_platform_operator``."""
    found = set()
    for module in sorted((BACKEND / "core" / "schema" / "types").glob("*.py")):
        tree = ast.parse(module.read_text())
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for fn in cls.body:
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if any(
                    isinstance(c, ast.Call) and getattr(c.func, "id", None) == "is_platform_operator"
                    for c in ast.walk(fn)
                ):
                    found.add((cls.name, fn.name))
    return found


def _resolve(strawberry_type, name: str, source, user):
    fn = next(
        f for f in strawberry_type.__strawberry_definition__.fields if f.name == name
    ).base_resolver.wrapped_func
    if inspect.iscoroutinefunction(fn):
        return async_to_sync(fn)(source, info=_info(user))
    return fn(source, info=_info(user))


def test_every_gated_field_shows_only_to_the_operator(people):
    from core.models import Upload
    from core.schema.types.address import AddressType
    from core.schema.types.user import ProfileType, UserType

    address = Address.objects.create(
        address_line_one="1 Main St",
        address_line_two="Unit 2",
        city="Springfield",
        state="IL",
        street="Main St",
        zipcode="62704",
    )
    subject = User.objects.create_user(username="subject-1864", email="subject-1864@t.test", password="x")
    profile = subject.profile
    profile.first_name = "Ada"
    profile.last_name = "Lovelace"
    profile.avatar = Upload.objects.create(
        name="avatar-1864.png", content_type="image/png", public_url="https://t.test/avatar-1864.png"
    )
    profile.signature = Upload.objects.create(
        name="signature-1864.png", content_type="image/png", public_url="https://t.test/signature-1864.png"
    )
    profile.save()

    # (type, field, source): (what the operator sees, what anyone else sees)
    fields = {
        **{
            (AddressType, name, address): (getattr(address, name), "")
            for name in ("address_line_one", "address_line_two", "city", "state", "street", "zipcode")
        },
        (ProfileType, "email", profile): ("subject-1864@t.test", None),
        (ProfileType, "first_name", profile): ("Ada", None),
        (ProfileType, "last_name", profile): ("Lovelace", None),
        (ProfileType, "avatar", profile): (profile.avatar_id, None),
        (ProfileType, "signature", profile): (profile.signature_id, None),
        (UserType, "first_name", subject): ("Ada", ""),
        (UserType, "last_name", subject): ("Lovelace", ""),
    }
    assert {(t.__name__, name) for t, name, _ in fields} == _gated_resolvers()

    for (strawberry_type, name, source), (shown, hidden) in fields.items():
        for who, user in people.items():
            got = _resolve(strawberry_type, name, source, user)
            got = getattr(got, "pk", got)
            expected = shown if who == "operator" else hidden
            assert got == expected, (strawberry_type.__name__, name, who)


def test_the_platform_schema_export_lists_the_rbac_catalogue():
    from core.management.commands.export_platform_schema import Command
    from core.permissions import Permission

    assert Command()._get_permissions() == [{"name": p.name, "value": p.value} for p in Permission]


# ---------------------------------------------------------------------------
# Checks that named a permission that never existed now refuse outright
# ---------------------------------------------------------------------------


def test_authenticating_with_another_users_pin_is_refused_rather_than_crashing():
    from core.models import PinTransaction
    from core.models.user import PinTransactionKindChoices

    operator = User.objects.create_superuser(username="pin-op-1864", email="pin-op@t.test", password="x")
    other = User.objects.create_user(username="pin-other-1864", email="pin-other@t.test", password="x")
    with pytest.raises(PermissionDenied):
        PinTransaction.add_transaction(operator, PinTransactionKindChoices.AUTHENTICATED, proxy_user=other)
    assert not PinTransaction.objects.filter(user=operator).exists()


@pytest.mark.parametrize(
    "action", ["request_user", "sign", "cancel", "reset_sign_request", "users_allowed_to_sign"]
)
def test_sign_request_actions_refuse_rather_than_crash(action):
    from core.models import SignRequest

    operator = User.objects.create_superuser(
        username=f"sign-{action}-1864", email=f"{action}@t.test", password="x"
    )
    request = SignRequest(user=operator)
    with pytest.raises(PermissionDenied):
        if action == "users_allowed_to_sign":
            request.users_allowed_to_sign()
        else:
            getattr(request, action)(operator)
