"""Tests for the CloudFront CDN managed-service driver (#1010).

The astrolift-local container has no moto, so these exercise the driver
against a stateful recording fake CloudFront/S3 client.
"""

from __future__ import annotations

import json

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
)
from aws.managed._base import parse_handle
from aws.managed.cdn_cloudfront import (
    KIND,
    CloudFrontConfig,
    CloudFrontDriver,
)

# ---- recording fakes -------------------------------------------------


class _DistributionAlreadyExists(Exception):  # noqa: N818 -- mimics SDK class name
    pass


class _CNAMEAlreadyExists(Exception):  # noqa: N818 -- mimics SDK class name
    pass


class _NoSuchDistribution(Exception):  # noqa: N818 -- mimics SDK class name
    pass


class _OriginAccessControlAlreadyExists(Exception):  # noqa: N818 -- mimics SDK class name
    pass


class _CFExceptions:
    DistributionAlreadyExists = _DistributionAlreadyExists
    CNAMEAlreadyExists = _CNAMEAlreadyExists
    NoSuchDistribution = _NoSuchDistribution
    OriginAccessControlAlreadyExists = _OriginAccessControlAlreadyExists


class FakeCloudFront:
    """Stateful recording fake. Methods take ``**kwargs`` so the
    driver's boto3-fidelity PascalCase keyword args land here as-is."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.exceptions = _CFExceptions()
        self._dists: dict[str, dict] = {}
        self._oacs: dict[str, str] = {}
        self._etag = 0
        self._dist_n = 0
        self._oac_n = 0
        self.invalidations: list[dict] = []
        # test toggles
        self.raise_create_already_exists = False
        self.raise_oac_already_exists = False

    def _next_etag(self) -> str:
        self._etag += 1
        return f"etag-{self._etag}"

    # -- OAC --
    def create_origin_access_control(self, **kwargs):
        oac_config = kwargs["OriginAccessControlConfig"]
        self.calls.append(("create_oac", oac_config))
        if self.raise_oac_already_exists:
            raise _OriginAccessControlAlreadyExists("exists")
        self._oac_n += 1
        oac_id = f"oac-{self._oac_n}"
        self._oacs[oac_id] = oac_config["Name"]
        return {"OriginAccessControl": {"Id": oac_id}}

    def list_origin_access_controls(self, **kwargs):
        items = [{"Id": k, "Name": v} for k, v in self._oacs.items()]
        return {"OriginAccessControlList": {"Items": items}}

    def get_origin_access_control(self, **kwargs):
        return {"ETag": self._next_etag()}

    def delete_origin_access_control(self, **kwargs):
        self.calls.append(("delete_oac", kwargs["Id"]))
        self._oacs.pop(kwargs["Id"], None)

    # -- distribution --
    def _seed(self, dist_id: str, config: dict, status: str = "Deployed"):
        self._dists[dist_id] = {
            "config": config,
            "arn": f"arn:aws:cloudfront::111122223333:distribution/{dist_id}",
            "domain": f"{dist_id}.cloudfront.net",
            "status": status,
        }

    def create_distribution_with_tags(self, **kwargs):
        payload = kwargs["DistributionConfigWithTags"]
        self.calls.append(("create_dist", payload))
        if self.raise_create_already_exists:
            raise _DistributionAlreadyExists("exists")
        self._dist_n += 1
        dist_id = f"E{self._dist_n}ABC"
        self._seed(dist_id, payload["DistributionConfig"])
        d = self._dists[dist_id]
        return {
            "Distribution": {
                "Id": dist_id,
                "ARN": d["arn"],
                "DomainName": d["domain"],
                "Status": "InProgress",
                "DistributionConfig": d["config"],
            },
        }

    def get_distribution(self, **kwargs):
        dist_id = kwargs["Id"]
        if dist_id not in self._dists:
            raise _NoSuchDistribution(dist_id)
        d = self._dists[dist_id]
        return {
            "ETag": self._next_etag(),
            "Distribution": {
                "Id": dist_id,
                "ARN": d["arn"],
                "DomainName": d["domain"],
                "Status": d["status"],
                "DistributionConfig": d["config"],
            },
        }

    def update_distribution(self, **kwargs):
        dist_id = kwargs["Id"]
        config = kwargs["DistributionConfig"]
        self.calls.append(("update_dist", dist_id, config))
        if dist_id not in self._dists:
            raise _NoSuchDistribution(dist_id)
        self._dists[dist_id]["config"] = config
        return {
            "ETag": self._next_etag(),
            "Distribution": {"Id": dist_id, "DistributionConfig": config},
        }

    def delete_distribution(self, **kwargs):
        dist_id = kwargs["Id"]
        self.calls.append(("delete_dist", dist_id))
        if dist_id not in self._dists:
            raise _NoSuchDistribution(dist_id)
        del self._dists[dist_id]

    def list_distributions(self, **kwargs):
        items = [{"Id": k, "Comment": v["config"].get("Comment", ""), "ARN": v["arn"]} for k, v in self._dists.items()]
        return {"DistributionList": {"Items": items}}

    def create_invalidation(self, **kwargs):
        self.invalidations.append(
            {"id": kwargs["DistributionId"], "batch": kwargs["InvalidationBatch"]},
        )
        return {"Invalidation": {"Id": "I9999"}}


class FakeS3:
    def __init__(self) -> None:
        self.policies: dict[str, str] = {}

    def put_bucket_policy(self, **kwargs):
        self.policies[kwargs["Bucket"]] = kwargs["Policy"]

    def delete_bucket_policy(self, **kwargs):
        self.policies.pop(kwargs["Bucket"], None)


@pytest.fixture
def cf() -> FakeCloudFront:
    return FakeCloudFront()


@pytest.fixture
def s3() -> FakeS3:
    return FakeS3()


@pytest.fixture
def driver(cf: FakeCloudFront, s3: FakeS3) -> CloudFrontDriver:
    return CloudFrontDriver(
        config=CloudFrontConfig(),
        client=cf,
        s3_client=s3,
    )


def _spec(**overrides) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="site",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="aws-prod",
        service_handle_hint="site-cdn",
        size="small",
        managed_service_id="ms-1",
        config={
            "origin_bucket": "astrolift-acme-site-prod-assets",
            "origin_region": "us-west-2",
            "aliases": ["site.acme.example"],
            "acm_cert_arn": "arn:aws:acm:us-east-1:111122223333:certificate/abc",
            "spa": True,
            "index": "index.html",
        },
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision -------------------------------------------------------


def test_provision_creates_distribution(driver, cf, s3) -> None:
    result = driver.provision(_spec())
    assert result.ok is True
    kind, dist_id = parse_handle(result.handle)
    assert kind == KIND
    assert dist_id in cf._dists

    config = cf._dists[dist_id]["config"]
    assert config["DefaultRootObject"] == "index.html"
    # OAC wired, no legacy OAI
    origin = config["Origins"]["Items"][0]
    assert origin["OriginAccessControlId"] == "oac-1"
    assert origin["S3OriginConfig"]["OriginAccessIdentity"] == ""
    assert origin["DomainName"] == ("astrolift-acme-site-prod-assets.s3.us-west-2.amazonaws.com")


def test_provision_requires_origin_bucket(driver) -> None:
    result = driver.provision(_spec(config={"origin_bucket": ""}))
    assert result.ok is False
    assert "origin_bucket" in result.errors[0]


def test_provision_spa_error_responses(driver, cf) -> None:
    result = driver.provision(_spec())
    config = cf._dists[parse_handle(result.handle)[1]]["config"]
    items = config["CustomErrorResponses"]["Items"]
    codes = {i["ErrorCode"]: i for i in items}
    assert set(codes) == {403, 404}
    for entry in items:
        assert entry["ResponsePagePath"] == "/index.html"
        assert entry["ResponseCode"] == "200"


def test_provision_alias_and_cert_when_present(driver, cf) -> None:
    result = driver.provision(_spec())
    config = cf._dists[parse_handle(result.handle)[1]]["config"]
    assert config["Aliases"]["Items"] == ["site.acme.example"]
    cert = config["ViewerCertificate"]
    assert cert["ACMCertificateArn"].endswith("certificate/abc")
    assert "CloudFrontDefaultCertificate" not in cert


def test_provision_default_cert_when_no_cert(driver, cf) -> None:
    result = driver.provision(
        _spec(
            config={
                "origin_bucket": "b1",
                "aliases": ["x.example"],  # alias without a cert -> default cert
            }
        )
    )
    config = cf._dists[parse_handle(result.handle)[1]]["config"]
    assert config["Aliases"]["Quantity"] == 0
    assert config["ViewerCertificate"]["CloudFrontDefaultCertificate"] is True


def test_provision_sets_bucket_policy_scoped_to_distribution(
    driver,
    cf,
    s3,
) -> None:
    result = driver.provision(_spec())
    dist_id = parse_handle(result.handle)[1]
    policy = json.loads(s3.policies["astrolift-acme-site-prod-assets"])
    stmt = policy["Statement"][0]
    assert stmt["Action"] == "s3:GetObject"
    assert stmt["Principal"]["Service"] == "cloudfront.amazonaws.com"
    assert stmt["Condition"]["StringEquals"]["AWS:SourceArn"] == (cf._dists[dist_id]["arn"])


def test_provision_tags_distribution(driver, cf) -> None:
    driver.provision(_spec())
    _, payload = cf.calls[1]  # second call is create_dist
    tags = {t["Key"]: t["Value"] for t in payload["Tags"]["Items"]}
    assert tags["astrolift.io/managed-by"] == "platform"
    assert tags["astrolift.io/app"] == "site"


def test_provision_idempotent_reconcile(cf, s3) -> None:
    """A pre-existing distribution (same Comment) reconciles, no crash."""
    driver = CloudFrontDriver(
        config=CloudFrontConfig(),
        client=cf,
        s3_client=s3,
    )
    # Seed a distribution carrying the comment this spec will compute.
    cf._seed(
        "EOLD123",
        {
            "Comment": "astrolift ms-1",
            "DefaultRootObject": "old.html",
            "Origins": {
                "Items": [
                    {
                        "DomainName": "b.s3.us-west-2.amazonaws.com",
                        "OriginAccessControlId": "oac-9",
                    }
                ]
            },
        },
    )
    cf.raise_create_already_exists = True
    result = driver.provision(_spec())
    assert result.ok is True
    assert parse_handle(result.handle)[1] == "EOLD123"
    # reconciled the index back to the desired value
    assert cf._dists["EOLD123"]["config"]["DefaultRootObject"] == "index.html"


# ---- binding ---------------------------------------------------------


def test_binding_env_vars_and_grant(driver, cf) -> None:
    result = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=result.handle))
    assert set(binding.env_vars) == {
        "CDN_DISTRIBUTION_ID",
        "CDN_DOMAIN_NAME",
        "CDN_INVALIDATION_ROLE",
    }
    dist_id = parse_handle(result.handle)[1]
    assert binding.env_vars["CDN_DISTRIBUTION_ID"].literal == dist_id
    assert binding.env_vars["CDN_DOMAIN_NAME"].literal.endswith(
        ".cloudfront.net",
    )
    assert len(binding.iam_grants) == 1
    grant = binding.iam_grants[0]
    assert grant.actions == ["cloudfront:CreateInvalidation"]
    assert grant.resource == cf._dists[dist_id]["arn"]


# ---- invalidate ------------------------------------------------------


def test_invalidate_default_wildcard(driver, cf) -> None:
    out = driver.invalidate("E123ABC")
    assert out["invalidation_id"] == "I9999"
    batch = cf.invalidations[0]["batch"]
    assert batch["Paths"]["Items"] == ["/*"]
    assert batch["CallerReference"]


def test_invalidate_explicit_paths(driver, cf) -> None:
    driver.invalidate("E123ABC", ["/index.html", "/app.js"])
    assert cf.invalidations[0]["batch"]["Paths"]["Items"] == [
        "/index.html",
        "/app.js",
    ]


# ---- status ----------------------------------------------------------


def test_status_available_when_deployed(driver, cf) -> None:
    result = driver.provision(_spec())
    status = driver.status(ServiceHandle(handle=result.handle))
    assert status.state == "available"


def test_status_provisioning_when_in_progress(driver, cf) -> None:
    result = driver.provision(_spec())
    cf._dists[parse_handle(result.handle)[1]]["status"] = "InProgress"
    status = driver.status(ServiceHandle(handle=result.handle))
    assert status.state == "provisioning"


def test_status_deprovisioned_when_missing(driver) -> None:
    status = driver.status(ServiceHandle(handle="cdn/ENOPE"))
    assert status.state == "deprovisioned"


# ---- deprovision -----------------------------------------------------


def test_deprovision_disables_then_deletes(driver, cf, s3) -> None:
    result = driver.provision(_spec())
    dist_id = parse_handle(result.handle)[1]
    deprov = driver.deprovision(DeprovisionSpec(handle=result.handle))
    assert deprov.ok is True
    assert dist_id not in cf._dists
    # disable update happened before delete
    methods = [c[0] for c in cf.calls]
    assert methods.index("update_dist") < methods.index("delete_dist")
    # origin bucket policy cleaned up
    assert "astrolift-acme-site-prod-assets" not in s3.policies


def test_deprovision_idempotent_when_missing(driver) -> None:
    deprov = driver.deprovision(DeprovisionSpec(handle="cdn/ENOPE"))
    assert deprov.ok is True


# ---- ASCII / schemas -------------------------------------------------


def test_iam_and_policy_descriptions_are_ascii(driver, cf, s3) -> None:
    """#1026: no unicode in OAC description or bucket-policy Sid."""
    driver.provision(_spec())
    oac_cfg = cf.calls[0][1]
    oac_cfg["Description"].encode("ascii")  # raises if non-ASCII
    policy = json.loads(s3.policies["astrolift-acme-site-prod-assets"])
    policy["Statement"][0]["Sid"].encode("ascii")


def test_config_schema_advertises_keys(driver) -> None:
    props = driver.config_schema()["properties"]
    assert {"spa", "index", "aliases", "acm_cert_arn"} <= set(props)


def test_binding_schema_documents_env_vars(driver) -> None:
    schema = driver.binding_schema()
    assert set(schema.env_vars) == {
        "CDN_DISTRIBUTION_ID",
        "CDN_DOMAIN_NAME",
        "CDN_INVALIDATION_ROLE",
    }
