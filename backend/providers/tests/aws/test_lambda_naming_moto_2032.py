"""Native function/role/URL collision and ownership boundaries without execution."""

from __future__ import annotations

import dataclasses
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from _sdk.managed_service import DeprovisionSpec, ServiceHandle, UpdateSpec
from aws._naming import iam_role_name
from aws.managed._base import ManagedServiceError, tags_for
from aws.managed.faas_lambda import LambdaDriver
from tests.aws._lambda_native_2032 import native_lambda
from tests.aws.test_managed_faas_lambda import _ID, _spec

OTHER = "b1234567-1234-4234-8234-123456789012"
LAMBDA_EFFECTS = (
    "create_function",
    "update_function_code",
    "update_function_configuration",
    "delete_function",
    "create_function_url_config",
    "update_function_url_config",
    "delete_function_url_config",
    "add_permission",
    "remove_permission",
    "tag_resource",
)
IAM_EFFECTS = (
    "create_role",
    "update_assume_role_policy",
    "put_role_policy",
    "delete_role_policy",
    "delete_role",
    "tag_role",
)


@pytest.fixture
def cloud():
    with native_lambda() as state:
        yield state


def spec(cloud, **kwargs):
    return dataclasses.replace(_spec(cloud.code), **kwargs)


def provision(cloud, **kwargs):
    return cloud.driver.provision(spec(cloud, **kwargs))


def name(handle):
    return handle.partition("/")[2]


def no_effects(cloud):
    stack = ExitStack()
    spies = [
        stack.enter_context(patch.object(client, key, wraps=getattr(client, key)))
        for client, keys in ((cloud.api, LAMBDA_EFFECTS), (cloud.iam, IAM_EFFECTS))
        for key in keys
    ]
    return stack, spies


def legacy(cloud, function_name="astrolift-legacy-2032", service_id=_ID):
    driver = cloud.driver
    original = spec(cloud, managed_service_id=service_id)
    role_name = driver._role_name_for(function_name)
    role_arn = driver._ensure_exec_role(role_name=role_name, function_name=function_name, spec=original)
    cloud.api.create_function(**driver._build_create_args(original, function_name, role_arn))
    cloud.api.create_function_url_config(FunctionName=function_name, AuthType="AWS_IAM")
    return "faas/" + function_name


@pytest.mark.parametrize("collision", ["joined", "long_joined"])
def test_actual_old_collisions_create_two_distinct_uuid_functions_roles_and_urls(cloud, collision):
    if collision == "joined":
        first = spec(cloud, organization_slug="alpha-beta", app_slug="gamma")
        second = spec(cloud, managed_service_id=OTHER, organization_slug="alpha", app_slug="beta-gamma")
    else:
        first = spec(cloud, organization_slug="alpha-beta", app_slug="gamma" + "x" * 100)
        second = spec(cloud, managed_service_id=OTHER, organization_slug="alpha", app_slug="beta-gamma" + "x" * 100)
    old = [
        iam_role_name(
            "astrolift", s.organization_slug, s.app_slug, s.environment_name, s.service_handle_hint, max_len=64
        )
        for s in (first, second)
    ]
    assert old[0] == old[1]
    results = [cloud.driver.provision(s) for s in (first, second)]
    assert all(r.ok for r in results), results
    assert results[0].handle != results[1].handle
    for s, r in zip((first, second), results, strict=True):
        fn = cloud.api.get_function(FunctionName=name(r.handle))
        assert name(r.handle).endswith(s.managed_service_id.replace("-", ""))
        assert len(name(r.handle)) <= 64
        assert fn["Tags"]["astrolift.io/managed_service_id"] == s.managed_service_id
        role = cloud.iam.get_role(RoleName=cloud.driver._role_name_for(name(r.handle)))["Role"]
        assert role["Arn"] == fn["Configuration"]["Role"]
        assert (
            dict((t["Key"], t["Value"]) for t in role["Tags"])["astrolift.io/managed_service_id"]
            == s.managed_service_id
        )
        assert cloud.driver.provision(s).handle == r.handle
    assert len(cloud.api.list_functions()["Functions"]) == 2
    assert len(cloud.iam.list_roles()["Roles"]) == 2


@pytest.mark.parametrize("function_name", ["Old_Function", "x" * 64])
def test_exact_owned_legacy_function_and_role_survive_slug_changes_and_full_lifecycle(cloud, function_name):
    handle = legacy(cloud, function_name)
    result = provision(
        cloud, recorded_handle=handle, organization_slug="renamed", app_slug="renamed", service_handle_hint="new"
    )
    assert result.ok and result.handle == handle, result
    assert len(cloud.api.list_functions()["Functions"]) == 1
    updated = cloud.driver.update(
        UpdateSpec(handle=handle, managed_service_id=_ID, config={**cloud.code, "memory_mb": 1024})
    )
    assert updated.ok, updated
    assert cloud.api.get_function_configuration(FunctionName=function_name)["MemorySize"] == 1024
    binding = cloud.driver.binding(ServiceHandle(handle=handle))
    assert binding.env_vars["FUNCTION_ARN"].literal == cloud.driver._function_arn(function_name)
    assert cloud.driver.deprovision(DeprovisionSpec(handle=handle, managed_service_id=_ID), force_destroy=True).ok
    assert cloud.api.list_functions()["Functions"] == []
    assert cloud.iam.list_roles()["Roles"] == []


@pytest.mark.parametrize("target", ["function", "role"])
@pytest.mark.parametrize("corruption", ["owner", "marker", "missing_owner"])
def test_foreign_or_unverifiable_parent_blocks_every_effect_even_force(cloud, target, corruption):
    initial = provision(cloud)
    assert initial.ok
    fn = name(initial.handle)
    role = cloud.driver._role_name_for(fn)
    key = "astrolift.io/managed-by" if corruption == "marker" else "astrolift.io/managed_service_id"
    value = "foreign" if corruption == "marker" else OTHER
    if target == "function":
        arn = cloud.driver._function_arn(fn)
        if corruption == "missing_owner":
            cloud.api.untag_resource(Resource=arn, TagKeys=[key])
        else:
            cloud.api.tag_resource(Resource=arn, Tags={key: value})
    elif corruption == "missing_owner":
        cloud.iam.untag_role(RoleName=role, TagKeys=[key])
    else:
        cloud.iam.tag_role(RoleName=role, Tags=[{"Key": key, "Value": value}])
    stack, spies = no_effects(cloud)
    with stack:
        assert not provision(cloud, recorded_handle=initial.handle).ok
        update = cloud.driver.update(UpdateSpec(handle=initial.handle, managed_service_id=_ID, config=cloud.code))
        delete = cloud.driver.deprovision(
            DeprovisionSpec(handle=initial.handle, managed_service_id=_ID), force_destroy=True
        )
        assert not update.ok and not update.retryable
        assert not delete.ok and not delete.retryable
        for spy in spies:
            spy.assert_not_called()
    assert len(cloud.api.list_functions()["Functions"]) == 1
    assert len(cloud.iam.list_roles()["Roles"]) == 1


@pytest.mark.parametrize("missing", ["function", "role"])
def test_missing_recorded_parent_is_never_replaced(cloud, missing):
    initial = provision(cloud)
    assert initial.ok
    fn = name(initial.handle)
    if missing == "function":
        cloud.api.delete_function(FunctionName=fn)
    else:
        role = cloud.driver._role_name_for(fn)
        cloud.iam.delete_role_policy(RoleName=role, PolicyName="astrolift-faas-policy")
        cloud.iam.delete_role(RoleName=role)
    stack, spies = no_effects(cloud)
    with stack:
        result = provision(cloud, recorded_handle=initial.handle)
        assert not result.ok and "missing" in result.message
        for spy in spies:
            spy.assert_not_called()


@pytest.mark.parametrize("field", ["FunctionName", "FunctionArn", "Role"])
def test_foreign_function_metadata_is_refused_before_effects(cloud, field):
    initial = provision(cloud)
    response = cloud.api.get_function(FunctionName=name(initial.handle))
    response["Configuration"][field] += "-foreign"
    stack, spies = no_effects(cloud)
    with stack, patch.object(cloud.api, "get_function", return_value=response):
        assert not provision(cloud, recorded_handle=initial.handle).ok
        assert not cloud.driver.update(UpdateSpec(handle=initial.handle, managed_service_id=_ID, config=cloud.code)).ok
        assert not cloud.driver.deprovision(
            DeprovisionSpec(handle=initial.handle, managed_service_id=_ID), force_destroy=True
        ).ok
        for spy in spies:
            spy.assert_not_called()


@pytest.mark.parametrize("field", ["RoleName", "Path", "Arn"])
def test_foreign_role_metadata_is_refused_before_effects(cloud, field):
    initial = provision(cloud)
    response = cloud.iam.get_role(RoleName=cloud.driver._role_name_for(name(initial.handle)))
    response["Role"][field] += "-foreign"
    stack, spies = no_effects(cloud)
    with stack, patch.object(cloud.iam, "get_role", return_value=response):
        assert not provision(cloud, recorded_handle=initial.handle).ok
        assert not cloud.driver.update(UpdateSpec(handle=initial.handle, managed_service_id=_ID, config=cloud.code)).ok
        assert not cloud.driver.deprovision(
            DeprovisionSpec(handle=initial.handle, managed_service_id=_ID), force_destroy=True
        ).ok
        for spy in spies:
            spy.assert_not_called()


def test_foreign_url_parent_is_refused_before_root_or_iam_effects(cloud):
    initial = provision(cloud)
    url = cloud.api.get_function_url_config(FunctionName=name(initial.handle))
    url["FunctionArn"] = cloud.driver._function_arn("foreign")
    stack, spies = no_effects(cloud)
    with stack, patch.object(cloud.api, "get_function_url_config", return_value=url):
        assert not provision(cloud, recorded_handle=initial.handle).ok
        assert not cloud.driver.update(UpdateSpec(handle=initial.handle, managed_service_id=_ID, config=cloud.code)).ok
        assert not cloud.driver.deprovision(
            DeprovisionSpec(handle=initial.handle, managed_service_id=_ID), force_destroy=True
        ).ok
        for spy in spies:
            spy.assert_not_called()
        with pytest.raises(ManagedServiceError):
            cloud.driver.binding(ServiceHandle(handle=initial.handle))


@pytest.mark.parametrize("handle", ["topic/name", "faas/a:b", "faas/a/b", "faas/" + "x" * 65, "faas/"])
def test_invalid_recorded_handle_is_not_looked_up_or_replaced(cloud, handle):
    stack, spies = no_effects(cloud)
    with stack, patch.object(cloud.api, "get_function", wraps=cloud.api.get_function) as read:
        assert not provision(cloud, recorded_handle=handle).ok
        read.assert_not_called()
        for spy in spies:
            spy.assert_not_called()


def test_partial_create_owned_role_recovers_but_foreign_role_never_gets_rewritten(cloud):
    s = spec(cloud)
    fn = cloud.driver._function_name(s)
    role = cloud.driver._role_name_for(fn)
    cloud.driver._ensure_exec_role(role_name=role, function_name=fn, spec=s)
    cloud.iam.tag_role(RoleName=role, Tags=[{"Key": "astrolift.io/managed_service_id", "Value": OTHER}])
    stack, spies = no_effects(cloud)
    with stack:
        assert not provision(cloud).ok
        for spy in spies:
            spy.assert_not_called()
    cloud.iam.tag_role(RoleName=role, Tags=tags_for(s))
    assert provision(cloud).ok
    assert len(cloud.iam.list_roles()["Roles"]) == 1


def test_conflicting_function_create_never_reconciles_a_foreign_raced_parent(cloud):
    original_create = cloud.api.create_function

    def race(**kwargs):
        kwargs["Tags"] = {**kwargs["Tags"], "astrolift.io/managed_service_id": OTHER}
        original_create(**kwargs)
        raise cloud.api.exceptions.ResourceConflictException(
            {"Error": {"Code": "ResourceConflictException"}}, "CreateFunction"
        )

    with (
        patch.object(cloud.api, "create_function", side_effect=race),
        patch.object(cloud.api, "update_function_code", wraps=cloud.api.update_function_code) as update,
    ):
        result = provision(cloud)
        assert not result.ok
        update.assert_not_called()
    assert cloud.api.list_functions()["Functions"][0]["FunctionName"] == cloud.driver._function_name(spec(cloud))


def test_owned_role_only_partial_teardown_does_not_delete_foreign_role(cloud):
    initial = provision(cloud)
    assert initial.ok
    fn = name(initial.handle)
    cloud.api.delete_function(FunctionName=fn)
    result = cloud.driver.deprovision(DeprovisionSpec(handle=initial.handle, managed_service_id=_ID))
    assert result.ok, result
    assert cloud.iam.list_roles()["Roles"] == []
    assert cloud.driver.deprovision(DeprovisionSpec(handle=initial.handle, managed_service_id=_ID)).ok


def test_cloudfront_extension_requires_explicit_owner_and_exact_account(cloud):
    initial = provision(cloud)
    fn = name(initial.handle)
    arn = "arn:aws:cloudfront::123456789012:distribution/E123"
    with patch.object(cloud.api, "add_permission", wraps=cloud.api.add_permission) as write:
        with pytest.raises(ValueError):
            cloud.driver.allow_cloudfront_invoke(fn, arn)
        with pytest.raises(ManagedServiceError):
            cloud.driver.allow_cloudfront_invoke(
                fn, arn.replace("123456789012", "999999999999"), managed_service_id=_ID
            )
        write.assert_not_called()
        cloud.driver.allow_cloudfront_invoke(fn, arn, managed_service_id=_ID)
        assert write.call_count == 1


def test_native_nondefault_role_path_is_exactly_preserved(cloud):
    cloud.cfg = dataclasses.replace(cloud.cfg, role_path_prefix="/astrolift/fixture/")
    cloud.driver = LambdaDriver(config=cloud.cfg, client=cloud.api, iam_client=cloud.iam)
    initial = provision(cloud)
    assert initial.ok, initial
    assert (
        ":role/astrolift/fixture/" in cloud.api.get_function(FunctionName=name(initial.handle))["Configuration"]["Role"]
    )


def test_cloudfront_permission_never_silently_accepts_another_distribution(cloud):
    initial = provision(cloud)
    fn = name(initial.handle)
    original = "arn:aws:cloudfront::123456789012:distribution/E123"
    cloud.driver.allow_cloudfront_invoke(fn, original, managed_service_id=_ID)
    before = cloud.api.get_policy(FunctionName=fn)["Policy"]
    with patch.object(cloud.api, "add_permission", wraps=cloud.api.add_permission) as add:
        cloud.driver.allow_cloudfront_invoke(fn, original, managed_service_id=_ID)
        with pytest.raises(ManagedServiceError):
            cloud.driver.allow_cloudfront_invoke(fn, original + "4", managed_service_id=_ID)
        add.assert_not_called()
    assert cloud.api.get_policy(FunctionName=fn)["Policy"] == before


def test_mutable_labels_do_not_move_the_same_saved_uuid(cloud):
    first = provision(cloud)
    second = provision(
        cloud,
        organization_slug="renamed",
        app_slug="renamed",
        environment_name="changed",
        service_handle_hint="different",
    )
    assert first.ok and second.ok and first.handle == second.handle
    assert len(cloud.api.list_functions()["Functions"]) == 1


@pytest.mark.parametrize(
    "field,value",
    [("account_id", ""), ("account_id", "invalid"), ("region", "bad:region"), ("role_path_prefix", "missing-slash")],
)
def test_invalid_native_configuration_never_creates_a_function_or_role(cloud, field, value):
    cloud.driver = LambdaDriver(
        config=dataclasses.replace(cloud.cfg, **{field: value}), client=cloud.api, iam_client=cloud.iam
    )
    stack, spies = no_effects(cloud)
    with stack:
        assert not provision(cloud).ok
        for spy in spies:
            spy.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("FunctionUrl", "https://foreign.invalid/"),
        ("FunctionUrl", "https://foreign.lambda-url.us-west-2.on.aws/"),
        ("FunctionUrl", "https://user@foreign.lambda-url.us-east-1.on.aws/"),
        ("AuthType", "unknown"),
    ],
)
def test_unverifiable_native_url_metadata_is_never_returned_or_reconciled(cloud, field, value):
    initial = provision(cloud)
    metadata = cloud.api.get_function_url_config(FunctionName=name(initial.handle))
    metadata[field] = value
    stack, spies = no_effects(cloud)
    with stack, patch.object(cloud.api, "get_function_url_config", return_value=metadata):
        assert not provision(cloud, recorded_handle=initial.handle).ok
        with pytest.raises(ManagedServiceError):
            cloud.driver.binding(ServiceHandle(handle=initial.handle))
        for spy in spies:
            spy.assert_not_called()


def test_function_disappearance_after_wait_cannot_report_successful_private_provision(cloud):
    def vanished(fn):
        cloud.api.delete_function(FunctionName=fn)

    with patch.object(cloud.driver, "_wait_active", side_effect=vanished):
        result = provision(cloud, config={**cloud.code, "public": False})
    assert not result.ok and "disappeared" in result.message
    assert cloud.api.list_functions()["Functions"] == []
    assert len(cloud.iam.list_roles()["Roles"]) == 1
    # The owned partial role remains available to a later explicit retry.
    assert provision(cloud).ok
    assert len(cloud.iam.list_roles()["Roles"]) == 1


def test_role_disappearance_during_create_conflict_cannot_reconcile_code(cloud):
    create = cloud.api.create_function

    def race(**kwargs):
        create(**kwargs)
        role = cloud.driver._role_name_for(kwargs["FunctionName"])
        cloud.iam.delete_role_policy(RoleName=role, PolicyName="astrolift-faas-policy")
        cloud.iam.delete_role(RoleName=role)
        raise cloud.api.exceptions.ResourceConflictException(
            {"Error": {"Code": "ResourceConflictException"}}, "CreateFunction"
        )

    with (
        patch.object(cloud.api, "create_function", side_effect=race),
        patch.object(cloud.api, "update_function_code", wraps=cloud.api.update_function_code) as update,
    ):
        result = provision(cloud)
        assert not result.ok and "role is missing" in result.message
        update.assert_not_called()


def test_basic_execution_policy_names_only_this_account_region_and_function(cloud):
    import json

    initial = provision(cloud)
    fn = name(initial.handle)
    policy = cloud.iam.get_role_policy(RoleName=cloud.driver._role_name_for(fn), PolicyName="astrolift-faas-policy")
    document = policy["PolicyDocument"]
    if isinstance(document, str):
        document = json.loads(document)
    assert document["Statement"][0]["Resource"] == f"arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/{fn}:*"
