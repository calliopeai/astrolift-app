"""Read-only credential observations through the actor-owned encrypted outbox."""

import hashlib
import hmac
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone
from nvke.client import NvkeError
from nvke.contract import upstream_revision
from nvke.credential_metrics import MAX_SAMPLES, _identity, _value, catalog

from astrolift_clusters import nvke_metrics as metrics
from astrolift_clusters import nvke_native_operations as native
from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.models import NvkeMetricsAuthority, NvkeOperation
from astrolift_clusters.nvke_connections import open_connection
from astrolift_identity.api_tokens import get_current_api_token
from core.permissions import Permission, PermissionDenied
from core.secrets import encrypt_at_rest

KEY = "metrics.credentials"
_SALT = "astrolift.nvke.credentials.page.v1"


def _uuid(value):
    try:
        parsed = UUID(str(value))
        if not parsed.int:
            raise ValueError
        return str(parsed)
    except (TypeError, ValueError, AttributeError):
        raise NvkeError("INVALID_INPUT", "Use nonzero NVKE request and source UUIDs") from None


def _name(source):
    data = outbox.decode_result(source).get("response", {}).get("data", {})
    name = data.get("name") if isinstance(data, dict) else None
    try:
        if (
            not isinstance(name, str)
            or not name
            or not name.isprintable()
            or len(name.encode("utf-8")) > 1000
        ):
            raise ValueError
    except (ValueError, UnicodeError):
        raise NvkeError(
            "SNAPSHOT_UNAVAILABLE", "The reviewed controller read has no bounded cluster name"
        ) from None
    return name


def _name_digest(cluster_id, name):
    return hmac.new(
        str(settings.SECRET_KEY).encode(), outbox.canonical([cluster_id, name]), hashlib.sha256
    ).hexdigest()


def reserve(
    *,
    site_id,
    expected_actor_id,
    expected_site_version,
    expected_authority_version,
    source_operation_id,
    expected_source_version,
    request_id,
    timeout_seconds=600,
):
    org, caller = metrics.actor(expected_actor_id, require_read=True)
    identity, source_id = _uuid(request_id), _uuid(source_operation_id)
    if type(timeout_seconds) is not int or not 30 <= timeout_seconds <= 86400:
        raise NvkeError("INVALID_INPUT", "Use an observation deadline between 30 and 86400 seconds")
    with transaction.atomic():
        site = sites.site_row(site_id, org, locked=True)
        request_digest = hmac.new(
            str(settings.SECRET_KEY).encode(),
            outbox.canonical(
                {
                    "key": KEY,
                    "site": str(site.guid),
                    "site_version": expected_site_version,
                    "authority_version": expected_authority_version,
                    "source": source_id,
                    "source_version": expected_source_version,
                    "timeout": timeout_seconds,
                }
            ),
            hashlib.sha256,
        ).hexdigest()
        existing = (
            NvkeOperation.all_objects.select_for_update()
            .filter(site=site, actor=caller, request_id=identity)
            .first()
        )
        if existing is not None:
            if existing.deleted_at is not None or not hmac.compare_digest(
                existing.request_digest, request_digest
            ):
                raise NvkeError("CONFLICT", "This UUID identifies a different NVKE request")
            if not existing.terminal:
                transaction.on_commit(lambda: outbox.dispatch(existing.pk))
            return existing
        sites._version(site, expected_site_version)
        if not site.is_active or site.upstream_revision != upstream_revision():
            raise NvkeError("SOURCE_CHANGED", "NVKE site authority or contract changed")
        authority = NvkeMetricsAuthority.objects.filter(site=site, is_enabled=True).first()
        if authority is None:
            raise NvkeError("SURFACE_UNAVAILABLE", "Configure explicit NVKE metrics authority first")
        sites._version(authority, expected_authority_version)
        source, observation, controller = native.cluster_observation(
            site, caller, source_id, expected_source_version
        )
        name = _name(source)
        cluster_id = observation["cluster_id"]
        sealed = encrypt_at_rest(outbox.canonical({"cluster_id": cluster_id, "cluster_name": name}))
        token = get_current_api_token()
        row = NvkeOperation.objects.create(
            site=site,
            organization=org,
            actor=caller,
            request_id=identity,
            request_digest=request_digest,
            operation_key=KEY,
            expected_site_version=site.version,
            authority={
                "metrics_id": str(authority.guid),
                "metrics_digest": metrics.digest(authority),
                "metrics_version": authority.version,
                "native_cluster_id": cluster_id,
                "cluster_name_digest": _name_digest(cluster_id, name),
                "connection_id": str(controller.guid),
                "connection_digest": outbox.connection_digest(controller),
                "source_id": str(source.guid),
                "source_version": source.version,
                "revision": upstream_revision(),
                "organization_namespace": site.organization_namespace,
                "token_id": str(token.guid) if token else None,
            },
            input_backend_kind=sealed.backend_kind,
            input_ciphertext=sealed.backend_ref,
            deadline=timezone.now() + timedelta(seconds=timeout_seconds),
            created_by=caller,
            updated_by=caller,
        )
        transaction.on_commit(lambda: outbox.dispatch(row.pk))
        return row


def checkpoint(row):
    with outbox.original_actor(row):
        org, caller = metrics.actor(row.actor_id, require_read=True)
        site = sites.site_row(row.site.guid, org)
        authority = (
            NvkeMetricsAuthority.objects.select_related("site")
            .filter(
                site=site,
                guid=row.authority.get("metrics_id"),
                is_enabled=True,
            )
            .first()
        )
        if (
            not site.is_active
            or site.version != row.expected_site_version
            or site.upstream_revision != upstream_revision()
            or row.authority.get("revision") != site.upstream_revision
            or row.authority.get("organization_namespace") != site.organization_namespace
            or authority is None
            or metrics.digest(authority) != row.authority.get("metrics_digest")
        ):
            raise NvkeError("SOURCE_CHANGED", "NVKE metrics execution authority changed")
        source, observation, controller = native.cluster_observation(
            site, caller, row.authority.get("source_id"), row.authority.get("source_version")
        )
        if (
            observation["cluster_id"] != row.authority.get("native_cluster_id")
            or str(controller.guid) != row.authority.get("connection_id")
            or outbox.connection_digest(controller) != row.authority.get("connection_digest")
            or _name_digest(observation["cluster_id"], _name(source))
            != row.authority.get("cluster_name_digest")
        ):
            raise NvkeError("SOURCE_CHANGED", "The reviewed NVKE cluster identity changed")
        return authority, controller


def attempt(row, *, execution):
    from astrolift_clusters.nvke_runtime import _state

    def fence():
        current = NvkeOperation.objects.get(pk=row.pk)
        if (
            current.generation != row.generation
            or current.workflow_run_id != execution[1]
            or current.terminal
        ):
            raise NvkeError("EXECUTION_REFUSED", "NVKE credential observation execution changed")
        if current.cancellation_requested_at:
            raise NvkeError("OPERATION_CANCELLED", "NVKE credential observation was cancelled")
        if timezone.now() >= current.deadline:
            raise NvkeError("DEADLINE_EXCEEDED", "NVKE credential observation deadline was reached")
        return checkpoint(row)

    def live_cluster(controller, parameters):
        with open_connection(
            controller, namespace=row.site.organization_namespace, checkpoint=fence
        ) as client:
            result = client.execute("getCluster", path={"clusterID": parameters["cluster_id"]})
        fence()
        data = result.data
        conditions = data.get("conditions") if isinstance(data, dict) else None
        conditions = conditions if isinstance(conditions, dict) else {}
        deleting, provisioned = conditions.get("deleting"), conditions.get("provisioned")
        if (
            not isinstance(data, dict)
            or data.get("id") != parameters["cluster_id"]
            or data.get("name") != parameters["cluster_name"]
            or (
                deleting is not None and (not isinstance(deleting, dict) or deleting.get("status") != "False")
            )
            or not isinstance(provisioned, dict)
            or provisioned.get("status") != "True"
        ):
            raise NvkeError(
                "SOURCE_CHANGED",
                "The live controller no longer proves this provisioned cluster UUID and name",
            )

    try:
        authority, controller = fence()
        parameters = outbox.decode_input(row)
        if (
            not isinstance(parameters, dict)
            or parameters.get("cluster_id") != row.authority.get("native_cluster_id")
            or _name_digest(parameters["cluster_id"], parameters.get("cluster_name"))
            != row.authority.get("cluster_name_digest")
        ):
            raise NvkeError("SOURCE_CHANGED", "NVKE credential observation input changed")
        _state(row, "running", "credential_metrics_reading")
        live_cluster(controller, parameters)
        with metrics.open_metrics(authority, fence) as client:
            snapshot = client.read(parameters["cluster_name"])
        scraped_at = timezone.now()
        live_cluster(controller, parameters)
        with transaction.atomic():
            sites.site_row(row.site.guid, row.organization, locked=True)
            NvkeOperation.objects.select_for_update().get(pk=row.pk)
            NvkeMetricsAuthority.objects.select_for_update().get(pk=authority.pk)
            type(controller).objects.select_for_update().get(pk=controller.pk)
            NvkeOperation.objects.select_for_update().get(guid=row.authority["source_id"])
            fence()
            return _state(
                row,
                "succeeded",
                "credential_metrics_observed",
                progress={
                    "credential_metrics_format": 1,
                    "cluster_id": parameters["cluster_id"],
                    "source_id": row.authority["source_id"],
                    "source_version": row.authority["source_version"],
                    "metrics_version": row.authority["metrics_version"],
                    "scraped_at": scraped_at.isoformat(),
                    **asdict(snapshot),
                },
            )
    except PermissionDenied:
        return _state(
            row,
            "failed",
            "credential_metrics_refused",
            error=NvkeError("AUTHORITY_REVOKED", "The original metrics caller is no longer authorized"),
        )
    except (NvkeError, sites.NvkeSiteError) as exc:
        status = (
            "cancelled"
            if exc.code == "OPERATION_CANCELLED"
            else "awaiting_ready"
            if getattr(exc, "retryable", False)
            else "failed"
        )
        return _state(
            row,
            status,
            "credential_metrics_read_pending" if status == "awaiting_ready" else "credential_metrics_refused",
            error=NvkeError(exc.code, str(exc)),
        )
    except Exception:
        return _state(
            row,
            "failed",
            "credential_metrics_refused",
            error=NvkeError("METRICS_UNAVAILABLE", "The NVKE credential observation could not be collected"),
        )


def _snapshot(*, site_id, operation_id, expected_actor_id, expected_version):
    _org, caller = sites.authorized(Permission.NVKE_READ)
    if type(expected_actor_id) is not int or expected_actor_id != caller.pk:
        raise NvkeError("SOURCE_CHANGED", "The reviewed NVKE caller changed")
    row = outbox.read(operation_id)
    if str(row.site.guid) != str(site_id):
        raise NvkeError("NOT_FOUND", "NVKE credential observation not found")
    if (
        row.operation_key != KEY
        or row.status != "succeeded"
        or type(expected_version) is not int
        or row.version != expected_version
    ):
        raise NvkeError(
            "SNAPSHOT_UNAVAILABLE", "Use a successful credential observation at its reviewed version"
        )
    data = outbox.decode_result(row)
    try:
        if not isinstance(data, dict):
            raise ValueError
        observed_at = datetime.fromisoformat(data["scraped_at"])
        if (
            data.get("credential_metrics_format") != 1
            or data.get("cluster_id") != row.authority["native_cluster_id"]
            or data.get("source_id") != row.authority["source_id"]
            or data.get("source_version") != row.authority["source_version"]
            or data.get("metrics_version") != row.authority["metrics_version"]
            or observed_at.utcoffset() is None
            or type(data.get("cluster_reported")) is not bool
            or not isinstance(data.get("samples"), list)
            or len(data["samples"]) > MAX_SAMPLES
        ):
            raise ValueError
        samples = []
        vocabulary = catalog()
        definitions = {
            item["name"]: set(item["labels"]) - {"tenant_cluster"} for item in vocabulary["metrics"]
        }
        credentials = {item["name"]: item["required"] for item in vocabulary["credentials"]}
        seen = set()
        for sample in data["samples"]:
            if not isinstance(sample, dict):
                raise ValueError
            labels = dict(sample["labels"])
            if (
                sample["name"] not in definitions
                or set(labels) != definitions[sample["name"]]
                or len(labels) != len(sample["labels"])
            ):
                raise ValueError
            identity = (sample["name"], tuple(sorted(labels.items())))
            if identity in seen or not isinstance(sample["value"], str):
                raise ValueError
            for label in labels.values():
                if not isinstance(label, str):
                    raise ValueError
                _identity(label)
            if (
                ("credential" in labels and labels["credential"] not in credentials)
                or ("state" in labels and labels["state"] not in vocabulary["states"][sample["name"]])
                or _value(sample["name"], sample["value"]) != sample["value"]
                or (
                    sample["name"] == "certificate_required"
                    and (sample["value"] == "1") != credentials[labels["credential"]]
                )
            ):
                raise ValueError
            seen.add(identity)
            samples.append(
                {
                    "key": hashlib.sha256(outbox.canonical(identity)).hexdigest(),
                    "metric": sample["name"],
                    "node": labels.get("node"),
                    "credential": labels.get("credential"),
                    "state": labels.get("state"),
                    "value": sample["value"],
                }
            )
        return row, data, samples, observed_at, caller
    except (TypeError, ValueError, KeyError, OverflowError, NvkeError):
        raise NvkeError("SNAPSHOT_UNAVAILABLE", "The saved NVKE credential observation is invalid") from None


def observation(**review):
    row, data, samples, observed_at, _caller = _snapshot(**review)
    globals_ = {
        item["metric"]: item["value"]
        for item in samples
        if item["node"] is None and item["state"] is None and item["credential"] is None
    }
    return row, {
        "cluster_id": data["cluster_id"],
        "source_operation_id": data["source_id"],
        "source_version": data["source_version"],
        "authority_id": row.authority["metrics_id"],
        "authority_version": data["metrics_version"],
        "scraped_at": observed_at,
        "cluster_reported": data["cluster_reported"],
        "collection_succeeded": {"0": False, "1": True}.get(
            globals_.get("credential_metrics_collection_success")
        ),
        "monitoring_enabled": {"0": False, "1": True}.get(globals_.get("credential_monitoring_enabled")),
        "renewal_automation_enabled": {"0": False, "1": True}.get(globals_.get("renewal_automation_enabled")),
        "warning_window_seconds": globals_.get("certificate_warning_window_seconds"),
    }


def _certificates(samples, observed_at):
    nodes, certificates = {}, {}
    for item in samples:
        if item["node"] is None:
            continue
        if item["credential"] is None:
            nodes.setdefault(item["node"], {})[item["metric"]] = item["value"]
        else:
            certificates.setdefault((item["node"], item["credential"]), {})[item["metric"]] = item["value"]
    result = []
    for (node, credential), values in sorted(certificates.items()):
        status = nodes.get(node, {})
        not_after = values.get("certificate_not_after_timestamp_seconds")
        expires = (
            datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=int(not_after))
            if not_after is not None
            else None
        )
        available = {"0": False, "1": True}.get(values.get("certificate_observation_available"))
        result.append(
            {
                "key": hashlib.sha256(outbox.canonical([node, credential])).hexdigest(),
                "node": node,
                "credential": credential,
                "required": {"0": False, "1": True}.get(values.get("certificate_required")),
                "observation_available": available,
                "latest_inspection_available": {"0": False, "1": True}.get(
                    status.get("certificate_inspection_available")
                ),
                "observation_age_seconds": status.get("certificate_observation_age_seconds"),
                "reported_not_after": expires,
                "reported_expired_at_scrape": expires <= observed_at
                if available is True and expires is not None
                else None,
            }
        )
    return result


def page(*, kind="metrics", after=None, limit=25, search="", **review):
    row, data, samples, observed_at, caller = _snapshot(**review)
    if kind not in {"metrics", "certificates"}:
        raise NvkeError("INVALID_INPUT", "Use a declared NVKE credential observation view")
    if kind == "certificates":
        samples = _certificates(samples, observed_at)
    if (
        type(limit) is not int
        or not 1 <= limit <= 200
        or not isinstance(search, str)
        or len(search) > 200
        or not search.isprintable()
        and search
    ):
        raise NvkeError("INVALID_INPUT", "Use a bounded NVKE credential page/search")
    binding = [
        row.organization_id,
        caller.pk,
        str(row.site.guid),
        str(row.guid),
        row.version,
        kind,
        search,
        limit,
    ]
    offset = 0
    if after:
        try:
            if not isinstance(after, str) or len(after) > 4096:
                raise ValueError
            cursor = signing.Signer(salt=_SALT).unsign_object(after)
            if (
                not isinstance(cursor, list)
                or cursor[:-1] != binding
                or type(cursor[-1]) is not int
                or cursor[-1] < 0
            ):
                raise ValueError
            offset = cursor[-1]
        except (signing.BadSignature, ValueError, TypeError):
            raise NvkeError(
                "INVALID_CURSOR", "Use a cursor from this credential observation and caller"
            ) from None
    filtered = [
        item
        for item in samples
        if not search
        or search.casefold()
        in " ".join(str(item.get(key) or "") for key in ("metric", "node", "credential", "state")).casefold()
    ]
    if offset > len(filtered):
        raise NvkeError("INVALID_CURSOR", "Credential page no longer matches the observation")
    end = offset + limit
    cursor = signing.Signer(salt=_SALT).sign_object([*binding, end]) if end < len(filtered) else None
    return row, filtered[offset:end], cursor, len(filtered), observed_at
