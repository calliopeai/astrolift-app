"""Bounded registered transports and durable collector checkpoints (#1706)."""

from __future__ import annotations

import copy
import hashlib
import hmac
import time
from dataclasses import asdict
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from django.db import transaction
from django.utils import timezone

from astrolift_clusters import agent_install
from astrolift_clusters import log_collector as service
from core.cluster_credentials import credential_for_cluster
from core.permissions import PermissionDenied


class DurableCollectorGate:
    def __init__(self, operation_id, generation, execution):
        self.operation_id, self.generation, self.execution = operation_id, generation, execution
        self.atomic = None
        self.row = self.cluster = self.plugin = None
        self.refusal = None

    def close(self, exception=None):
        if self.atomic is not None:
            atomic, self.atomic = self.atomic, None
            atomic.__exit__(type(exception) if exception else None, exception, None)

    def check(self, action):
        self.close()
        if self.refusal is not None:
            raise self.refusal
        self.atomic = transaction.atomic()
        self.atomic.__enter__()
        try:
            self.row, self.cluster, self.plugin = service._locked(self.operation_id)
            workflow_id, run_id, workflow_type = self.execution
            if (
                self.row.generation != self.generation
                or workflow_id != self.row.workflow_id
                or workflow_type != "InstallClusterLogCollectorWorkflow"
                or not run_id
                or self.row.workflow_run_id != run_id
                or self.row.terminal
            ):
                raise service.CollectorError("STALE_ATTEMPT", "Collector execution is no longer current")
            service._authorize(self.row, self.cluster, self.plugin)
            self.row.stage = action[:96]
            self.row.save()
        except (service.CollectorError, agent_install.AgentInstallError, PermissionDenied) as exc:
            self.refusal = service.CollectorError(
                getattr(exc, "code", "PERMISSION_DENIED"),
                "Original collector authority or reviewed source changed; existing reader remains active",
            )
            self.close(exc)
            raise self.refusal from None
        except Exception as exc:
            self.close(exc)
            raise

    def load(self):
        return copy.deepcopy(self.row.checkpoints.get("executor", {}))

    def save(self, state):
        self.row.checkpoints["executor"] = copy.deepcopy(state)
        self.row.cleanup_pending = any(
            k.startswith("v1/Pod/") for k in state.get("resources", {})
        ) and not state.get("probe_deleted", False)
        self.row.save()
        self.close()

    def metadata(self, key, value):
        if self.atomic is None:
            self.check("checkpoint." + key)
        self.row.checkpoints[key] = copy.deepcopy(value)
        self.row.save()
        self.close()

    def identity(self, key, value):
        if self.atomic is None:
            self.check("identity." + key)
        prior = self.row.checkpoints.get(key)
        if prior is not None and prior != value:
            self.refusal = service.CollectorError(
                "RESOURCE_REPLACED",
                "Recorded collector infrastructure was replaced; operator review is required",
            )
            raise self.refusal
        self.metadata(key, value)


class _GuardedCredentials:
    def __init__(self, original, gate):
        self.original, self.gate = original, gate

    def get_frozen_credentials(self):
        self.gate.check("aws.credentials.refresh")
        return self.original.get_frozen_credentials()

    def __getattr__(self, name):
        return getattr(self.original, name)


class RegisteredAwsClients:
    def __init__(self, gate, credential, region, *, session=None):
        import boto3
        from botocore.config import Config

        self.gate, self.credential, self.region = gate, credential, region
        gate.check("aws.session.construct")
        self.session = session or boto3.Session(region_name=region)
        self.config = Config(connect_timeout=5, read_timeout=20, retries={"total_max_attempts": 1})
        botocore_session = self.session._session
        botocore_session.set_default_client_config(self.config)
        botocore_session.set_config_variable("metadata_service_timeout", 5)
        botocore_session.set_config_variable("metadata_service_num_attempts", 1)
        resolver = botocore_session.get_component("credential_provider")
        for provider in resolver.providers:
            fetcher = getattr(provider, "_fetcher", None) or getattr(provider, "_role_fetcher", None)
            transport = getattr(fetcher, "_session", None)
            if transport is not None:
                original_send = transport.send

                def guarded_send(request, *, send=original_send):
                    gate.check("aws.credentials.http")
                    return send(request)

                transport.send = guarded_send
        self.session.events.register("before-send.*.*", lambda **kw: gate.check("aws.http"))
        self.clients = {}
        self.ambient_sts = self._build("sts", region_name=region)

    def _build(self, name, **kwargs):
        self.gate.check("aws.client." + name)
        kwargs.setdefault("config", self.config)
        client = self.session.client(name, **kwargs)
        credentials = client._request_signer._credentials
        if credentials is not None:
            client._request_signer._credentials = _GuardedCredentials(credentials, self.gate)
        return client

    def client(self, name, *, region, credential):
        from aws.session import aws_client

        if region != self.region or credential != self.credential:
            raise service.CollectorError("CREDENTIAL_CHANGED", "Registered collector identity changed")
        if name not in self.clients:
            self.gate.check("aws.registered." + name)
            raw = aws_client(
                name, region=region, credential=credential, build=self._build, sts=self.ambient_sts
            )
            self.clients[name] = _InfrastructureClient(name, raw, self)
        return self.clients[name]

    def signing_session(self):
        from aws.session import aws_session

        self.gate.check("aws.eks.signing_session")
        session = aws_session(
            region=self.region, credential=self.credential, build=self._signing_build, sts=self.ambient_sts
        )
        return session

    def _signing_build(self, **kwargs):
        import boto3

        # Ambient credentials come from the same private session, retaining its
        # before-send and refresh gates; assumed static credentials share cache.
        if "aws_access_key_id" not in kwargs:
            session = self.session
        else:
            session = boto3.Session(**kwargs)
        credentials = session.get_credentials()
        if credentials is not None and not isinstance(credentials, _GuardedCredentials):
            session._session._credentials = _GuardedCredentials(credentials, self.gate)
        return session

    def close(self):
        self.ambient_sts.close()
        for client in self.clients.values():
            client.raw.close()


class _InfrastructureClient:
    def __init__(self, name, raw, owner):
        self.name, self.raw, self.owner = name, raw, owner

    def _record(self, method, result, kwargs):
        gate = self.owner.gate
        candidate = service._candidate(gate.cluster)
        if self.name == "iam" and method in {"get_role", "create_role"}:
            role = result.get("Role", {})
            if role.get("Path") != "/astrolift/" or not _owned(role.get("Tags", []), str(gate.cluster.guid)):
                raise service.CollectorError(
                    "RESOURCE_UNOWNED", "Collector role is not owned by the reviewed cluster"
                )
            identity = {"arn": role.get("Arn"), "id": role.get("RoleId")}
            if (
                identity["arn"] != candidate.irsa_role_arn
                or not isinstance(identity["id"], str)
                or not identity["id"]
            ):
                raise service.CollectorError(
                    "RESOURCE_IDENTITY_INVALID", "Collector role identity is unavailable"
                )
            gate.identity("role_identity", identity)
        if self.name == "logs" and method == "describe_log_groups":
            for group in result.get("logGroups", []):
                if group.get("logGroupName") == candidate.log_group:
                    identity = {
                        "arn": str(group.get("arn", "")).removesuffix(":*"),
                        "created": group.get("creationTime"),
                    }
                    if identity["arn"] != candidate.log_group_arn or type(identity["created"]) is not int:
                        raise service.CollectorError(
                            "RESOURCE_IDENTITY_INVALID", "Collector log group identity is unavailable"
                        )
                    gate.identity("group_identity", identity)
        if self.name == "eks" and method == "describe_cluster":
            cluster = result.get("cluster", {})
            if (
                cluster.get("arn") != candidate.cluster_arn
                or cluster.get("status") != "ACTIVE"
                or cluster.get("endpoint") != gate.cluster.endpoint
                or (cluster.get("certificateAuthority") or {}).get("data") != gate.cluster.ca_cert
            ):
                raise service.CollectorError(
                    "EKS_TARGET_MISMATCH",
                    "Registered EKS endpoint, CA or immutable identity does not match discovery",
                )

    def __getattr__(self, method):
        function = getattr(self.raw, method)

        def checked_call(**kwargs):
            gate = self.owner.gate
            gate.check("aws." + self.name + "." + method)
            if self.name == "logs" and method == "filter_log_events":
                _recheck_infrastructure(gate, self.owner)
            effect = method.startswith(("create_", "put_", "update_", "delete_", "attach_", "tag_"))
            if effect:
                if method in {
                    "delete_role",
                    "delete_log_group",
                    "attach_role_policy",
                    "tag_role",
                    "tag_resource",
                }:
                    raise service.CollectorError(
                        "EFFECT_UNSUPPORTED",
                        "External policy or infrastructure lifecycle mutation is not supported",
                    )
                candidate = service._candidate(gate.cluster)
                if self.name == "iam" and method != "create_role":
                    gate.check("aws.role.recheck")
                    current = self.raw.get_role(RoleName=f"astrolift-{gate.cluster.guid}-fluent-bit")
                    self._record("get_role", current, {})
                if self.name == "logs" and method != "create_log_group":
                    gate.check("aws.group.recheck")
                    current = self.raw.describe_log_groups(logGroupNamePrefix=candidate.log_group, limit=1)
                    if not any(
                        g.get("logGroupName") == candidate.log_group for g in current.get("logGroups", [])
                    ):
                        raise service.CollectorError(
                            "RESOURCE_REPLACED", "Recorded collector log group disappeared"
                        )
                    self._record("describe_log_groups", current, {})
                gate.check("aws.intent." + method)
                gate.metadata(
                    "aws_intent",
                    {"service": self.name, "action": method, "hash": agent_install._digest(kwargs)},
                )
                gate.check("aws.effect." + method)
            result = function(**kwargs)
            self._record(method, result, kwargs)
            if self.name == "logs" and method == "create_log_group":
                gate.check("aws.group.created")
                current = self.raw.describe_log_groups(logGroupNamePrefix=kwargs["logGroupName"], limit=1)
                self._record("describe_log_groups", current, {})
                if "group_identity" not in gate.row.checkpoints:
                    raise service.CollectorError(
                        "RESOURCE_IDENTITY_INVALID", "Created collector log group could not be confirmed"
                    )
            return result

        def call(**kwargs):
            try:
                return checked_call(**kwargs)
            except service.CollectorError as exc:
                self.owner.gate.refusal = exc
                raise

        return call


def _recheck_infrastructure(gate, clients):
    credential = credential_for_cluster(gate.cluster)
    candidate = service._candidate(gate.cluster)
    iam = clients.client("iam", region=candidate.region, credential=credential)
    logs = clients.client("logs", region=candidate.region, credential=credential)
    iam.get_role(RoleName=f"astrolift-{gate.cluster.guid}-fluent-bit")
    groups = logs.describe_log_groups(logGroupNamePrefix=candidate.log_group, limit=1)
    if not any(item.get("logGroupName") == candidate.log_group for item in groups.get("logGroups", [])):
        exc = service.CollectorError("RESOURCE_REPLACED", "Recorded collector log group disappeared")
        gate.refusal = exc
        raise exc
    tags = logs.list_tags_for_resource(resourceArn=candidate.log_group_arn).get("tags", {})
    if not _owned(tags, str(gate.cluster.guid)):
        exc = service.CollectorError("RESOURCE_REPLACED", "Recorded collector log group ownership changed")
        gate.refusal = exc
        raise exc


def _owned(tags, guid):
    if isinstance(tags, list):
        tags = {item.get("Key"): item.get("Value") for item in tags}
    return isinstance(tags, dict) and all(
        tags.get(key) == value
        for key, value in {
            "astrolift.io/managed-by": "platform",
            "astrolift.io/cluster": guid,
            "astrolift.io/component": "fluent-bit-cloudwatch",
        }.items()
    )


def _archive(gate):
    from aws._cloudwatch_collector import PIN

    gate.check("chart.fetch")
    url = service._policy()["chart_archive_url"]

    class GuardedRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, newurl):
            gate.check("chart.redirect")
            target = urlsplit(newurl)
            if (
                target.scheme != "https"
                or target.hostname != "release-assets.githubusercontent.com"
                or target.port not in {None, 443}
                or target.username
                or target.password
            ):
                raise service.CollectorError(
                    "CHART_SOURCE_CHANGED", "Collector chart redirected outside its pinned upstream transport"
                )
            return super().redirect_request(request, fp, code, message, headers, newurl)

    with build_opener(GuardedRedirect()).open(url, timeout=10) as response:
        data = b""
        while True:
            gate.check("chart.read")
            chunk = response.read(65536)
            if not chunk:
                break
            data += chunk
            if len(data) > 10_000_000:
                raise service.CollectorError("CHART_LIMIT", "Collector chart exceeded its bounded size")
    if hashlib.sha256(data).hexdigest() != PIN["archiveSha256"]:
        raise service.CollectorError(
            "CHART_DIGEST_MISMATCH", "Collector chart digest does not match the reviewed pin"
        )
    return data


def _registered_transport(gate, clients):
    from _sdk.k8s_dynamic_client import KubernetesDynamicClient
    from aws._eks_auth import mint_eks_token

    gate.check("kubernetes.registered.construct")
    candidate = service._candidate(gate.cluster)

    def token():
        gate.check("kubernetes.registered.refresh")
        return mint_eks_token(
            cluster_name=candidate.cluster_arn.rsplit("/", 1)[1],
            region=candidate.region,
            session=clients.signing_session(),
        )

    return KubernetesDynamicClient(
        endpoint=gate.cluster.endpoint, ca_data=gate.cluster.ca_cert, token_provider=token
    )


def _namespace_and_coverage(gate, registered):
    import re

    from aws.cloudwatch_collector_execution import bounded_registered_kubernetes

    gate.check("kubernetes.preflight")
    bounded = bounded_registered_kubernetes(registered, checkpoints=gate)
    try:
        namespace = bounded.get(kind="v1/Namespace", name="astrolift-system", namespace=None)
        if not agent_install._namespace_owned(gate.cluster, namespace) or namespace.get("metadata", {}).get(
            "deletionTimestamp"
        ):
            raise service.CollectorError(
                "NAMESPACE_UNOWNED", "A live platform-owned collector namespace is required"
            )
        identity = {"uid": namespace["metadata"].get("uid"), "name": "astrolift-system"}
        if not identity["uid"]:
            raise service.CollectorError("NAMESPACE_UNOWNED", "Collector namespace identity is unavailable")
        gate.identity("namespace_identity", identity)
        nodes = bounded.list(kind="v1/Node", namespace=None)
        candidate = service._candidate(gate.cluster)
        eligible = [
            node
            for node in nodes
            if node.get("metadata", {}).get("labels", {}).get("kubernetes.io/os") == "linux"
            and node.get("metadata", {}).get("labels", {}).get("eks.amazonaws.com/compute-type")
            in {None, "ec2"}
            and re.fullmatch(
                rf"aws:///{re.escape(candidate.region)}[-a-z0-9]*/i-(?:[0-9a-f]{{8}}|[0-9a-f]{{17}})",
                node.get("spec", {}).get("providerID", ""),
            )
        ]
        if not eligible:
            raise service.CollectorError(
                "UNSUPPORTED_NODE_COVERAGE",
                "This node collector requires Linux EC2 workers; Fargate is unsupported",
            )
    finally:
        bounded._api_client.close()


def _reader(gate, clients, stage):
    from _sdk.observability.cloudwatch_logs import CloudWatchLogsConfig, CloudWatchLogsQueryDriver
    from aws.cloudwatch_collector_execution import ReaderBinding, ReaderGrantPending
    from botocore.exceptions import ClientError

    gate.check("reader.grant.check")
    client = clients.client("logs", region=stage.region, credential=credential_for_cluster(gate.cluster))
    # Distinguish a verified AWS denial before the SDK's sanitized generic error
    # boundary. The registered reader is never granted or replaced automatically.
    try:
        client.filter_log_events(
            logGroupName=stage.log_group,
            filterPattern='{ $.kubernetes.namespace_name = "astrolift-system" && $.kubernetes.labels.[\'astrolift.io/app\'] = "collector-probe" }',
            startTime=int(gate.row.query_since.timestamp() * 1000),
            endTime=int(gate.row.query_until.timestamp() * 1000),
            limit=1,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"AccessDenied", "AccessDeniedException"}:
            raise ReaderGrantPending from None
        raise RuntimeError("Collector reader preflight could not be confirmed") from None
    driver = CloudWatchLogsQueryDriver(
        config=CloudWatchLogsConfig(
            log_group=stage.log_group,
            region=stage.region,
            log_stream_name_prefix="from-fluent-bit-",
            credential=credential_for_cluster(gate.cluster),
            client=client,
        )
    )
    return ReaderBinding(
        driver, str(gate.cluster.guid), stage.log_group, stage.region, gate.row.source_digest
    )


def _activate(gate, result, stage, clients, registered):
    _namespace_and_coverage(gate, registered)
    _recheck_infrastructure(gate, clients)
    gate.check("reader.activate")
    row, cluster, plugin = gate.row, gate.cluster, gate.plugin
    state = row.checkpoints.get("executor", {})
    if (
        result.state != "POST_LOSS_READ_VERIFIED"
        or result.cleanup_pending
        or not result.event_hash
        or not state.get("probe_deleted")
        or state.get("event_hash") != result.event_hash
        or not state.get("delete_intent")
        or state.get("delete_refused")
        or not row.checkpoints.get("role_identity")
        or not row.checkpoints.get("group_identity")
    ):
        raise service.CollectorError(
            "PROOF_INCOMPLETE", "Historical read and exact probe cleanup proof are incomplete"
        )
    if not hmac.compare_digest(agent_install._digest(service._binding(cluster)), row.binding_digest):
        raise service.CollectorError("BINDING_CHANGED", "Existing reader binding changed; activation refused")
    candidate = stage.candidate_reader_config()
    cluster.provider_config = {**cluster.provider_config, **candidate}
    cluster.save(update_fields=["provider_config", "version", "updated_at"])
    now = timezone.now()
    row.status = "activated"
    row.stage = "reader.activated"
    row.coverage = result.coverage
    row.cleanup_pending = False
    row.event_hash = result.event_hash
    row.post_loss_verified_at = now
    row.activated_at = now
    row.activated_cluster_version = cluster.version
    row.activation_source_digest = service._source(cluster, plugin, row.retention_days)
    row.error_code = ""
    row.error_message = ""
    row.save()
    gate.close()
    return "activated"


def install_attempt(
    operation_id,
    generation,
    *,
    execution=None,
    client_session=None,
    archive_fetcher=_archive,
    transport_factory=_registered_transport,
    renderer=None,
    idle=lambda: time.sleep(3),
):
    from aws.cloudwatch_collector import CollectorSpec, prepare_collector
    from aws.cloudwatch_collector_execution import CollectorExecutor, ExecutionRefused, ExecutionRequest
    from aws.cloudwatch_collector_render import CollectorRenderError, render_collector

    if execution is None or not isinstance(execution, tuple) or len(execution) != 3:
        return "refused"
    with transaction.atomic():
        row, cluster, plugin = service._locked(operation_id)
        workflow_id, run_id, workflow_type = execution
        if (
            row.generation != generation
            or workflow_id != row.workflow_id
            or workflow_type != "InstallClusterLogCollectorWorkflow"
            or not run_id
            or row.workflow_run_id
            and row.workflow_run_id != run_id
        ):
            return "refused"
        if row.terminal:
            return row.status
        row.workflow_run_id = run_id
        row.save()
    gate = DurableCollectorGate(operation_id, generation, execution)
    clients = registered = None
    try:
        gate.check("preflight")
        candidate = service._support(gate.cluster, gate.plugin, gate.row.retention_days)
        archive = archive_fetcher(gate)
        from aws._cloudwatch_collector import PIN

        if (
            not isinstance(archive, bytes)
            or len(archive) > 10_000_000
            or hashlib.sha256(archive).hexdigest() != PIN["archiveSha256"]
        ):
            raise service.CollectorError(
                "CHART_DIGEST_MISMATCH", "Collector chart digest does not match the reviewed pin"
            )
        gate.check("aws.prepare")
        gate.row.status = "preparing"
        gate.row.save()
        credential = credential_for_cluster(gate.cluster)
        clients = RegisteredAwsClients(gate, credential, candidate.region, session=client_session)
        clients.client("eks", region=candidate.region, credential=credential).describe_cluster(
            name=candidate.cluster_arn.rsplit("/", 1)[1]
        )
        registered = transport_factory(gate, clients)
        _namespace_and_coverage(gate, registered)
        gate.check("chart.runtime.preflight")
        try:
            (renderer or render_collector)(
                candidate.component(),
                archive=archive,
                namespace="astrolift-system",
                release_name="fluent-bit",
            )
        except CollectorRenderError:
            raise service.CollectorError(
                "VERIFIED_RENDERER_UNAVAILABLE", "The verified collector renderer is unavailable"
            ) from None
        gate.check("aws.prepare")
        stage = prepare_collector(
            CollectorSpec(
                str(gate.cluster.guid),
                candidate.cluster_arn.rsplit("/", 1)[1],
                candidate.region,
                credential,
                retention_days=gate.row.retention_days,
            ),
            client_factory=clients.client,
        )
        gate.check("checkpoint.prepared")
        prior = gate.row.checkpoints.get("prepared")
        if prior and prior != asdict(stage):
            raise service.CollectorError(
                "PREPARED_SOURCE_CHANGED", "Prepared collector infrastructure identity changed"
            )
        gate.metadata("prepared", asdict(stage))
        gate.check("kubernetes.construct")
        gate.check("collector.execute")
        gate.row.status = "installing"
        gate.row.save()
        request = ExecutionRequest(
            operation_guid=str(gate.row.guid),
            since=gate.row.query_since.isoformat(),
            until=gate.row.query_until.isoformat(),
            probe_image=gate.row.probe_image,
            reader_source=gate.row.source_digest,
        )
        with CollectorExecutor(
            stage=stage,
            request=request,
            archive=archive,
            renderer=renderer or render_collector,
            kubernetes=registered,
            reader_factory=lambda prepared: _reader(gate, clients, prepared),
            checkpoints=gate,
            idle=idle,
        ) as executor:
            result = executor.run()
        gate.check("collector.result")
        if result.state == "POST_LOSS_READ_VERIFIED":
            return _activate(gate, result, stage, clients, registered)
        mapping = {
            "READINESS_PENDING": "readiness_pending",
            "READER_GRANT_PENDING": "reader_grant_pending",
            "INGESTION_PENDING": "ingestion_pending",
            "PROBE_DELETION_PENDING": "probe_deletion_pending",
            "POST_LOSS_READ_PENDING": "post_loss_read_pending",
            "PROVIDER_OUTCOME_UNCERTAIN": "uncertain",
        }
        gate.row.status = mapping.get(result.state, "uncertain")
        gate.row.coverage = result.coverage
        gate.row.cleanup_pending = result.cleanup_pending
        gate.row.error_code = "PROVIDER_OUTCOME_UNCERTAIN" if gate.row.status == "uncertain" else ""
        gate.row.error_message = (
            "Collector provider outcome could not be confirmed; resume the original request"
            if gate.row.status == "uncertain"
            else ""
        )
        gate.row.save()
        gate.close()
        return gate.row.status
    except Exception as exc:
        exc = gate.refusal or exc
        gate.close(exc)
        with transaction.atomic():
            row, cluster, plugin = service._locked(operation_id)
            if row.generation != generation or row.workflow_run_id != execution[1] or row.terminal:
                return row.status
            refused = isinstance(
                exc,
                (service.CollectorError, agent_install.AgentInstallError, PermissionDenied, ExecutionRefused),
            )
            row.status = "refused" if refused else "uncertain"
            if getattr(exc, "code", "") == "UNSUPPORTED_NODE_COVERAGE":
                row.coverage = "UNSUPPORTED"
            row.cleanup_pending = row.cleanup_pending or bool(getattr(exc, "cleanup_pending", False))
            row.error_code = (
                getattr(exc, "code", "PROVIDER_OUTCOME_UNCERTAIN")
                if refused
                else "PROVIDER_OUTCOME_UNCERTAIN"
            )
            row.error_message = (
                "Collector admission or resource identity was refused; existing reader remains active"
                if refused
                else "Collector provider outcome could not be confirmed; resume the original request"
            )
            row.save()
            return row.status
    finally:
        gate.close()
        if clients is not None:
            clients.close()
        if registered is not None:
            registered._api_client.close()
