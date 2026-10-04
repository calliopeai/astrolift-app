"""Read-only catalogue contract against real boto3 models and Stubber dispatch."""

from dataclasses import asdict

import boto3
import pytest
from botocore.stub import Stubber

from _sdk.cloud_credentials import CloudCredential, CredentialMode
from aws.bedrock_catalogue import (
    BedrockCatalogue,
    BedrockCatalogueConfig,
    BedrockCatalogueError,
    BedrockSourceKind,
    CatalogueState,
)

ACCOUNT = "123456789012"
REGION = "us-west-2"
MODEL = "amazon.titan-text-express-v1"
MODEL_ARN = f"arn:aws:bedrock:{REGION}::foundation-model/{MODEL}"
PROFILE = "us.amazon.titan-text-express-v1"
PROFILE_ARN = f"arn:aws:bedrock:{REGION}:{ACCOUNT}:inference-profile/{PROFILE}"
PRIVATE = "synthetic-private-provider-body-marker"


def foundation(model=MODEL, **changes):
    return {
        "modelId": model,
        "modelArn": f"arn:aws:bedrock:{REGION}::foundation-model/{model}",
        "modelName": "Titan Text",
        "providerName": "Amazon",
        "inputModalities": ["TEXT"],
        "outputModalities": ["TEXT"],
        "responseStreamingSupported": True,
        "inferenceTypesSupported": ["ON_DEMAND"],
        "modelLifecycle": {"status": "ACTIVE"},
        **changes,
    }


def profile(identifier=PROFILE, **changes):
    return {
        "inferenceProfileId": identifier,
        "inferenceProfileArn": f"arn:aws:bedrock:{REGION}:{ACCOUNT}:inference-profile/{identifier}",
        "inferenceProfileName": "Titan Cross Region",
        "type": "SYSTEM_DEFINED",
        "status": "ACTIVE",
        "models": [
            {"modelArn": MODEL_ARN},
            {"modelArn": f"arn:aws:bedrock:us-east-1::foundation-model/{MODEL}"},
        ],
        **changes,
    }


@pytest.fixture
def wire():
    session = boto3.Session(
        aws_access_key_id="fixture-only",
        aws_secret_access_key="fixture-only",
        region_name=REGION,
    )
    catalog = BedrockCatalogue(
        BedrockCatalogueConfig(REGION, CloudCredential("aws", declared_account=ACCOUNT)), session=session
    )
    catalog._clients()
    with Stubber(catalog._sts) as sts, Stubber(catalog._bedrock) as bedrock:
        yield catalog, sts, bedrock
        sts.assert_no_pending_responses()
        bedrock.assert_no_pending_responses()
    catalog.close()


def identity(sts, *, account=ACCOUNT, arn=None, user_id="ROLE:fixture", times=1):
    for _ in range(times):
        sts.add_response(
            "get_caller_identity",
            {
                "Account": account,
                "Arn": arn or f"arn:aws:sts::{account}:assumed-role/fixture/catalogue",
                "UserId": user_id,
            },
            {},
        )


def read(sts, bedrock, method, response, params):
    identity(sts, times=2)
    bedrock.add_response(method, response, params)


def test_foundation_metadata_never_claims_invoke_authority(wire):
    catalog, sts, bedrock = wire
    read(sts, bedrock, "list_foundation_models", {"modelSummaries": [foundation()]}, {})
    page = catalog.foundation_models()
    assert page.state == CatalogueState.METADATA
    assert asdict(page.identity) == {"account_id": ACCOUNT, "region": REGION, "partition": "aws"}
    source = page.items[0]
    assert source.kind == BedrockSourceKind.FOUNDATION_MODEL
    assert (source.identifier, source.arn, source.inference_types) == (MODEL, MODEL_ARN, ("ON_DEMAND",))
    assert source.streaming is True
    assert source.availability.invoke_access == "unknown"
    assert not page.truncated and not page.partial
    assert catalog._bedrock.meta.config.connect_timeout == 5
    assert catalog._bedrock.meta.config.read_timeout == 20
    assert catalog._bedrock.meta.config.retries["total_max_attempts"] == 1
    assert catalog._session._session.get_default_client_config().read_timeout == 20


def test_foundation_output_cap_is_explicit(wire):
    catalog, sts, bedrock = wire
    read(sts, bedrock, "list_foundation_models", {"modelSummaries": [foundation(), foundation("amazon.other-v1")]}, {})
    result = catalog.foundation_models(limit=1)
    assert result.truncated and not result.partial and len(result.items) == 1


def test_paginated_profiles_keep_native_cross_region_destinations(wire):
    catalog, sts, bedrock = wire
    read(
        sts,
        bedrock,
        "list_inference_profiles",
        {"inferenceProfileSummaries": [profile()], "nextToken": "native-page-2"},
        {"maxResults": 3},
    )
    application = profile(
        "app-profile",
        type="APPLICATION",
        inferenceProfileArn=f"arn:aws:bedrock:{REGION}:{ACCOUNT}:application-inference-profile/app-profile",
    )
    read(
        sts,
        bedrock,
        "list_inference_profiles",
        {"inferenceProfileSummaries": [application]},
        {"maxResults": 2, "nextToken": "native-page-2"},
    )
    result = catalog.inference_profiles(limit=3)
    assert result.state == CatalogueState.METADATA and not result.truncated
    assert len(result.items) == 2
    assert result.items[0].destination_model_arns[1].startswith("arn:aws:bedrock:us-east-1::")
    assert result.items[1].profile_type == "APPLICATION"
    assert all(item.availability.invoke_access == "unknown" for item in result.items)
    assert "native-page-2" not in repr(result)


@pytest.mark.parametrize("limit,max_pages", [(1, 5), (5, 1)])
def test_profile_item_and_page_bounds(wire, limit, max_pages):
    catalog, sts, bedrock = wire
    read(
        sts,
        bedrock,
        "list_inference_profiles",
        {"inferenceProfileSummaries": [profile()], "nextToken": "more"},
        {"maxResults": limit},
    )
    result = catalog.inference_profiles(limit=limit, max_pages=max_pages)
    assert len(result.items) == 1 and result.truncated and not result.partial


def test_profile_partial_provider_error_is_not_complete_or_ready(wire, caplog, capsys):
    catalog, sts, bedrock = wire
    read(
        sts,
        bedrock,
        "list_inference_profiles",
        {"inferenceProfileSummaries": [profile()], "nextToken": "more"},
        {"maxResults": 3},
    )
    identity(sts, times=2)
    bedrock.add_client_error(
        "list_inference_profiles",
        service_error_code="ThrottlingException",
        service_message=PRIVATE,
        expected_params={"maxResults": 2, "nextToken": "more"},
    )
    result = catalog.inference_profiles(limit=3)
    assert result.state == CatalogueState.ERROR and result.partial and result.truncated
    assert len(result.items) == 1 and result.reason == "METADATA_UNAVAILABLE"
    assert PRIVATE not in repr(result) + caplog.text + str(capsys.readouterr())


@pytest.mark.parametrize(
    "error,state",
    [
        ("AccessDeniedException", CatalogueState.DENIED),
        ("ResourceNotFoundException", CatalogueState.NOT_FOUND),
        ("InternalServerException", CatalogueState.ERROR),
    ],
)
def test_detail_errors_are_fixed_and_sanitized(wire, error, state, caplog, capsys):
    catalog, sts, bedrock = wire
    identity(sts, times=2)
    bedrock.add_client_error(
        "get_foundation_model",
        service_error_code=error,
        service_message=PRIVATE,
        expected_params={"modelIdentifier": MODEL},
    )
    result = catalog.detail(BedrockSourceKind.FOUNDATION_MODEL, MODEL)
    assert result.state == state and result.source is None
    assert PRIVATE not in repr(result) + caplog.text + str(capsys.readouterr())


@pytest.mark.parametrize("selector", [MODEL, MODEL_ARN])
def test_exact_foundation_detail_and_native_availability(wire, selector):
    catalog, sts, bedrock = wire
    read(sts, bedrock, "get_foundation_model", {"modelDetails": foundation()}, {"modelIdentifier": selector})
    read(
        sts,
        bedrock,
        "get_foundation_model_availability",
        {
            "modelId": MODEL,
            "authorizationStatus": "AUTHORIZED",
            "entitlementAvailability": "AVAILABLE",
            "regionAvailability": "AVAILABLE",
            "agreementAvailability": {"status": "AVAILABLE"},
        },
        {"modelId": MODEL},
    )
    result = catalog.detail(BedrockSourceKind.FOUNDATION_MODEL, selector, check_availability=True)
    assert result.state == CatalogueState.METADATA
    assert result.source.availability.authorization == "AUTHORIZED"
    assert result.source.availability.invoke_access == "unknown"


@pytest.mark.parametrize("error", ["AccessDeniedException", "ResourceNotFoundException", "InternalServerException"])
def test_availability_failure_preserves_readable_unknown_metadata(wire, error):
    catalog, sts, bedrock = wire
    read(sts, bedrock, "get_foundation_model", {"modelDetails": foundation()}, {"modelIdentifier": MODEL})
    identity(sts, times=2)
    bedrock.add_client_error(
        "get_foundation_model_availability",
        service_error_code=error,
        service_message=PRIVATE,
        expected_params={"modelId": MODEL},
    )
    result = catalog.detail(BedrockSourceKind.FOUNDATION_MODEL, MODEL, check_availability=True)
    assert result.state == CatalogueState.METADATA and result.source.identifier == MODEL
    assert result.source.availability.invoke_access == "unknown"
    assert result.source.availability.authorization is None
    assert PRIVATE not in repr(result)


@pytest.mark.parametrize("selector", [PROFILE, PROFILE_ARN])
def test_profile_detail_never_substitutes_a_foundation_model(wire, selector):
    catalog, sts, bedrock = wire
    read(sts, bedrock, "get_inference_profile", profile(), {"inferenceProfileIdentifier": selector})
    result = catalog.detail(BedrockSourceKind.INFERENCE_PROFILE, selector, check_availability=True)
    assert result.state == CatalogueState.METADATA
    assert result.source.arn == PROFILE_ARN
    assert len(result.source.destination_model_arns) == 2
    assert result.source.availability.invoke_access == "unknown"


@pytest.mark.parametrize(
    "changed",
    [
        {"modelArn": "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-text-express-v1"},
        {"modelArn": f"arn:aws:bedrock:{REGION}:999999999999:foundation-model/{MODEL}"},
        {"modelArn": f"arn:aws-cn:bedrock:{REGION}::foundation-model/{MODEL}"},
        {"modelId": "amazon.other-v1"},
    ],
)
def test_foreign_foundation_identity_is_refused_even_beyond_output_limit(wire, changed):
    catalog, sts, bedrock = wire
    read(sts, bedrock, "list_foundation_models", {"modelSummaries": [foundation(), foundation(**changed)]}, {})
    result = catalog.foundation_models(limit=1)
    assert result.state == CatalogueState.REFUSED and result.items == ()


@pytest.mark.parametrize(
    "changed",
    [
        {"inferenceProfileArn": f"arn:aws:bedrock:{REGION}:999999999999:inference-profile/{PROFILE}"},
        {"inferenceProfileArn": f"arn:aws:bedrock:us-east-1:{ACCOUNT}:inference-profile/{PROFILE}"},
        {"inferenceProfileId": "other"},
        {"models": [{"modelArn": f"arn:aws:bedrock:us-east-1:999999999999:foundation-model/{MODEL}"}]},
        {"models": [{"modelArn": f"arn:aws-cn:bedrock:cn-north-1::foundation-model/{MODEL}"}]},
    ],
)
def test_foreign_profile_response_discards_every_page(wire, changed):
    catalog, sts, bedrock = wire
    read(
        sts,
        bedrock,
        "list_inference_profiles",
        {"inferenceProfileSummaries": [profile()], "nextToken": "more"},
        {"maxResults": 3},
    )
    read(
        sts,
        bedrock,
        "list_inference_profiles",
        {"inferenceProfileSummaries": [profile(**changed)]},
        {"maxResults": 2, "nextToken": "more"},
    )
    result = catalog.inference_profiles(limit=3)
    assert result.state == CatalogueState.REFUSED and not result.items and not result.partial


@pytest.mark.parametrize("after", [False, True])
def test_actual_sts_account_mismatch_refuses_before_or_after_read(wire, after):
    catalog, sts, bedrock = wire
    if after:
        identity(sts)
        bedrock.add_response("list_foundation_models", {"modelSummaries": [foundation()]}, {})
    identity(sts, account="999999999999")
    result = catalog.foundation_models()
    assert result.state == CatalogueState.REFUSED and not result.items


def test_same_account_principal_change_is_refused(wire):
    catalog, sts, bedrock = wire
    identity(sts)
    bedrock.add_response("list_foundation_models", {"modelSummaries": [foundation()]}, {})
    identity(sts, arn=f"arn:aws:sts::{ACCOUNT}:assumed-role/other/catalogue")
    result = catalog.foundation_models()
    assert result.state == CatalogueState.REFUSED and not result.items


def test_detail_response_must_match_exact_requested_id(wire):
    catalog, sts, bedrock = wire
    read(
        sts,
        bedrock,
        "get_foundation_model",
        {"modelDetails": foundation("amazon.other-v1")},
        {"modelIdentifier": MODEL},
    )
    result = catalog.detail(BedrockSourceKind.FOUNDATION_MODEL, MODEL)
    assert result.state == CatalogueState.REFUSED and result.source is None


@pytest.mark.parametrize(
    "identifier",
    [
        "https://provider/private",
        "../model",
        "*",
        "amazon.model?secret=value",
        f"arn:aws:bedrock:{REGION}:999999999999:inference-profile/{PROFILE}",
        f"arn:aws:bedrock:us-east-1:{ACCOUNT}:inference-profile/{PROFILE}",
    ],
)
def test_invalid_selector_refuses_before_any_sdk_request(wire, identifier):
    catalog, _, _ = wire
    with pytest.raises(BedrockCatalogueError, match="INVALID_REQUEST"):
        catalog.detail(BedrockSourceKind.INFERENCE_PROFILE, identifier)


@pytest.mark.parametrize("limit,max_pages", [(0, 1), (501, 1), (True, 1), (3, 0), (3, 6)])
def test_invalid_bounds_have_no_sdk_requests(wire, limit, max_pages):
    catalog, _, _ = wire
    with pytest.raises(BedrockCatalogueError, match="INVALID_REQUEST"):
        catalog.inference_profiles(limit=limit, max_pages=max_pages)


def test_repeated_native_token_refuses_without_infinite_scan(wire):
    catalog, sts, bedrock = wire
    read(
        sts,
        bedrock,
        "list_inference_profiles",
        {"inferenceProfileSummaries": [], "nextToken": "same"},
        {"maxResults": 3},
    )
    read(
        sts,
        bedrock,
        "list_inference_profiles",
        {"inferenceProfileSummaries": [], "nextToken": "same"},
        {"maxResults": 3, "nextToken": "same"},
    )
    assert catalog.inference_profiles(limit=3).state == CatalogueState.REFUSED


@pytest.mark.parametrize(
    "credential",
    [
        CloudCredential("aws"),
        CloudCredential("gcp", declared_account=ACCOUNT),
        CloudCredential("aws", declared_account="bad"),
        CloudCredential(
            "aws",
            mode=CredentialMode.AWS_ASSUME_ROLE,
            declared_account=ACCOUNT,
            role_arn="arn:aws:iam::999999999999:role/foreign",
        ),
    ],
)
def test_unbound_configuration_is_refused_without_clients(credential):
    with pytest.raises(BedrockCatalogueError, match="INVALID_CONFIGURATION"):
        BedrockCatalogue(BedrockCatalogueConfig(REGION, credential))


@pytest.mark.parametrize("agreement", ["PENDING", "ERROR"])
def test_native_agreement_nonready_state_remains_literal_metadata(wire, agreement):
    catalog, sts, bedrock = wire
    read(sts, bedrock, "get_foundation_model", {"modelDetails": foundation()}, {"modelIdentifier": MODEL})
    read(
        sts,
        bedrock,
        "get_foundation_model_availability",
        {
            "modelId": MODEL,
            "authorizationStatus": "NOT_AUTHORIZED",
            "entitlementAvailability": "NOT_AVAILABLE",
            "regionAvailability": "AVAILABLE",
            "agreementAvailability": {"status": agreement, "errorMessage": PRIVATE},
        },
        {"modelId": MODEL},
    )
    result = catalog.detail(BedrockSourceKind.FOUNDATION_MODEL, MODEL, check_availability=True)
    assert result.state == CatalogueState.METADATA
    assert result.source.availability.authorization == "NOT_AUTHORIZED"
    assert result.source.availability.agreement == agreement
    assert result.source.availability.invoke_access == "unknown"
    assert PRIVATE not in repr(result)


def test_unknown_availability_enum_does_not_destroy_metadata(wire):
    catalog, sts, bedrock = wire
    read(sts, bedrock, "get_foundation_model", {"modelDetails": foundation()}, {"modelIdentifier": MODEL})
    read(
        sts,
        bedrock,
        "get_foundation_model_availability",
        {
            "modelId": MODEL,
            "authorizationStatus": "FUTURE_STATUS",
            "entitlementAvailability": "AVAILABLE",
            "regionAvailability": "AVAILABLE",
            "agreementAvailability": {"status": "AVAILABLE"},
        },
        {"modelId": MODEL},
    )
    result = catalog.detail(BedrockSourceKind.FOUNDATION_MODEL, MODEL, check_availability=True)
    assert result.state == CatalogueState.METADATA
    assert result.source.availability.state == CatalogueState.ERROR
    assert result.source.availability.authorization is None
    assert result.source.availability.invoke_access == "unknown"


def test_maximum_empty_page_scan_is_bounded(wire):
    catalog, sts, bedrock = wire
    for page in range(5):
        params = {"maxResults": 100}
        if page:
            params["nextToken"] = f"next-{page}"
        read(
            sts,
            bedrock,
            "list_inference_profiles",
            {"inferenceProfileSummaries": [], "nextToken": f"next-{page + 1}"},
            params,
        )
    result = catalog.inference_profiles(limit=500)
    assert result.state == CatalogueState.METADATA and result.truncated
    assert result.items == () and not result.partial


def test_duplicate_profile_identity_is_not_counted_as_another_resource(wire):
    catalog, sts, bedrock = wire
    read(
        sts,
        bedrock,
        "list_inference_profiles",
        {"inferenceProfileSummaries": [profile()], "nextToken": "more"},
        {"maxResults": 3},
    )
    read(
        sts,
        bedrock,
        "list_inference_profiles",
        {"inferenceProfileSummaries": [profile()]},
        {"maxResults": 2, "nextToken": "more"},
    )
    assert catalog.inference_profiles(limit=3).state == CatalogueState.REFUSED


def test_same_arn_changed_principal_user_id_is_refused(wire):
    catalog, sts, bedrock = wire
    identity(sts)
    bedrock.add_response("list_foundation_models", {"modelSummaries": [foundation()]}, {})
    identity(sts, user_id="OTHER_ROLE:fixture")
    assert catalog.foundation_models().state == CatalogueState.REFUSED


@pytest.mark.parametrize("matching", [True, False])
def test_declared_assumed_role_requires_actual_sts_role_and_session(matching):
    session = boto3.Session(aws_access_key_id="fixture-only", aws_secret_access_key="fixture-only", region_name=REGION)
    credential = CloudCredential(
        "aws",
        mode=CredentialMode.AWS_ASSUME_ROLE,
        declared_account=ACCOUNT,
        role_arn=f"arn:aws:iam::{ACCOUNT}:role/platform/catalogue",
        session_name="catalogue",
    )
    catalog = BedrockCatalogue(BedrockCatalogueConfig(REGION, credential), session=session)
    catalog._clients()
    with Stubber(catalog._sts) as sts, Stubber(catalog._bedrock) as bedrock:
        identity(
            sts,
            arn=f"arn:aws:sts::{ACCOUNT}:assumed-role/{'catalogue' if matching else 'other'}/catalogue",
            times=2 if matching else 1,
        )
        if matching:
            bedrock.add_response("list_foundation_models", {"modelSummaries": []}, {})
        result = catalog.foundation_models()
        assert result.state == (CatalogueState.METADATA if matching else CatalogueState.REFUSED)
        sts.assert_no_pending_responses()
        bedrock.assert_no_pending_responses()


@pytest.mark.parametrize("region,partition", [("us-gov-west-1", "aws-us-gov"), ("cn-north-1", "aws-cn")])
def test_partition_identity_is_derived_from_native_sdk(region, partition):
    session = boto3.Session(aws_access_key_id="fixture-only", aws_secret_access_key="fixture-only", region_name=region)
    catalog = BedrockCatalogue(
        BedrockCatalogueConfig(region, CloudCredential("aws", declared_account=ACCOUNT)), session=session
    )
    catalog._clients()
    with Stubber(catalog._sts) as sts, Stubber(catalog._bedrock) as bedrock:
        identity(sts, arn=f"arn:{partition}:sts::{ACCOUNT}:assumed-role/fixture/catalogue", times=2)
        bedrock.add_response(
            "get_foundation_model",
            {"modelDetails": foundation(modelArn=f"arn:{partition}:bedrock:{region}::foundation-model/{MODEL}")},
            {"modelIdentifier": MODEL},
        )
        result = catalog.detail(BedrockSourceKind.FOUNDATION_MODEL, MODEL)
        assert result.state == CatalogueState.METADATA and result.identity.partition == partition
        sts.assert_no_pending_responses()
        bedrock.assert_no_pending_responses()


def test_failed_metadata_call_with_changed_principal_cannot_release_partial_rows(wire):
    catalog, sts, bedrock = wire
    read(
        sts,
        bedrock,
        "list_inference_profiles",
        {"inferenceProfileSummaries": [profile()], "nextToken": "more"},
        {"maxResults": 3},
    )
    identity(sts)
    identity(sts, user_id="OTHER_ROLE:fixture")
    bedrock.add_client_error(
        "list_inference_profiles",
        service_error_code="AccessDeniedException",
        service_message=PRIVATE,
        expected_params={"maxResults": 2, "nextToken": "more"},
    )
    result = catalog.inference_profiles(limit=3)
    assert result.state == CatalogueState.REFUSED and result.items == () and not result.partial
    assert PRIVATE not in repr(result)


def test_unverified_post_availability_identity_cannot_escape_as_unknown_metadata(wire):
    catalog, sts, bedrock = wire
    read(sts, bedrock, "get_foundation_model", {"modelDetails": foundation()}, {"modelIdentifier": MODEL})
    identity(sts)
    bedrock.add_client_error(
        "get_foundation_model_availability",
        service_error_code="AccessDeniedException",
        service_message=PRIVATE,
        expected_params={"modelId": MODEL},
    )
    sts.add_client_error(
        "get_caller_identity", service_error_code="AccessDenied", service_message=PRIVATE, expected_params={}
    )
    result = catalog.detail(BedrockSourceKind.FOUNDATION_MODEL, MODEL, check_availability=True)
    assert result.state == CatalogueState.REFUSED and result.source is None
    assert result.reason == "IDENTITY_UNVERIFIED" and PRIVATE not in repr(result)


@pytest.mark.parametrize(
    "region,partition", [(REGION, "aws"), ("us-gov-west-1", "aws-us-gov"), ("cn-north-1", "aws-cn")]
)
def test_actual_client_construction_ignores_process_endpoint_overrides(monkeypatch, region, partition):
    # Real private-session construction only: no request, factory or saved credentials.
    for variable in ("AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_STS", "AWS_ENDPOINT_URL_BEDROCK"):
        monkeypatch.setenv(variable, "https://untrusted-override.invalid/private")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fixture-only")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fixture-only")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "fixture-only")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with BedrockCatalogue(BedrockCatalogueConfig(region, CloudCredential("aws", declared_account=ACCOUNT))) as catalog:
        catalog._clients()
        for client in (catalog._sts, catalog._bedrock):
            assert "untrusted-override.invalid" not in client.meta.endpoint_url
            assert client.meta.partition == partition and client.meta.region_name == region
            assert client.meta.config.connect_timeout == 5 and client.meta.config.read_timeout == 20
            assert client.meta.config.retries["total_max_attempts"] == 1
        assert catalog._session._session.get_default_client_config().ignore_configured_endpoint_urls is True


def test_native_fips_routing_survives_endpoint_override_refusal(monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://untrusted-override.invalid")
    monkeypatch.setenv("AWS_USE_FIPS_ENDPOINT", "true")
    session = boto3.Session(aws_access_key_id="fixture-only", aws_secret_access_key="fixture-only", region_name=REGION)
    with BedrockCatalogue(
        BedrockCatalogueConfig(REGION, CloudCredential("aws", declared_account=ACCOUNT)), session=session
    ) as catalog:
        catalog._clients()
        assert "fips" in catalog._sts.meta.endpoint_url
        assert "fips" in catalog._bedrock.meta.endpoint_url
        assert "untrusted" not in catalog._bedrock.meta.endpoint_url


@pytest.mark.parametrize(
    "field,value",
    [
        ("modelName", {"private": PRIVATE}),
        ("modelName", ""),
        ("modelName", "x" * 257),
        ("modelName", "unsafe\nname"),
        ("providerName", 42),
        ("providerName", "x" * 257),
        ("inputModalities", "TEXT"),
        ("inputModalities", [None]),
        ("inputModalities", ["TEXT", "TEXT"]),
        ("outputModalities", {"TEXT": True}),
        ("outputModalities", ["x" * 65]),
        ("inferenceTypesSupported", ["ON_DEMAND"] * 17),
        ("inferenceTypesSupported", [False]),
        ("responseStreamingSupported", "true"),
        ("responseStreamingSupported", 1),
        ("modelLifecycle", "ACTIVE"),
        ("modelLifecycle", {"status": {"private": PRIVATE}}),
        ("modelLifecycle", {"status": "x" * 65}),
    ],
)
def test_projected_foundation_metadata_validator_rejects_malformed_native_values(wire, field, value):
    # A validator boundary proof, deliberately not a malformed Stubber response.
    catalog, sts, _ = wire
    identity(sts)
    catalog._verify()
    with pytest.raises(BedrockCatalogueError, match="INVALID_METADATA") as caught:
        catalog._foundation(foundation(**{field: value}))
    assert catalog._failure(caught.value) == (CatalogueState.ERROR, "INVALID_METADATA")
    assert PRIVATE not in str(caught.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("inferenceProfileName", 42),
        ("inferenceProfileName", {"private": PRIVATE}),
        ("inferenceProfileName", "x" * 65),
        ("status", {"private": PRIVATE}),
        ("status", "x" * 65),
        ("status", "unsafe\x7fstatus"),
        ("models", [None]),
    ],
)
def test_projected_profile_metadata_validator_rejects_malformed_native_values(wire, field, value):
    catalog, sts, _ = wire
    identity(sts)
    catalog._verify()
    with pytest.raises(BedrockCatalogueError, match="INVALID_METADATA") as caught:
        catalog._profile(profile(**{field: value}))
    assert catalog._failure(caught.value) == (CatalogueState.ERROR, "INVALID_METADATA")
    assert PRIVATE not in str(caught.value)


def test_missing_optional_foundation_metadata_has_typed_unknown_projection(wire):
    catalog, sts, _ = wire
    identity(sts)
    catalog._verify()
    result = catalog._foundation({"modelId": MODEL, "modelArn": MODEL_ARN})
    assert result.name == MODEL and result.provider is None and result.streaming is None and result.lifecycle is None
    assert result.input_modalities == result.output_modalities == result.inference_types == ()


def test_context_closes_each_private_client_once_and_refuses_reuse(wire, monkeypatch):
    catalog, _, _ = wire
    closed = []
    for label, client in (("sts", catalog._sts), ("bedrock", catalog._bedrock)):
        original = client.close

        def close(label=label, original=original):
            closed.append(label)
            original()

        monkeypatch.setattr(client, "close", close)
    with pytest.raises(RuntimeError, match="controlled"), catalog:
        raise RuntimeError("controlled")
    catalog.close()
    assert sorted(closed) == ["bedrock", "sts"]
    result = catalog.foundation_models()
    assert result.state == CatalogueState.REFUSED and result.reason == "CLOSED"
    with pytest.raises(BedrockCatalogueError, match="CLOSED"):
        catalog.__enter__()


def test_context_does_not_construct_clients_when_unused():
    with BedrockCatalogue(BedrockCatalogueConfig(REGION, CloudCredential("aws", declared_account=ACCOUNT))) as catalog:
        assert catalog._sts is None and catalog._bedrock is None
    assert catalog._closed


def test_actual_botocore_json_decoder_does_not_certify_projected_string_type(wire):
    import json

    from botocore.parsers import create_parser

    catalog, sts, _ = wire
    identity(sts)
    catalog._verify()
    shape = catalog._bedrock.meta.service_model.operation_model("GetFoundationModel").output_shape
    parsed = create_parser("rest-json").parse(
        {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"modelDetails": foundation(modelName={"private": PRIVATE})}).encode(),
        },
        shape,
    )
    assert isinstance(parsed["modelDetails"]["modelName"], dict)
    with pytest.raises(BedrockCatalogueError, match="INVALID_METADATA"):
        catalog._foundation(parsed["modelDetails"])
