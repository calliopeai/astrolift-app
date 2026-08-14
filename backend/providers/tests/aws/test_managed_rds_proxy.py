from __future__ import annotations

from typing import ClassVar

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from aws.managed.rds_proxy import RDSProxyConfig, RDSProxyDriver

from .test_managed_aurora import NotFound


class EntityAlreadyExistsException(Exception):
    pass


class CloudFailure(Exception):
    def __init__(self, code: str, status: int):
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }
        super().__init__(code)


class FakeRDS:
    def __init__(self):
        self.proxies: dict[str, dict] = {}
        self.creates: list[dict] = []
        self.targets: list[dict] = []
        self.modifies: list[dict] = []
        self.target_group_modifies: list[dict] = []
        self.deletes: list[dict] = []

    def describe_db_proxies(self, **kwargs):
        name = kwargs["DBProxyName"]
        if name not in self.proxies:
            raise NotFound("DBProxyNotFoundFault")
        return {"DBProxies": [self.proxies[name]]}

    def create_db_proxy(self, **kwargs):
        self.creates.append(kwargs)
        self.proxies[kwargs["DBProxyName"]] = {
            **kwargs,
            "Status": "creating",
            "Endpoint": f"{kwargs['DBProxyName']}.proxy.example",
            "DBProxyArn": f"arn:aws:rds:::db-proxy:{kwargs['DBProxyName']}",
        }

    def register_db_proxy_targets(self, **kwargs):
        self.targets.append(kwargs)

    def modify_db_proxy(self, **kwargs):
        self.modifies.append(kwargs)
        self.proxies[kwargs["DBProxyName"]].update(kwargs)

    def modify_db_proxy_target_group(self, **kwargs):
        self.target_group_modifies.append(kwargs)

    def delete_db_proxy(self, **kwargs):
        self.deletes.append(kwargs)
        self.proxies[kwargs["DBProxyName"]]["Status"] = "deleting"


class FakeIAM:
    def __init__(self):
        self.roles: dict[str, str] = {}
        self.policies: list[dict] = []
        self.deleted: list[str] = []

    def create_role(self, **kwargs):
        name = kwargs["RoleName"]
        if name in self.roles:
            raise EntityAlreadyExistsException("EntityAlreadyExists")
        arn = f"arn:aws:iam::123456789012:role/{name}"
        self.roles[name] = arn
        return {"Role": {"Arn": arn}}

    def get_role(self, **kwargs):
        return {"Role": {"Arn": self.roles[kwargs["RoleName"]]}}

    def put_role_policy(self, **kwargs):
        self.policies.append(kwargs)

    def delete_role_policy(self, **kwargs):
        self.policies = [row for row in self.policies if row["RoleName"] != kwargs["RoleName"]]

    def delete_role(self, **kwargs):
        self.deleted.append(kwargs["RoleName"])
        self.roles.pop(kwargs["RoleName"], None)


def spec(**overrides):
    config = {
        "engine_family": "POSTGRESQL",
        "secret_arn": "arn:aws:secretsmanager:us-west-2:123456789012:secret:db",
        "db_cluster_identifier": "aurora-primary",
        **overrides,
    }
    return ProvisionSpec(
        organization_id="org",
        organization_slug="acme",
        app_id="app",
        app_slug="api",
        environment_id="env",
        environment_name="prod",
        tenant_cluster_id="cluster",
        service_handle_hint="pool",
        size="small",
        config=config,
    )


def driver(*, role_arn=""):
    rds = FakeRDS()
    iam = FakeIAM()
    subject = RDSProxyDriver(
        config=RDSProxyConfig(
            region="us-west-2",
            vpc_subnet_ids=["subnet-a", "subnet-b"],
            vpc_security_group_ids=["sg-proxy"],
            role_arn=role_arn,
        ),
        rds_client=rds,
        iam_client=iam,
    )
    return subject, rds, iam


def test_provision_creates_least_privilege_role_proxy_and_target_idempotently():
    subject, rds, iam = driver()

    result = subject.provision(spec(kms_key_arn="arn:aws:kms:us-west-2:123:key/one"))
    again = subject.provision(spec(kms_key_arn="arn:aws:kms:us-west-2:123:key/one"))

    assert result.ok and again.ok
    assert result.handle == "database_proxy/astrolift-acme-api-prod-pool"
    assert len(rds.creates) == 1
    assert len(rds.targets) == 2
    create = rds.creates[0]
    assert create["EngineFamily"] == "POSTGRESQL"
    assert create["VpcSubnetIds"] == ["subnet-a", "subnet-b"]
    assert create["RequireTLS"] is True
    assert iam.policies
    assert "secretsmanager:GetSecretValue" in iam.policies[0]["PolicyDocument"]
    assert "kms:Decrypt" in iam.policies[0]["PolicyDocument"]
    assert rds.targets[0]["DBClusterIdentifiers"] == ["aurora-primary"]


def test_binding_status_and_update():
    subject, rds, _ = driver(role_arn="arn:aws:iam::123:role/existing")
    result = subject.provision(spec())
    name = result.handle.split("/", 1)[1]
    rds.proxies[name]["Status"] = "available"

    status = subject.status(ServiceHandle(result.handle))
    binding = subject.binding(ServiceHandle(result.handle))
    updated = subject.update(
        UpdateSpec(
            result.handle,
            config={
                "idle_client_timeout": 900,
                "debug_logging": True,
                "max_connections_percent": 80,
            },
        ),
    )

    assert status.state == "available"
    assert binding.env_vars["DATABASE_PROXY_HOST"].literal.endswith(".proxy.example")
    assert binding.env_vars["DATABASE_PROXY_PORT"].literal == "5432"
    assert binding.env_vars["DATABASE_PROXY_TLS"].literal == "require"
    assert binding.env_vars["DATABASE_PROXY_AUTH_MODE"].literal == "secret"
    assert binding.env_vars["DATABASE_PROXY_CREDENTIALS"].secret_ref == spec().config["secret_arn"]
    assert binding.iam_grants[0].actions == ["secretsmanager:GetSecretValue"]
    assert updated.ok
    assert rds.modifies[-1]["IdleClientTimeout"] == 900
    assert rds.target_group_modifies[-1]["ConnectionPoolConfig"] == {
        "MaxConnectionsPercent": 80,
    }


def test_iam_auth_only_update_preserves_existing_secret_entries():
    subject, rds, _ = driver()
    result = subject.provision(spec())

    updated = subject.update(UpdateSpec(result.handle, config={"iam_auth": "REQUIRED"}))

    assert updated.ok
    assert rds.modifies[-1]["Auth"] == [
        {
            "AuthScheme": "SECRETS",
            "SecretArn": spec().config["secret_arn"],
            "Description": "Astrolift managed database credentials",
            "IAMAuth": "REQUIRED",
        },
    ]


def test_secret_rotation_reconciles_managed_role_policy():
    subject, rds, iam = driver()
    result = subject.provision(spec())
    rotated_arn = "arn:aws:secretsmanager:us-west-2:123456789012:secret:rotated"

    updated = subject.update(
        UpdateSpec(result.handle, config={"secret_arn": rotated_arn}),
    )

    assert updated.ok
    assert rds.modifies[-1]["Auth"][0]["SecretArn"] == rotated_arn
    assert rotated_arn in iam.policies[-1]["PolicyDocument"]


def test_repeated_provision_reconciles_auth_instead_of_only_discovering():
    subject, rds, iam = driver()
    result = subject.provision(spec())
    rotated_arn = "arn:aws:secretsmanager:us-west-2:123456789012:secret:reconciled"

    reconciled = subject.provision(spec(secret_arn=rotated_arn))

    assert result.ok and reconciled.ok
    assert len(rds.creates) == 1
    assert rds.modifies[-1]["Auth"][0]["SecretArn"] == rotated_arn
    assert rotated_arn in iam.policies[-1]["PolicyDocument"]


def test_malformed_auth_returns_a_validation_result_instead_of_raising():
    subject, rds, _ = driver()

    created = subject.provision(spec(auth="not-a-list", secret_arn=""))
    updated = subject.update(UpdateSpec("database_proxy/existing", config={"auth": [{}]}))

    assert not created.ok and not updated.ok
    assert created.errors == ["invalid_proxy_config"]
    assert updated.errors == ["invalid_proxy_config"]
    assert not rds.creates


def test_invalid_or_ambiguous_target_fails_before_cloud_calls():
    subject, rds, _ = driver()
    missing = subject.provision(spec(db_cluster_identifier=""))
    ambiguous = subject.provision(spec(db_instance_identifier="instance", db_cluster_identifier="cluster"))

    assert not missing.ok and not ambiguous.ok
    assert not rds.creates


def test_enabled_iam_auth_is_limited_to_sql_server():
    subject, rds, _ = driver()

    invalid = subject.provision(spec(iam_auth="ENABLED"))
    valid = subject.provision(spec(engine_family="SQLSERVER", iam_auth="ENABLED"))

    assert not invalid.ok
    assert valid.ok
    assert rds.creates[-1]["EngineFamily"] == "SQLSERVER"


def test_end_to_end_iam_auth_needs_no_secret_and_scopes_managed_role():
    subject, rds, iam = driver()
    dbuser_arn = "arn:aws:rds-db:us-west-2:123456789012:dbuser:cluster-123/app_user"

    result = subject.provision(
        spec(default_auth_scheme="IAM_AUTH", secret_arn="", iam_dbuser_arns=[dbuser_arn]),
    )

    assert result.ok
    assert "Auth" not in rds.creates[-1]
    assert rds.creates[-1]["DefaultAuthScheme"] == "IAM_AUTH"
    assert "rds-db:connect" in iam.policies[-1]["PolicyDocument"]
    assert dbuser_arn in iam.policies[-1]["PolicyDocument"]
    binding = subject.binding(
        ServiceHandle(result.handle),
        config={"iam_dbuser_arns": [dbuser_arn]},
    )
    assert binding.env_vars["DATABASE_PROXY_AUTH_MODE"].literal == "iam"
    assert binding.env_vars["DATABASE_PROXY_DB_USER"].literal == "app_user"
    assert binding.iam_grants[-1] == type(binding.iam_grants[-1])(
        dbuser_arn,
        ["rds-db:connect"],
    )


def test_multiple_auth_secrets_preserve_per_user_options():
    subject, rds, _ = driver(role_arn="arn:aws:iam::123:role/existing")
    result = subject.provision(
        spec(
            secret_arn="",
            auth=[
                {
                    "secret_arn": "arn:aws:secretsmanager:us-west-2:123:secret:one",
                    "iam_auth": "REQUIRED",
                    "username": "app_user",
                    "client_password_auth_type": "POSTGRES_SCRAM_SHA_256",
                },
                {
                    "secret_arn": "arn:aws:secretsmanager:us-west-2:123:secret:two",
                    "iam_auth": "DISABLED",
                },
            ],
        ),
    )

    assert result.ok
    assert len(rds.creates[-1]["Auth"]) == 2
    assert rds.creates[-1]["Auth"][0]["UserName"] == "app_user"
    assert rds.creates[-1]["Auth"][0]["ClientPasswordAuthType"] == "POSTGRES_SCRAM_SHA_256"


def test_already_registered_target_is_idempotent():
    class AlreadyRegistered(Exception):
        response: ClassVar[dict] = {
            "Error": {"Code": "DBProxyTargetAlreadyRegisteredFault"},
        }

    def already_registered(**kwargs):
        del kwargs
        raise AlreadyRegistered

    subject, rds, _ = driver()
    result = subject.provision(spec())
    rds.register_db_proxy_targets = already_registered

    again = subject.provision(spec())

    assert result.ok and again.ok


def test_deprovision_retries_until_proxy_is_gone_then_cleans_managed_role():
    subject, rds, iam = driver()
    result = subject.provision(spec())
    name = result.handle.split("/", 1)[1]

    queued = subject.deprovision(DeprovisionSpec(result.handle, config=spec().config))
    waiting = subject.deprovision(DeprovisionSpec(result.handle, config=spec().config))
    rds.proxies.pop(name)
    complete = subject.deprovision(DeprovisionSpec(result.handle, config=spec().config))

    assert not queued.ok and not waiting.ok
    assert complete.ok
    assert iam.deleted


def test_deprovision_does_not_hide_permanent_iam_cleanup_failure():
    subject, rds, iam = driver()
    result = subject.provision(spec())
    name = result.handle.split("/", 1)[1]
    rds.proxies.pop(name)

    def denied(**kwargs):
        del kwargs
        raise CloudFailure("AccessDenied", 403)

    iam.delete_role_policy = denied
    cleaned = subject.deprovision(DeprovisionSpec(result.handle, config=spec().config))

    assert not cleaned.ok
    assert not cleaned.retryable
    assert "AccessDenied" in cleaned.message


def test_deprovision_marks_transient_iam_cleanup_failure_retryable():
    subject, rds, iam = driver()
    result = subject.provision(spec())
    name = result.handle.split("/", 1)[1]
    rds.proxies.pop(name)

    def unavailable(**kwargs):
        del kwargs
        raise CloudFailure("ServiceUnavailable", 503)

    iam.delete_role_policy = unavailable
    cleaned = subject.deprovision(DeprovisionSpec(result.handle, config=spec().config))

    assert not cleaned.ok
    assert cleaned.retryable


def test_current_botocore_accepts_all_rds_proxy_request_shapes():
    from botocore.session import Session
    from botocore.validate import validate_parameters

    subject, rds, _ = driver(role_arn="arn:aws:iam::123456789012:role/existing")
    result = subject.provision(
        spec(
            endpoint_network_type="DUAL",
            target_connection_network_type="IPV4",
            default_auth_scheme="NONE",
            max_connections_percent=75,
            max_idle_connections_percent=40,
            connection_borrow_timeout=30,
            session_pinning_filters=["EXCLUDE_VARIABLE_SETS"],
            init_query="SET application_name = 'astrolift'",
        ),
    )
    updated = subject.update(
        UpdateSpec(
            result.handle,
            config={
                "secret_arn": spec().config["secret_arn"],
                "default_auth_scheme": "NONE",
                "require_tls": True,
                "max_connections_percent": 80,
            },
        ),
    )

    assert result.ok and updated.ok
    service = Session().get_service_model("rds")
    for operation, request in (
        ("CreateDBProxy", rds.creates[-1]),
        ("RegisterDBProxyTargets", rds.targets[-1]),
        ("ModifyDBProxy", rds.modifies[-1]),
        ("ModifyDBProxyTargetGroup", rds.target_group_modifies[-1]),
    ):
        validate_parameters(request, service.operation_model(operation).input_shape)
