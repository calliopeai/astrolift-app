"""Standalone committed original GCP source bootstrap; no deploy/render/cleanup caller.

A typed create reply can be retained after authority withdrawal as effect evidence.
That path never creates grants, changes owners or returns an admitted context.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import ssl
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from uuid import uuid4

from django.db import connection
from django.utils import timezone
from gcp.identity_owned import NativeIdentityContext
from gcp.identity_source import (
    ClusterPin,
    CreateCommitReceipt,
    CreatedAccountEvidence,
    CreateRequest,
    IdentitySourceError,
    NativeIdentitySource,
    ProjectScope,
    SourceDeclaration,
)

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_services.gcp_app_identity_plan import (
    MAX_SOURCE_SECONDS,
    ObservedEndpoint,
    _validate_observations,
    pre_identity_endpoint_snapshot,
)
from astrolift_services.gcp_gke_preparation_journal import authority_reference_payload
from astrolift_services.gcp_workload_identity_journal import (
    _hash,
    _outside_atomic,
    _short_transaction,
    _uuid,
)
from astrolift_services.models import GCPAppIdentitySource, GCPClusterIdentitySource
from astrolift_services.native_identity_authority import current_app_identity_authority
from core.cluster_credentials import credential_for_cluster
from core.fields.uuid_v7 import uuid7


def _json(value):
    return json.loads(json.dumps(value, allow_nan=False))


def _declaration(authority):
    with current_app_identity_authority(authority) as selected:
        cluster, app = selected.tenant_cluster, selected.registered_app
        config = cluster.provider_config
        if (
            cluster.provider_plugin.slug != "gcp"
            or cluster.organization_id not in (None, app.organization_id)
            or not isinstance(config, dict)
            or not config.get("vertex_region")
        ):
            raise IdentitySourceError("EXPLICIT_VERTEX_REGION_AND_REGISTERED_GCP_SOURCE_REQUIRED")
        try:
            if not cluster.ca_cert or len(cluster.ca_cert) > 65536:
                raise ValueError
            ssl.create_default_context().load_verify_locations(
                cadata=base64.b64decode(cluster.ca_cert, validate=True).decode()
            )
        except (ValueError, UnicodeError, ssl.SSLError):
            raise IdentitySourceError("REGISTERED_CLUSTER_CERTIFICATE_UNVERIFIED") from None
        declaration = SourceDeclaration(
            str(app.organization.guid),
            str(app.guid),
            str(cluster.guid),
            config.get("project_id", ""),
            config["vertex_region"],
            config.get("gcp_location", config.get("location", "")),
            config.get("cluster_name", ""),
            cluster.endpoint,
            hashlib.sha256(cluster.ca_cert.encode()).hexdigest(),
            credential_for_cluster(cluster),
        )
        physical = {
            "cluster_id": str(cluster.guid),
            "provider_id": str(cluster.provider_plugin.guid),
            "organization_id": str(cluster.organization.guid) if cluster.organization_id else None,
            "declaration_sha256": _hash(
                (
                    declaration.project_id,
                    declaration.location,
                    declaration.cluster_name,
                    declaration.endpoint,
                    declaration.certificate_sha256,
                    asdict(declaration.credential),
                )
            ),
        }
        return declaration, physical


def _native_source_sha256(observations):
    # Availability is rechecked below but is not an immutable routing/model fact.
    return _hash(tuple((name, values[:6] + values[7:]) for name, values in observations))


def _configuration_sha256(authority):
    with current_app_identity_authority(authority) as selected:
        cluster = selected.tenant_cluster
        return _hash((cluster.provider_config, cluster.auth_config))


@dataclass(frozen=True)
class SourceOperation:
    operation_id: str
    workflow_id: str
    execution_id: str

    def __post_init__(self):
        _uuid(self.operation_id)
        if any(
            type(v) is not str or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", v)
            for v in (self.workflow_id, self.execution_id)
        ):
            raise IdentitySourceError("INVALID_ORIGINAL_OPERATION")


@dataclass(frozen=True)
class SourceReservation:
    source_id: str
    operation_id: str
    reservation_nonce: str
    original_sha256: str
    create_request_sha256: str


@dataclass(frozen=True)
class OriginalIdentity:
    source_id: str
    source_version: int
    cluster_source_id: str
    original_sha256: str
    identity: NativeIdentityContext
    cluster: ClusterPin


@contextmanager
def source_mutex(authority):
    _outside_atomic()
    if connection.vendor != "postgresql":
        raise IdentitySourceError("POSTGRESQL_REQUIRED")
    _uuid(authority.app_guid)
    _uuid(authority.cluster_guid)
    lock_id = int.from_bytes(
        hashlib.sha256(
            json.dumps(
                ("astrolift-gcp-original-source-v1", authority.app_guid, authority.cluster_guid)
            ).encode()
        ).digest()[:8],
        "big",
        signed=True,
    )
    mutex = connection.Database.connect(**connection.get_connection_params())
    try:
        mutex.autocommit = True
        with mutex.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
            if not cursor.fetchone()[0]:
                raise IdentitySourceError("SOURCE_BUSY")
        store = SourceStore(authority, mutex, lock_id)
        try:
            yield store
        finally:
            store.active = False
    finally:
        if not mutex.closed:
            try:
                with mutex.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
            finally:
                mutex.close()


class SourceStore:
    def __init__(self, authority, mutex_connection, lock_id):
        self.authority, self.active = authority, True
        self.mutex_connection, self.lock_id = mutex_connection, lock_id

    def _ready(self):
        _outside_atomic()
        if not self.active or self.mutex_connection.closed:
            raise IdentitySourceError("SOURCE_MUTEX_REQUIRED")
        with self.mutex_connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype = %s AND pid = pg_backend_pid() AND classid = %s AND objid = %s AND objsubid = 1 AND granted)",
                ["advisory", (self.lock_id >> 32) & 0xFFFFFFFF, self.lock_id & 0xFFFFFFFF],
            )
            if not cursor.fetchone()[0]:
                raise IdentitySourceError("SOURCE_MUTEX_REQUIRED")

    @contextmanager
    def _locked(self, *, current=True):
        self._ready()
        a = self.authority
        with _short_transaction():
            try:
                org = Organization._unscoped.select_for_update(nowait=True).get(guid=a.organization_guid)
                cluster = TenantCluster._unscoped.select_for_update(nowait=True).get(guid=a.cluster_guid)
                provider = ProviderPlugin._unscoped.select_for_update(nowait=True).get(guid=a.provider_guid)
                team, project = None, None
                if current:
                    app_hint = RegisteredApp._unscoped.get(guid=a.app_guid)
                    team = Team._unscoped.select_for_update(nowait=True).get(pk=app_hint.team_id)
                    if app_hint.project_id:
                        project = Project._unscoped.select_for_update(nowait=True).get(pk=app_hint.project_id)
                app = RegisteredApp._unscoped.select_for_update(nowait=True).get(guid=a.app_guid)
            except (
                Organization.DoesNotExist,
                TenantCluster.DoesNotExist,
                ProviderPlugin.DoesNotExist,
                RegisteredApp.DoesNotExist,
                Team.DoesNotExist,
                Project.DoesNotExist,
            ):
                raise IdentitySourceError("ORIGINAL_SOURCE_OWNER_UNAVAILABLE") from None
            if current and (
                app.organization_id != org.pk
                or cluster.provider_plugin_id != provider.pk
                or app.team_id != team.pk
                or app.project_id != (project.pk if project else None)
            ):
                raise IdentitySourceError("ORIGINAL_SOURCE_OWNER_CHANGED")
            pin = (
                GCPClusterIdentitySource._unscoped.select_for_update(nowait=True)
                .filter(tenant_cluster=cluster)
                .first()
            )
            row = (
                GCPAppIdentitySource._unscoped.select_for_update(nowait=True)
                .filter(registered_app=app, tenant_cluster=cluster)
                .first()
            )
            if row and (
                row.organization_id != org.pk
                or row.provider_plugin_id != provider.pk
                or pin is None
                or row.cluster_source_id != pin.pk
                or row.deleted_at is not None
            ):
                raise IdentitySourceError("ORIGINAL_SOURCE_OWNER_CHANGED")
            if pin and (pin.provider_plugin_id != provider.pk or pin.deleted_at is not None):
                raise IdentitySourceError("ORIGINAL_CLUSTER_SOURCE_CHANGED")
            if current:
                with current_app_identity_authority(a) as admitted:
                    if (
                        admitted.registered_app.pk != app.pk
                        or admitted.tenant_cluster.pk != cluster.pk
                        or cluster.organization_id not in (None, org.pk)
                        or any(r.deleted_at is not None for r in (org, cluster, provider, team, app))
                        or project is not None
                        and project.deleted_at is not None
                    ):
                        raise IdentitySourceError("CURRENT_SOURCE_UNAVAILABLE")
            yield (org, app, cluster, provider, pin, row)

    def pin(self, operation, physical, native):
        if type(operation) is not SourceOperation or type(native) is not ClusterPin:
            raise IdentitySourceError("INVALID_CLUSTER_OBSERVATION")
        snapshot = _json({**physical, "native": asdict(native)})
        with self._locked() as (org, app, cluster, provider, pin, _):
            _, current = _declaration(self.authority)
            if current != physical:
                raise IdentitySourceError("CURRENT_SOURCE_CHANGED")
            if pin:
                if pin.original_sha256 != _hash(snapshot) or pin.original_snapshot != snapshot:
                    raise IdentitySourceError("ORIGINAL_CLUSTER_INCARNATION_CHANGED")
            else:
                pin = GCPClusterIdentitySource.objects.create(
                    tenant_cluster=cluster,
                    provider_plugin=provider,
                    organization=cluster.organization,
                    original_snapshot=snapshot,
                    original_sha256=_hash(snapshot),
                    admitted_operation_id=operation.operation_id,
                )
            return str(pin.guid)

    @staticmethod
    def _reservation(row):
        return SourceReservation(
            str(row.guid),
            str(row.operation_id),
            str(row.reservation_nonce),
            row.original_sha256,
            row.create_request_sha256,
        )

    @staticmethod
    def _request(row):
        source = row.original_snapshot
        return CreateRequest(
            str(row.guid),
            str(row.operation_id),
            row.account_id,
            source["project_id"],
            source["project_number"],
            source["owner_description"],
        )

    @staticmethod
    def _bound(row, reservation):
        if row is None or SourceStore._reservation(row) != reservation:
            raise IdentitySourceError("ORIGINAL_OPERATION_CHANGED")
        if (
            _hash(row.original_snapshot) != row.original_sha256
            or SourceStore._request(row).sha256 != row.create_request_sha256
        ):
            raise IdentitySourceError("ORIGINAL_CREATE_REQUEST_CHANGED")
        if _hash(row.authority_reference) != row.authority_reference_sha256:
            raise IdentitySourceError("ORIGINAL_AUTHORITY_REFERENCE_CHANGED")

    def reserve(self, operation, scope, physical, pin_id, snapshot, observations):
        if type(operation) is not SourceOperation or type(scope) is not ProjectScope:
            raise IdentitySourceError("INVALID_ORIGINAL_OPERATION")
        reference = authority_reference_payload(self.authority)
        original = _json(
            {
                "project_id": scope.project_id,
                "project_number": scope.project_number,
                "region": scope.region,
                "owner_description": scope.owner_description,
                "physical": physical,
                "cluster_source_id": pin_id,
            }
        )
        union = _hash(asdict(snapshot))
        with self._locked() as (org, app, cluster, provider, pin, row):
            if (
                pin is None
                or str(pin.guid) != pin_id
                or pin.original_snapshot["native"]["project_number"] != scope.project_number
            ):
                raise IdentitySourceError("ORIGINAL_CLUSTER_PIN_REQUIRED")
            if _hash(asdict(pre_identity_endpoint_snapshot(self.authority, scope))) != union:
                raise IdentitySourceError("CURRENT_COMPLETE_UNION_CHANGED")
            if row:
                if row.original_snapshot != original or row.original_sha256 != _hash(original):
                    raise IdentitySourceError("ORIGINAL_APP_SOURCE_CHANGED")
                if row.state != "OBSERVED" and (
                    str(row.operation_id) != operation.operation_id
                    or row.workflow_id != operation.workflow_id
                    or row.execution_id != operation.execution_id
                    or row.authority_reference != reference
                    or row.accepted_union_sha256 != union
                    or row.accepted_native_sha256 != _native_source_sha256(observations)
                ):
                    raise IdentitySourceError("ORIGINAL_PENDING_OPERATION_REQUIRES_REVIEW")
                return self._reservation(row), self._request(row), row.state, row.unique_id
            source_id = str(uuid7())
            request = CreateRequest(
                source_id,
                operation.operation_id,
                "astro-" + hashlib.sha256(source_id.encode()).hexdigest()[:24],
                scope.project_id,
                scope.project_number,
                scope.owner_description,
            )
            row = GCPAppIdentitySource.objects.create(
                guid=source_id,
                organization=org,
                registered_app=app,
                tenant_cluster=cluster,
                provider_plugin=provider,
                cluster_source=pin,
                original_snapshot=original,
                original_sha256=_hash(original),
                operation_id=operation.operation_id,
                reservation_nonce=uuid4(),
                workflow_id=operation.workflow_id,
                execution_id=operation.execution_id,
                authority_reference=reference,
                authority_reference_sha256=_hash(reference),
                accepted_union_sha256=union,
                accepted_native_sha256=_native_source_sha256(observations),
                create_request_sha256=request.sha256,
                account_id=request.account_id,
                state="UNSENT",
            )
            return self._reservation(row), request, row.state, None

    def validate(self, reservation, *, scope, physical, snapshot):
        with self._locked() as (_, _, _, _, pin, row):
            self._bound(row, reservation)
            if (
                pin.original_sha256 != _hash(pin.original_snapshot)
                or row.original_snapshot["physical"] != physical
                or _declaration(self.authority)[1] != physical
                or _hash(asdict(pre_identity_endpoint_snapshot(self.authority, scope)))
                != _hash(asdict(snapshot))
            ):
                raise IdentitySourceError("CURRENT_COMPLETE_SOURCE_CHANGED")

    def sent(self, reservation, *, scope, physical, snapshot):
        self.validate(reservation, scope=scope, physical=physical, snapshot=snapshot)
        with self._locked() as (_, _, _, _, _, row):
            self._bound(row, reservation)
            if row.state != "UNSENT" or row.accepted_union_sha256 != _hash(asdict(snapshot)):
                raise IdentitySourceError("ACCOUNT_SEND_NOT_ADMITTED")
            # Re-evaluate after the final source row lock, not only before it.
            if (
                _declaration(self.authority)[1] != physical
                or pre_identity_endpoint_snapshot(self.authority, scope) != snapshot
            ):
                raise IdentitySourceError("CURRENT_COMPLETE_SOURCE_CHANGED")
            row.state = "SENT"
            row.save()
            receipt = self._receipt(row)
        return receipt

    @staticmethod
    def _receipt(row):
        return CreateCommitReceipt(
            str(row.guid),
            row.version,
            str(row.operation_id),
            row.create_request_sha256,
            row.state,
            row.unique_id,
        )

    def evidence(self, reservation, evidence):
        if type(evidence) is not CreatedAccountEvidence:
            raise IdentitySourceError("TYPED_ORIGINAL_CREATE_EVIDENCE_REQUIRED")
        # This locks only retained original owners. It deliberately supplies no current authority.
        with self._locked(current=False) as (_, _, _, _, _, row):
            self._bound(row, reservation)
            if row.authority_reference != authority_reference_payload(self.authority):
                raise IdentitySourceError("ORIGINAL_EFFECT_AUTHORITY_REFERENCE_CHANGED")
            if (
                evidence.source_id != reservation.source_id
                or evidence.operation_id != reservation.operation_id
                or evidence.request_sha256 != reservation.create_request_sha256
                or row.state not in ("SENT", "EVIDENCE")
                or row.unique_id not in (None, evidence.unique_id)
            ):
                raise IdentitySourceError("ORIGINAL_CREATE_EVIDENCE_CHANGED")
            if row.state == "SENT":
                row.unique_id, row.state, row.evidence_at = evidence.unique_id, "EVIDENCE", timezone.now()
                row.save()
            receipt = self._receipt(row)
        return receipt

    def unknown(self, reservation):
        with self._locked(current=False) as (_, _, _, _, _, row):
            self._bound(row, reservation)
            if row.authority_reference != authority_reference_payload(self.authority):
                raise IdentitySourceError("ORIGINAL_EFFECT_AUTHORITY_REFERENCE_CHANGED")
            if row.state == "SENT":
                row.state = "UNKNOWN"
                row.save()

    def observed(self, reservation, *, scope, physical, snapshot):
        with self._locked() as (_, _, _, _, pin, row):
            self._bound(row, reservation)
            if row.state not in ("EVIDENCE", "OBSERVED") or row.unique_id is None:
                raise IdentitySourceError("ORIGINAL_ACCOUNT_NOT_OBSERVED")
            if (
                _declaration(self.authority)[1] != physical
                or pre_identity_endpoint_snapshot(self.authority, scope) != snapshot
            ):
                raise IdentitySourceError("CURRENT_COMPLETE_SOURCE_CHANGED")
            if row.state != "OBSERVED":
                row.state, row.observed_at = "OBSERVED", timezone.now()
                row.save()
            request = self._request(row)
            identity = NativeIdentityContext(
                scope.organization_id,
                scope.app_id,
                scope.cluster_id,
                scope.project_id,
                scope.project_number,
                scope.region,
                request.account_id,
                row.unique_id,
                scope.credential,
            )
            return OriginalIdentity(
                str(row.guid),
                row.version,
                str(pin.guid),
                row.original_sha256,
                identity,
                ClusterPin(**pin.original_snapshot["native"]),
            )


def bootstrap_original_identity(authority, operation, *, native_factory=NativeIdentitySource):
    """Independent committed bootstrap; never call from an enclosing activity transaction."""
    if type(operation) is not SourceOperation:
        raise IdentitySourceError("INVALID_ORIGINAL_OPERATION")
    with source_mutex(authority) as store:
        deadline = time.monotonic() + MAX_SOURCE_SECONDS
        declaration, physical = _declaration(authority)
        configuration = _configuration_sha256(authority)

        def source_current():
            if time.monotonic() >= deadline:
                raise IdentitySourceError("SOURCE_DEADLINE_EXCEEDED")
            if (
                _declaration(authority) != (declaration, physical)
                or _configuration_sha256(authority) != configuration
            ):
                raise IdentitySourceError("CURRENT_REGISTERED_SOURCE_CHANGED")

        source_current()
        native = native_factory(declaration)
        try:
            source_current()
            scope, pin = native.observe_cluster(current=source_current)
            snapshot = pre_identity_endpoint_snapshot(authority, scope)

            def complete_current():
                source_current()
                if pre_identity_endpoint_snapshot(authority, scope) != snapshot:
                    raise IdentitySourceError("CURRENT_COMPLETE_UNION_CHANGED")

            rows = native.observe_endpoints(scope, snapshot, cluster_pin=pin, current=complete_current)
            _validate_observations(
                snapshot, tuple(ObservedEndpoint(name, (values,)) for name, values in rows)
            )
            pin_id = store.pin(operation, physical, pin)
            reservation, request, state, uid = store.reserve(
                operation, scope, physical, pin_id, snapshot, rows
            )

            def current():
                source_current()
                store.validate(reservation, scope=scope, physical=physical, snapshot=snapshot)

            if state in ("SENT", "UNKNOWN"):
                raise IdentitySourceError("ACCOUNT_CREATE_UNKNOWN_REQUIRES_REVIEW")
            if state == "UNSENT":
                try:
                    evidence = native.create(
                        request,
                        current=current,
                        cluster_pin=pin,
                        commit_sent=lambda: store.sent(
                            reservation, scope=scope, physical=physical, snapshot=snapshot
                        ),
                        retain_evidence=lambda value: store.evidence(reservation, value),
                    )
                    uid = evidence.unique_id
                except Exception:
                    store.unknown(reservation)
                    raise
            current()
            native.read_account(request, uid, current=current)
            latest_scope, latest_pin = native.observe_cluster(current=current)
            if latest_scope != scope or latest_pin != pin:
                raise IdentitySourceError("ORIGINAL_CLUSTER_INCARNATION_CHANGED")
            latest_rows = native.observe_endpoints(scope, snapshot, cluster_pin=pin, current=current)
            if _native_source_sha256(latest_rows) != _native_source_sha256(rows):
                raise IdentitySourceError("ACCEPTED_NATIVE_SOURCE_CHANGED")
            _validate_observations(
                snapshot, tuple(ObservedEndpoint(name, (values,)) for name, values in latest_rows)
            )
            current()
            return store.observed(reservation, scope=scope, physical=physical, snapshot=snapshot)
        finally:
            native.close()


def original_identity_context(authority, *, native_factory=NativeIdentitySource):
    """Read/revalidate a retained observed source without creating or resuming an effect."""
    with source_mutex(authority) as store:
        with store._locked() as (_, _, _, _, _, row):
            if row is None or row.state != "OBSERVED":
                raise IdentitySourceError("ORIGINAL_OBSERVED_SOURCE_REQUIRED")
            reservation, request, uid = store._reservation(row), store._request(row), row.unique_id
        deadline = time.monotonic() + MAX_SOURCE_SECONDS
        declaration, physical = _declaration(authority)
        configuration = _configuration_sha256(authority)

        snapshot = None

        def current():
            if time.monotonic() >= deadline:
                raise IdentitySourceError("SOURCE_DEADLINE_EXCEEDED")
            with store._locked() as (_, _, _, _, pin, latest):
                store._bound(latest, reservation)
                if (
                    latest.state != "OBSERVED"
                    or latest.unique_id != uid
                    or latest.original_snapshot["physical"] != physical
                    or pin.original_sha256 != _hash(pin.original_snapshot)
                    or _declaration(authority) != (declaration, physical)
                    or _configuration_sha256(authority) != configuration
                ):
                    raise IdentitySourceError("ORIGINAL_SOURCE_CHANGED")
            if snapshot is not None and pre_identity_endpoint_snapshot(authority, scope) != snapshot:
                raise IdentitySourceError("CURRENT_COMPLETE_UNION_CHANGED")

        current()
        native = native_factory(declaration)
        try:
            current()
            scope, cluster = native.observe_cluster(current=current)
            native.read_account(request, uid, current=current)
            snapshot = pre_identity_endpoint_snapshot(authority, scope)
            rows = native.observe_endpoints(scope, snapshot, cluster_pin=cluster, current=current)
            _validate_observations(
                snapshot, tuple(ObservedEndpoint(name, (values,)) for name, values in rows)
            )
            with store._locked() as (_, _, _, _, pin, row):
                store._bound(row, reservation)
                if (
                    ClusterPin(**pin.original_snapshot["native"]) != cluster
                    or row.original_snapshot["project_number"] != scope.project_number
                    or row.original_snapshot["region"] != scope.region
                ):
                    raise IdentitySourceError("ORIGINAL_SOURCE_CHANGED")
            current()
            return store.observed(reservation, scope=scope, physical=physical, snapshot=snapshot)
        finally:
            native.close()
