"""Opt-in actual CNPG/SQL acceptance; explicit private kind credentials only."""

import base64
import json
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from _sdk.cluster import ApplyError, ApplyResult
from _sdk.k8s_dynamic_client import KubernetesDynamicClient
from _sdk.k8s_naming import app_namespace
from asgiref.sync import async_to_sync
from k8s_native.managed._handle import unpack
from k8s_native.managed.postgres_cnpg import CNPGPostgresDriver
from temporalio.testing import ActivityEnvironment

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment, PreviewEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from astrolift_workflows.activities.app_lifecycle import (
    _update_secrets_sync,
    cleanup_preview_managed_services_activity,
    provision_preview_managed_services_activity,
)
from astrolift_workflows.activities.managed_service_lifecycle import _sync_binding_rows, build_provision_spec
from core.cluster_observability import managed_config_for

pytestmark = pytest.mark.django_db(transaction=True)


def require(condition, reason):
    # Assert rewriting must never display a generated password or Secret payload.
    if not condition:
        raise AssertionError(reason)


class FileStore:
    """Controlled durable test adapter, not live Vault acceptance."""

    def __init__(self, directory):
        self.directory, self.writes = directory, []

    def _file(self, path):
        import hashlib

        return self.directory / hashlib.sha256(path.encode()).hexdigest()

    def get(self, path):
        file = self._file(path)
        return json.loads(file.read_text()) if file.exists() else None

    def upsert(self, path, payload):
        self.writes.append(path)
        descriptor = os.open(self._file(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(payload, stream)


def test_actual_cnpg_preview_authentication_retry_privileges_and_cleanup(monkeypatch, tmp_path):
    kubeconfig = os.environ.get("ASTROLIFT_CNPG_SLICE_KUBECONFIG")
    if not kubeconfig:
        pytest.skip("requires owned astrolift-cnpg-slice-2092 private kubeconfig and CNPG 1.30.1")
    require(Path(kubeconfig).is_absolute(), "requires an absolute private kubeconfig")
    context = "kind-astrolift-cnpg-slice-2092"

    def kubectl(*args, body=None, timeout=45, success=True):
        result = subprocess.run(
            ["kubectl", "--kubeconfig", kubeconfig, "--context", context, *args],
            input=json.dumps(body) if body is not None else None,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        if success and result.returncode:
            raise AssertionError(f"controlled kubectl failed ({args[0]}); payload/diagnostics suppressed")
        return result

    require(kubectl("config", "current-context").stdout.strip() == context, "private context mismatch")
    server_version = json.loads(kubectl("version", "-o", "json").stdout)["serverVersion"]["gitVersion"]

    def wait_for(resource, namespace, condition):
        for _ in range(6):
            if not kubectl(
                "wait", resource, "-n", namespace, condition, "--timeout=30s", timeout=35, success=False
            ).returncode:
                return
        raise AssertionError("owned operator resource did not reach expected readiness")

    operator = json.loads(
        kubectl("get", "deploy/cnpg-controller-manager", "-n", "cnpg-system", "-o", "json").stdout
    )
    operator_image = operator["spec"]["template"]["spec"]["containers"][0]["image"]
    require(operator_image.endswith(":1.30.1"), "operator differs from pinned contract")
    from k8s_native import cluster as cluster_module
    from kubernetes import client, config

    def private_client(**kwargs):
        require(
            kwargs["kubeconfig_path"] == kubeconfig and kwargs["context"] == context,
            "driver escaped private kubeconfig",
        )
        configuration = client.Configuration()
        config.load_kube_config(config_file=kubeconfig, context=context, client_configuration=configuration)
        return KubernetesDynamicClient.from_api_client(
            api_client=client.ApiClient(configuration=configuration)
        )

    monkeypatch.setattr(cluster_module, "_build_k8s_client", private_client)
    suffix = uuid.uuid4().hex[:8]
    org = Organization.objects.create(name="Owned CNPG acceptance", slug=f"cnpg-org-{suffix}")
    team = Team.objects.create(organization=org, name="Team", slug="team")
    project = Project.objects.create(organization=org, team=team, name="Project", slug="project")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug=f"cnpg-app-{suffix}",
        provisioning_status="ready",
        manifest_raw=f'name = "cnpg-app-{suffix}"\n',
    )
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="k8s_native", defaults={"name": "Native", "plugin_version": "test"}
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        provider_plugin=plugin,
        name="Owned kind CNPG",
        slug=f"cnpg-cluster-{suffix}",
        lifecycle="managed",
        provider_config={"kubeconfig_path": kubeconfig, "context": context, "cnpg_storage_class": "standard"},
    )
    primary = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="production", k8s_namespace=f"cnpg-primary-{suffix}"
    )
    preview = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="preview",
        previewed_environment=primary,
        k8s_namespace=f"cnpg-consumer-{suffix}",
    )
    preview_row = PreviewEnvironment.objects.create(
        registered_app=app,
        app_environment=preview,
        branch="acceptance",
        pr_number=2092,
        hostname="owned.invalid",
        namespace=preview.k8s_namespace,
    )
    service = ManagedService.objects.create(
        registered_app=app,
        app_environment=primary,
        kind="postgres",
        variant="cnpg",
        name=f"parent-{suffix}",
        config={"storage_size": "1Gi", "version": "16"},
        status="provisioning",
    )
    store = FileStore(tmp_path)

    def capability(target, capability):
        require(target.pk == cluster.pk and capability == "secrets", "store escaped owned source")
        return store

    monkeypatch.setattr("core.app_deploy.driver_for_capability", capability)
    driver = CNPGPostgresDriver(
        config=managed_config_for("k8s_native", cluster, kind="postgres", variant="cnpg")
    )
    service_ns = app_namespace(organization_slug=org.slug, app_slug=app.slug)
    namespaces = [service_ns, preview.k8s_namespace]
    try:
        for namespace in namespaces:
            kubectl("create", "namespace", namespace)
        result = driver.provision(build_provision_spec(service, cluster=cluster))
        require(result.ok, "actual parent provision refused")
        service.backend_ref = result.handle
        service.save(update_fields=["backend_ref"])
        parent = unpack(result.handle)
        require(
            parent.namespace == service_ns and parent.cluster_id == str(cluster.guid),
            "physical locator disagrees",
        )
        wait_for(f"cluster/{parent.name}", service_ns, "--for=condition=Ready")
        service.status = "active"
        service.save(update_fields=["status"])
        _sync_binding_rows(service)
        actual_parent = json.loads(
            kubectl("get", f"cluster/{parent.name}", "-n", service_ns, "-o", "json").stdout
        )
        primary_pod = actual_parent["status"]["currentPrimary"]

        def admin(sql, database="postgres"):
            return kubectl(
                "exec",
                "-n",
                service_ns,
                primary_pod,
                "-c",
                "postgres",
                "--",
                "psql",
                "-XAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "postgres",
                "-d",
                database,
                "-c",
                sql,
            ).stdout.strip()

        admin(
            "CREATE TABLE parent_private(marker text); INSERT INTO parent_private VALUES ('parent-intact'); ALTER TABLE parent_private OWNER TO app",
            "app",
        )
        native_apply = cluster_module.K8sNativeClusterDriver.apply_manifests
        faulted = []

        def fail_one_database(self, target, namespace, manifests, **kwargs):
            if namespace == service_ns and manifests[0]["kind"] == "Database" and not faulted:
                faulted.append(True)
                return ApplyResult(
                    created=[],
                    updated=[],
                    unchanged=[],
                    errors=[
                        ApplyError(
                            kind="Database",
                            name=manifests[0]["metadata"]["name"],
                            namespace=namespace,
                            exception_type="ControlledAcceptanceFault",
                            exception_message="owned Database create withheld",
                            is_retryable=True,
                        )
                    ],
                )
            applied = native_apply(self, target, namespace, manifests, **kwargs)
            if applied.errors:
                print(
                    "CONTROLLED_APPLY_ERROR_CLASS",
                    [
                        (error.exception_type, "409" in error.exception_message, error.is_retryable)
                        for error in applied.errors
                    ],
                )
            return applied

        monkeypatch.setattr(cluster_module.K8sNativeClusterDriver, "apply_manifests", fail_one_database)
        activity_environment = ActivityEnvironment()
        first = async_to_sync(activity_environment.run)(
            provision_preview_managed_services_activity, preview_row.pk
        )
        require(bool(first["errors"]) and not first["attached"], "partial provision advertised attachment")
        require(
            not ManagedServiceAttachment.objects.filter(app_environment=preview).exists(),
            "partial result persisted attachment",
        )
        require(
            bool(faulted) and len(store.writes) == 1,
            "partial creation did not issue one independent envelope",
        )
        path = f"services/{org.guid}/{app.guid}/cnpg-slices/{service.guid}/{preview.guid}"
        issued = FileStore(tmp_path).get(path)
        require(issued is not None, "envelope did not survive a fresh adapter")
        before = json.loads(
            kubectl(
                "get",
                "secret",
                "-n",
                service_ns,
                "-l",
                f"ai.astrolift/environment-id={preview.guid}",
                "-o",
                "json",
            ).stdout
        )["items"][0]
        credential_uid = before["metadata"]["uid"]
        healthy = async_to_sync(activity_environment.run)(
            provision_preview_managed_services_activity, preview_row.pk
        )
        require(
            not healthy["errors"] and healthy["sliced"] == [service.name], "retry did not attach owned slice"
        )
        require(
            FileStore(tmp_path).get(path) == issued and len(store.writes) == 1, "retry replaced credentials"
        )
        attachment = ManagedServiceAttachment.objects.get(managed_service=service, app_environment=preview)
        slice_handle = attachment.slice_handle
        database = unpack(slice_handle).name
        database_objects = json.loads(
            kubectl(
                "get",
                "databases",
                "-n",
                service_ns,
                "-l",
                f"ai.astrolift/environment-id={preview.guid}",
                "-o",
                "json",
            ).stdout
        )["items"]
        require(len(database_objects) == 1, "retry duplicated Database")
        database_name = database_objects[0]["metadata"]["name"]
        wait_for(f"database/{database_name}", service_ns, "--for=jsonpath={.status.applied}=true")
        observed_database = json.loads(
            kubectl("get", f"database/{database_name}", "-n", service_ns, "-o", "json").stdout
        )
        require(
            observed_database["status"]["observedGeneration"] == observed_database["metadata"]["generation"],
            "operator applied status names an older Database generation",
        )
        deployment = Deployment.objects.create(
            registered_app=app,
            app_environment=preview,
            trigger_kind="manual",
            status="pending",
            image_tag="owned-cnpg-2092",
        )
        _update_secrets_sync(deployment.pk)
        consumer_secret_name = f"astrolift-bindings-{app.slug}"
        consumer_secret = json.loads(
            kubectl("get", f"secret/{consumer_secret_name}", "-n", preview.k8s_namespace, "-o", "json").stdout
        )
        env = {key: base64.b64decode(value).decode() for key, value in consumer_secret["data"].items()}
        require(
            env["POSTGRES_USER"] == env["DATABASE_USER"] == issued["username"],
            "username aliases name another identity",
        )
        require(
            env["POSTGRES_DB"] == env["DATABASE_NAME"] == database, "database aliases fell back to parent"
        )
        require(
            env["POSTGRES_PASSWORD"] == env["DATABASE_PASSWORD"] == issued["password"]
            and env["DATABASE_URL"] == issued["uri"],
            "consumer credentials differ from issued envelope",
        )
        parent_secret = json.loads(
            kubectl("get", f"secret/{parent.name}-app", "-n", service_ns, "-o", "json").stdout
        )
        require(
            issued["password"] != base64.b64decode(parent_secret["data"]["password"]).decode(),
            "slice reused parent credentials",
        )
        kubectl(
            "apply",
            "-f",
            "-",
            body={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "consumer", "namespace": preview.k8s_namespace},
                "spec": {
                    "containers": [
                        {
                            "name": "client",
                            "image": "postgres:16-alpine",
                            "imagePullPolicy": "Never",
                            "command": ["sh", "-c", "sleep 3600"],
                            "envFrom": [{"secretRef": {"name": consumer_secret_name}}],
                        }
                    ]
                },
            },
        )
        kubectl("wait", "pod/consumer", "-n", preview.k8s_namespace, "--for=condition=Ready", "--timeout=30s")

        def consumer(sql, *, db=None, user=None, uri=False, tls=None, expect_success=True):
            script = 'export PGHOST="$POSTGRES_HOST" PGPORT="$POSTGRES_PORT" PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD" PGDATABASE="$POSTGRES_DB" PGSSLMODE="$POSTGRES_SSL_MODE" PGCONNECT_TIMEOUT=5; '
            if uri:
                script += (
                    r"""{ printf '%s\n' '\setenv PGHOST' '\setenv PGPORT' '\setenv PGUSER' '\setenv PGPASSWORD' '\setenv PGDATABASE'; printf '\\connect -reuse-previous=off "%s"\n' "$DATABASE_URL"; printf '%s\n' """
                    + shlex.quote(sql + ";")
                    + "; } | psql -XqAt -v ON_ERROR_STOP=1"
                )
            else:
                if db is not None:
                    script += f"export PGDATABASE='{db}'; "
                if user is not None:
                    script += f"export PGUSER='{user}'; "
                if tls is not None:
                    script += f"export PGSSLMODE='{tls}'; "
                script += "psql -XAt -v ON_ERROR_STOP=1 -c " + shlex.quote(sql)
            response = kubectl(
                "exec",
                "-n",
                preview.k8s_namespace,
                "consumer",
                "-c",
                "client",
                "--",
                "sh",
                "-c",
                script,
                success=False,
            )
            require(
                (response.returncode == 0) == expect_success,
                "actual SQL authentication/privilege expectation failed",
            )
            return response.stdout.strip()

        identity = consumer("SELECT current_user || '|' || current_database()")
        require(
            identity == f"{issued['username']}|{database}",
            "host aliases authenticated as another identity/database",
        )
        require(
            consumer("SELECT current_user || '|' || current_database()", uri=True) == identity,
            "DATABASE_URL did not authenticate",
        )
        require(
            consumer("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()")
            == issued["username"],
            "actual database owner differs",
        )
        require(
            consumer(
                "SELECT rolsuper || '|' || rolcreatedb || '|' || rolcreaterole || '|' || rolreplication || '|' || rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            == "false|false|false|false|false",
            "slice role has elevated privileges",
        )
        consumer(
            "CREATE TABLE slice_owned(id int PRIMARY KEY); INSERT INTO slice_owned VALUES (2092); SELECT id FROM slice_owned"
        )
        consumer("CREATE ROLE forbidden_slice_role", expect_success=False)
        consumer("CREATE DATABASE forbidden_slice_database", expect_success=False)
        require(
            consumer(
                "SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)"
            )
            == "0",
            "slice role inherits another role's privileges",
        )
        consumer("SELECT marker FROM parent_private", db="app", expect_success=False)
        consumer("SELECT current_user", user="app", db="app", expect_success=False)
        print(
            "Real operator issued independent credentials; host/URI authentication, database ownership, DML and privilege limits passed."
        )
        consumer("SELECT current_database()", db="app", expect_success=False)
        consumer("SELECT current_database()", db="postgres", expect_success=False)
        consumer("SELECT current_database()", tls="disable", expect_success=False)
        repeat = async_to_sync(activity_environment.run)(
            provision_preview_managed_services_activity, preview_row.pk
        )
        require(not repeat["errors"] and len(store.writes) == 1, "repeat rotated credentials or failed")
        retained = json.loads(
            kubectl("get", f"secret/{before['metadata']['name']}", "-n", service_ns, "-o", "json").stdout
        )
        require(retained["metadata"]["uid"] == credential_uid, "repeat replaced credential Secret")
        require(consumer("SELECT id FROM slice_owned") == "2092", "repeat lost content/login")
        hold = "acceptance.astrolift.io/hold"
        ready_database = json.loads(
            kubectl("get", f"database/{database_name}", "-n", service_ns, "-o", "json").stdout
        )
        kubectl(
            "patch",
            f"database/{database_name}",
            "-n",
            service_ns,
            "--type=merge",
            "-p",
            json.dumps(
                {"metadata": {"finalizers": ready_database["metadata"].get("finalizers", []) + [hold]}}
            ),
        )
        cleanup = async_to_sync(activity_environment.run)(
            cleanup_preview_managed_services_activity, preview_row.pk
        )
        require(
            not cleanup["dropped"] and cleanup["leaked"] == [service.name],
            "pending finalizer advertised completed cleanup",
        )
        attachment.refresh_from_db()
        require(attachment.slice_handle == slice_handle, "pending cleanup discarded retry identity")
        require(
            kubectl("get", f"secret/{before['metadata']['name']}", "-n", service_ns, success=False).returncode
            == 0,
            "cleanup deleted credentials before finalizer completion",
        )
        for _ in range(25):
            pending = json.loads(
                kubectl("get", f"database/{database_name}", "-n", service_ns, "-o", "json").stdout
            )
            if (
                pending["metadata"].get("finalizers") == [hold]
                and admin(f"SELECT count(*) FROM pg_database WHERE datname='{database}'") == "0"
            ):
                break
            time.sleep(1)
        require(
            pending["metadata"].get("finalizers") == [hold],
            "operator did not finish physical Database cleanup",
        )
        kubectl(
            "patch",
            f"database/{database_name}",
            "-n",
            service_ns,
            "--type=json",
            "-p",
            json.dumps([{"op": "remove", "path": "/metadata/finalizers"}]),
        )
        cleanup = async_to_sync(activity_environment.run)(
            cleanup_preview_managed_services_activity, preview_row.pk
        )
        require(cleanup["dropped"] == [service.name], "actual cleanup did not complete")
        require(
            admin(f"SELECT count(*) FROM pg_database WHERE datname='{database}'") == "0",
            "physical slice database retained",
        )
        require(
            admin("SELECT marker FROM parent_private", "app") == "parent-intact",
            "cleanup changed parent content",
        )
        require(
            admin(f"SELECT rolcanlogin FROM pg_roles WHERE rolname='{issued['username']}'") == "t",
            "retained-role design unexpectedly claimed revocation",
        )
        require(FileStore(tmp_path).get(path) == issued, "cleanup changed portable credentials")
        print(
            json.dumps(
                {
                    "operator": operator_image,
                    "kubernetes": server_version,
                    "postgresql": admin("SHOW server_version"),
                    "organization_id": str(org.guid),
                    "app_id": str(app.guid),
                    "environment_id": str(preview.guid),
                    "managed_service_id": str(service.guid),
                    "cluster_id": str(cluster.guid),
                    "parent_namespace": service_ns,
                    "consumer_namespace": preview.k8s_namespace,
                    "slice_handle": slice_handle,
                    "credential_secret_uid": credential_uid,
                    "consumer_secret_uid": consumer_secret["metadata"]["uid"],
                    "consumer_binding_keys": sorted(env),
                    "database_object_uid": observed_database["metadata"]["uid"],
                    "database_applied_generation": observed_database["status"]["observedGeneration"],
                    "database_owner": issued["username"],
                    "credential_store_path": path,
                    "postgres_and_uri_authentication": True,
                    "independent_credentials": True,
                    "parent_default_login_denied": True,
                    "retry_password_retained": True,
                    "database_removed": True,
                    "role_and_portable_envelope_retained": True,
                }
            )
        )
    finally:
        for namespace in reversed(namespaces):
            kubectl(
                "delete", "namespace", namespace, "--ignore-not-found=true", "--wait=false", success=False
            )
        for file in tmp_path.iterdir():
            file.unlink()
