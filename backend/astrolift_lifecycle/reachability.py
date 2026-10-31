"""Whether an environment's public hosts answer, and what is missing when not (#2573).

A deploy that rolled its workloads out used to read as deployed whether or
not the platform half existed: an app registered while the default zone was
missing came up ``Ready`` and ``Running`` with no Ingress, certificate or
hostname claim, and its host answered 404. This module is the one answer to
"is it reachable", over the database plus one read of each cluster's
routing objects.

For an environment with an Ingress-backed public workload (static sites and
functions are served through their CDN, not the cluster ingress):

* ``reachable``: every expected host has an Ingress (an HTTPRoute on an
  Envoy edge) owned by the environment, a certificate that is Ready, a live
  ``HostnameClaim``, and the environment URL is set.
* ``provisioning``: a deployment is in flight (unless the environment was
  last recorded reachable), cert-manager is still issuing, or the environment
  has never had a running deployment and its domain binding, URL and claims
  are in place (its first deploy publishes it).
* ``no_managed_domain``: the environment has no live zone and
  ``resolve_managed_domain`` finds none for the organization.
* ``needs_reprovision``: anything else; the detail names what is missing.

The cluster is read once per cluster per ``ClusterReader`` (one per GraphQL
request), in parallel under a deadline. A cluster with no driver, an error or
a timeout gives the database-only verdict, and the detail says so.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from typing import Any

from django.utils import timezone

# Re-exported so tests can swap the driver resolution the way the
# force-redeploy tests do, without reaching into core.cluster_management.
from core.cluster_management import _context_for_cluster, _driver_for_cluster

log = logging.getLogger(__name__)

REACHABLE = "reachable"
PROVISIONING = "provisioning"
NEEDS_REPROVISION = "needs_reprovision"
NO_MANAGED_DOMAIN = "no_managed_domain"

#: Worst first, for an app over its environments.
_RANK = {REACHABLE: 0, PROVISIONING: 1, NEEDS_REPROVISION: 2, NO_MANAGED_DOMAIN: 3}

#: Served through a CDN alias, never the cluster ingress (#1010, #987).
_CDN_SERVED_KINDS = frozenset({"static_site", "faas"})

_IN_FLIGHT = ("pending", "deploying", "redeploying")

INGRESS_KIND = "networking.k8s.io/v1/Ingress"
CERTIFICATE_KIND = "cert-manager.io/v1/Certificate"
HTTP_ROUTE_KIND = "gateway.networking.k8s.io/v1/HTTPRoute"
_ALB_CERTIFICATE_ANNOTATION = "alb.ingress.kubernetes.io/certificate-arn"
_CERT_MANAGER_ANNOTATIONS = ("cert-manager.io/cluster-issuer", "cert-manager.io/issuer")

#: How long an Ingress that asks cert-manager for a certificate may go
#: without one before the request counts as lost rather than in progress.
CERTIFICATE_GRACE = dt.timedelta(minutes=10)

#: The whole cluster read, all clusters in parallel.
READ_DEADLINE_SECONDS = 6.0


@dataclasses.dataclass(frozen=True)
class Verdict:
    status: str
    detail: str = ""
    #: Provisioning because a deployment (a reprovision included) is running.
    in_flight: bool = False


@dataclasses.dataclass(frozen=True)
class ClusterView:
    """One cluster's routing objects. ``error`` set means it was not read."""

    ingresses: tuple[dict, ...] = ()
    routes: tuple[dict, ...] = ()
    certificates: tuple[dict, ...] = ()
    certificates_readable: bool = True
    error: str = ""


def _missing_kind(exc: BaseException) -> bool:
    """The apiserver answered, without that kind (the CRD is not installed)."""
    return type(exc).__name__ in {"ResourceNotFoundError", "NotFoundError"}


def _read_cluster(driver: Any, slug: str, *, envoy: bool) -> ClusterView:
    ingresses = driver.list_manifests(slug, None, INGRESS_KIND)
    routes: list[dict] = []
    if envoy:
        from providers.k8s_native.edge_gateway import EDGE_NAMESPACE

        routes = driver.list_manifests(slug, EDGE_NAMESPACE, HTTP_ROUTE_KIND)
    try:
        certificates = driver.list_manifests(slug, None, CERTIFICATE_KIND)
        readable = True
    except Exception as exc:  # noqa: BLE001 - cert-manager absent or not readable here
        if not _missing_kind(exc):
            log.info("reachability: certificates not readable on %s: %s", slug, exc)
        certificates, readable = [], False
    return ClusterView(
        ingresses=tuple(ingresses or ()),
        routes=tuple(routes or ()),
        certificates=tuple(certificates or ()),
        certificates_readable=readable,
    )


class ClusterReader:
    """Each cluster's routing objects, read at most once for its lifetime."""

    def __init__(self, *, deadline_seconds: float = READ_DEADLINE_SECONDS) -> None:
        self._deadline = deadline_seconds
        self._views: dict[int, ClusterView] = {}

    def views(self, clusters: Iterable[Any]) -> dict[int, ClusterView]:
        wanted = {c.pk: c for c in clusters if c is not None}
        jobs: dict[int, tuple[Any, Any, str]] = {}
        for pk, cluster in wanted.items():
            if pk in self._views:
                continue
            try:
                driver = _driver_for_cluster(cluster)
                slug = _context_for_cluster(cluster).slug
            except Exception as exc:  # noqa: BLE001 - no usable driver: database-only verdicts
                self._views[pk] = ClusterView(error=f"cluster {cluster.slug} could not be read: {exc}")
                continue
            jobs[pk] = (cluster, driver, slug)
        if jobs:
            pool = ThreadPoolExecutor(max_workers=min(8, len(jobs)), thread_name_prefix="reachability")
            try:
                futures = {
                    pk: pool.submit(
                        _read_cluster, driver, slug, envoy=(getattr(cluster, "ingress_class", "") == "envoy")
                    )
                    for pk, (cluster, driver, slug) in jobs.items()
                }
                wait_futures(futures.values(), timeout=self._deadline)
                for pk, future in futures.items():
                    cluster = jobs[pk][0]
                    if not future.done():
                        self._views[pk] = ClusterView(
                            error=f"cluster {cluster.slug} did not answer within {self._deadline:g}s"
                        )
                        continue
                    try:
                        self._views[pk] = future.result()
                    except Exception as exc:  # noqa: BLE001 - unreachable: database-only verdicts
                        log.warning("reachability: cluster %s read failed: %s", cluster.slug, exc)
                        self._views[pk] = ClusterView(
                            error=f"cluster {cluster.slug} could not be read: {exc}"
                        )
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        return {pk: self._views[pk] for pk in wanted}


def usable_domain(domain: Any) -> Any:
    """``domain`` when its zone may serve hosts, else None (deleted, unproven, read-only)."""
    from astrolift_clusters.models import ManagedDomain

    if domain is None or getattr(domain, "deleted_at", None) is not None:
        return None
    if domain.verification_state == ManagedDomain.VerificationState.PENDING:
        return None
    if domain.dns_driver == "cloudflare_read_only":
        return None
    return domain


def environment_url(env: Any, domain: Any) -> str:
    """The URL ``env`` advertises under ``domain``, as registration writes it.

    ``<label>.<zone>``, the label being the app's subdomain (or slug), with
    the environment's name appended for an environment in a namespace of its
    own (#1922). A preview keeps the URL its preview row named.
    """
    from core.app_deploy import environment_hostname_label

    app = env.registered_app
    label = (app.subdomain or app.slug or "").strip()
    if not label or domain is None:
        return env.url or ""
    if (env.k8s_namespace or "").strip():
        from astrolift_lifecycle.models import PreviewEnvironment

        if PreviewEnvironment.all_objects.filter(app_environment=env).exists():
            return env.url or ""
        label = environment_hostname_label(label, env.name)
    return f"https://{label}.{domain.zone}"


def expected_hosts(env: Any, workloads: list[Any], domain: Any) -> list[tuple[str, str, bool]]:
    """``(workload_slug, hostname, through_ingress)`` for each public workload of ``env``."""
    from astrolift_manifest.hostname import HostnameInputs, compute_hostnames
    from astrolift_manifest.types import NormalizedManifest, WorkloadManifest
    from core.app_deploy import environment_hostname_inputs

    app = env.registered_app
    manifest = NormalizedManifest(
        name=app.slug,
        workloads=tuple(WorkloadManifest(name=w.slug, kind=w.kind, is_public=w.is_public) for w in workloads),
        managed_services=(),
        defaults_applied=(),
        serialized={},
    )
    kinds = {w.slug: w.kind for w in workloads}
    inputs = environment_hostname_inputs(
        env,
        HostnameInputs(
            app_slug=app.slug,
            org_slug=app.organization.slug if app.organization_id else "none",
            base_zone=domain.zone,
            subdomain_override=app.subdomain or "",
        ),
    )
    return [
        (wh.workload_slug, wh.hostname, kinds.get(wh.workload_slug) not in _CDN_SERVED_KINDS)
        for wh in compute_hostnames(manifest, inputs)
    ]


def _condition(obj: dict, kind: str) -> dict | None:
    for condition in (obj.get("status") or {}).get("conditions") or []:
        if condition.get("type") == kind:
            return condition
    return None


def _created_at(obj: dict) -> dt.datetime | None:
    raw = (obj.get("metadata") or {}).get("creationTimestamp")
    if isinstance(raw, dt.datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=dt.UTC)
    if isinstance(raw, str) and raw:
        try:
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _ingress_for(view: ClusterView, namespace: str, host: str) -> dict | None:
    for ingress in view.ingresses:
        if (ingress.get("metadata") or {}).get("namespace") != namespace:
            continue
        for rule in (ingress.get("spec") or {}).get("rules") or []:
            if rule.get("host") == host:
                return ingress
    return None


def _route_for(view: ClusterView, namespace: str, host: str) -> dict | None:
    from providers.k8s_native.edge_gateway import ROUTE_NAMESPACE_LABEL

    for route in view.routes:
        labels = (route.get("metadata") or {}).get("labels") or {}
        if labels.get(ROUTE_NAMESPACE_LABEL) != namespace:
            continue
        if host in ((route.get("spec") or {}).get("hostnames") or []):
            return route
    return None


def _certificate_state(view: ClusterView, ingress: dict, namespace: str, host: str, now: dt.datetime):
    """``("ready" | "pending" | "missing", reason)`` for ``host`` on ``ingress``."""
    meta = ingress.get("metadata") or {}
    annotations = meta.get("annotations") or {}
    if (annotations.get(_ALB_CERTIFICATE_ANNOTATION) or "").strip():
        return "ready", ""
    tls = next(
        (
            entry
            for entry in (ingress.get("spec") or {}).get("tls") or []
            if host in (entry.get("hosts") or [])
        ),
        None,
    )
    if tls is None:
        return "missing", f"{host} has no TLS certificate"
    secret = (tls.get("secretName") or "").strip()
    if not secret:
        return "missing", f"{host} has no TLS certificate"
    if not view.certificates_readable:
        return "ready", ""
    certificate = next(
        (
            c
            for c in view.certificates
            if (c.get("metadata") or {}).get("namespace") == namespace
            and (c.get("spec") or {}).get("secretName") == secret
        ),
        None,
    )
    if certificate is None:
        if not any(annotations.get(key) for key in _CERT_MANAGER_ANNOTATIONS):
            return "ready", ""
        created = _created_at(ingress)
        if created is not None and now - created < CERTIFICATE_GRACE:
            return "pending", f"the certificate for {host} has been requested"
        return "missing", f"no certificate was issued for {host}"
    ready = _condition(certificate, "Ready") or {}
    names = (certificate.get("spec") or {}).get("dnsNames") or []
    if str(ready.get("status")) == "True" and (not names or host in names):
        return "ready", ""
    issuing = _condition(certificate, "Issuing") or {}
    if str(issuing.get("status")) == "True" or not ready:
        return "pending", f"the certificate for {host} is being issued"
    message = (ready.get("message") or ready.get("reason") or "not Ready").strip()
    return "missing", f"the certificate for {host} is not Ready: {message}"[:300]


@dataclasses.dataclass
class _Facts:
    """Everything one batch needs from the database, loaded once."""

    workloads: dict[int, list[Any]]
    in_flight: set[int]
    running: set[int]
    claims: dict[int, set[str]]
    held_elsewhere: set[tuple[int, str]]
    previews: set[int]
    zones: dict[tuple[int, bool], Any]


def _load_facts(envs: list[Any]) -> _Facts:
    from astrolift_clusters.models import resolve_managed_domain
    from astrolift_lifecycle.models import Deployment, PreviewEnvironment
    from astrolift_registry.models import HostnameClaim, Workload

    env_ids = [e.pk for e in envs]
    app_ids = {e.registered_app_id for e in envs}
    workloads: dict[int, list[Any]] = {}
    for w in Workload.objects.filter(
        registered_app_id__in=app_ids, deleted_at__isnull=True, is_public=True
    ).order_by("pk"):
        workloads.setdefault(w.registered_app_id, []).append(w)
    in_flight: set[int] = set()
    running: set[int] = set()
    for env_id, status in (
        Deployment.objects.filter(
            app_environment_id__in=env_ids,
            deleted_at__isnull=True,
            status__in=(*_IN_FLIGHT, Deployment.Status.RUNNING.value),
        )
        .values_list("app_environment_id", "status")
        .distinct()
    ):
        (running if status == Deployment.Status.RUNNING.value else in_flight).add(env_id)
    claims: dict[int, set[str]] = {}
    for env_id, hostname in HostnameClaim.objects.filter(
        environment_id__in=env_ids, deleted_at__isnull=True
    ).values_list("environment_id", "hostname"):
        claims.setdefault(env_id, set()).add(hostname)
    zone_ids = {e.managed_domain_id for e in envs if e.managed_domain_id}
    held_elsewhere = set(
        HostnameClaim.objects.filter(managed_domain_id__in=zone_ids, deleted_at__isnull=True)
        .exclude(registered_app_id__in=app_ids)
        .values_list("managed_domain_id", "hostname")
    )
    previews = {e.pk for e in envs if e.previewed_environment_id} | set(
        PreviewEnvironment.all_objects.filter(app_environment_id__in=env_ids).values_list(
            "app_environment_id", flat=True
        )
    )
    zones: dict[tuple[int, bool], Any] = {}
    for env in envs:
        org = env.registered_app.organization
        key = (org.pk, env.pk in previews)
        if key not in zones:
            zones[key] = resolve_managed_domain(org, for_preview=key[1])
    return _Facts(workloads, in_flight, running, claims, held_elsewhere, previews, zones)


def _sentence(parts: list[str]) -> str:
    return " ".join(p if p.endswith(".") else f"{p}." for p in parts if p)


def _evaluate(env: Any, facts: _Facts, view: ClusterView | None, now: dt.datetime) -> Verdict:
    public = facts.workloads.get(env.registered_app_id, [])
    if not public:
        return Verdict(REACHABLE, "No public workload.")
    in_flight = env.pk in facts.in_flight
    deployed = env.pk in facts.running
    domain = usable_domain(env.managed_domain if env.managed_domain_id else None)
    gaps: list[str] = []
    pending: list[str] = []
    notes: list[str] = []
    no_zone = False
    if domain is None:
        resolved = facts.zones.get((env.registered_app.organization_id, env.pk in facts.previews))
        if resolved is None:
            no_zone = True
            gaps.append("No managed domain resolves for this organization")
        elif env.managed_domain_id:
            gaps.append(f"The environment is bound to a zone that can no longer serve; {resolved.zone} can")
        else:
            gaps.append(f"The environment is not bound to the managed domain {resolved.zone}")
    else:
        if not (env.url or "").strip():
            gaps.append("The environment has no URL")
        hosts = expected_hosts(env, public, domain)
        if env.previewed_environment_id is None:
            held = facts.claims.get(env.pk, set())
            for _workload, host, _ingress in hosts:
                if host in held:
                    continue
                if (domain.pk, host) in facts.held_elsewhere:
                    gaps.append(f"{host} is claimed by another app")
                else:
                    gaps.append(f"{host} has no hostname claim")
        served = [host for _workload, host, through_ingress in hosts if through_ingress]
        if deployed and served and not in_flight:
            if view is None:
                notes.append("Ingress and certificate not checked")
            elif view.error:
                notes.append(f"Ingress and certificate not verified: {view.error}")
            else:
                from core.app_deploy import namespace_for_environment

                namespace = namespace_for_environment(env)
                envoy = getattr(env.tenant_cluster, "ingress_class", "") == "envoy"
                for host in served:
                    if envoy:
                        if _route_for(view, namespace, host) is None:
                            gaps.append(f"No edge route serves {host}")
                        continue
                    ingress = _ingress_for(view, namespace, host)
                    if ingress is None:
                        gaps.append(f"No Ingress serves {host}")
                        continue
                    state, reason = _certificate_state(view, ingress, namespace, host, now)
                    if state == "pending":
                        pending.append(reason)
                    elif state == "missing":
                        gaps.append(reason)
                if not view.certificates_readable and not envoy:
                    notes.append("Certificate status is not readable on this cluster")
        if not deployed and not in_flight:
            pending.append("Waiting for the first deploy")
    if env.ingress_paused:
        notes.append("Ingress is paused")
    if in_flight:
        # Nothing can be settled until it finishes. An environment last
        # recorded reachable stays so, which keeps a routine deploy from
        # reading as an outage; a reprovision records provisioning as it starts.
        if env.reachability_status == REACHABLE and not gaps:
            return Verdict(REACHABLE, _sentence(["A deployment is in flight", *notes]))
        return Verdict(
            PROVISIONING, _sentence(["A deployment is in flight", *gaps, *pending, *notes]), in_flight=True
        )
    if not gaps and not pending:
        return Verdict(REACHABLE, _sentence(notes))
    if no_zone:
        return Verdict(NO_MANAGED_DOMAIN, _sentence([*gaps, *notes]))
    if gaps:
        return Verdict(NEEDS_REPROVISION, _sentence([*gaps, *pending, *notes]))
    return Verdict(PROVISIONING, _sentence([*pending, *notes]))


def _environments(env_rows: Iterable[Any]) -> list[Any]:
    from astrolift_lifecycle.models import AppEnvironment

    ids = [e.pk for e in env_rows]
    return list(
        AppEnvironment.objects.filter(pk__in=ids)
        .select_related(
            "registered_app__organization__default_managed_domain",
            "managed_domain",
            "tenant_cluster__provider_plugin",
        )
        .order_by("pk")
    )


def environment_verdicts(
    env_rows: Iterable[Any], *, reader: ClusterReader | None = None
) -> dict[int, Verdict]:
    """The verdict for each environment, read in one batch.

    ``reader`` None checks the database only; registration uses that, since
    nothing is deployed yet.
    """
    envs = _environments(env_rows)
    if not envs:
        return {}
    facts = _load_facts(envs)
    views: dict[int, ClusterView] = {}
    if reader is not None:
        clusters = [
            e.tenant_cluster
            for e in envs
            if e.pk in facts.running
            and e.pk not in facts.in_flight
            and facts.workloads.get(e.registered_app_id)
            and usable_domain(e.managed_domain if e.managed_domain_id else None) is not None
        ]
        views = reader.views(clusters)
    now = timezone.now()
    return {
        env.pk: _evaluate(env, facts, views.get(env.tenant_cluster_id) if reader is not None else None, now)
        for env in envs
    }


def app_verdicts(
    apps: Iterable[Any], *, reader: ClusterReader | None = None, known: dict[int, Verdict] | None = None
) -> tuple[dict[int, Verdict], dict[int, Verdict]]:
    """Each app's verdict, and the environment verdicts it was built from.

    The worst of its non-preview environments, except that one with a
    deployment in flight makes the app provisioning. An app with no public
    workload is reachable; one with no environment yet reads as provisioning,
    or as having no managed domain when none resolves.
    """
    from astrolift_clusters.models import resolve_managed_domain
    from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment
    from astrolift_registry.models import Workload

    app_list = list(apps)
    if not app_list:
        return {}, {}
    app_ids = [a.pk for a in app_list]
    env_rows = list(
        AppEnvironment.objects.filter(registered_app_id__in=app_ids, deleted_at__isnull=True).only(
            "pk", "registered_app_id", "previewed_environment_id"
        )
    )
    known = dict(known or {})
    todo = [e for e in env_rows if e.pk not in known]
    known.update(environment_verdicts(todo, reader=reader))
    previews = {e.pk for e in env_rows if e.previewed_environment_id} | set(
        PreviewEnvironment.all_objects.filter(app_environment_id__in=[e.pk for e in env_rows]).values_list(
            "app_environment_id", flat=True
        )
    )
    with_public = set(
        Workload.objects.filter(registered_app_id__in=app_ids, deleted_at__isnull=True, is_public=True)
        .values_list("registered_app_id", flat=True)
        .distinct()
    )
    by_app: dict[int, list[Verdict]] = {}
    for env in env_rows:
        if env.pk not in previews and env.pk in known:
            by_app.setdefault(env.registered_app_id, []).append(known[env.pk])
    out: dict[int, Verdict] = {}
    for app in app_list:
        if app.pk not in with_public:
            out[app.pk] = Verdict(REACHABLE, "No public workload.")
            continue
        verdicts = by_app.get(app.pk)
        if not verdicts:
            org = app.organization if app.organization_id else None
            out[app.pk] = (
                Verdict(PROVISIONING, "No environment yet.")
                if resolve_managed_domain(org) is not None
                else Verdict(NO_MANAGED_DOMAIN, "No managed domain resolves for this organization.")
            )
            continue
        # While any environment is being deployed the app's answer is not
        # settled, so callers waiting on it keep waiting (#2573).
        running = next((v for v in verdicts if v.in_flight), None)
        out[app.pk] = running or max(verdicts, key=lambda v: _RANK[v.status])
    return out, known


def record_environment_verdicts(env_rows: Iterable[Any], *, read_cluster: bool) -> dict[int, Verdict]:
    """Compute and store the verdict on each environment row.

    Called where the platform finishes with an environment (registration,
    deploy, reprovision), which is the moment a missing host rule has to show.
    An update, not a save: the stored verdict is derived, so it must never
    bump the row's version under an operator's edit.
    """
    from astrolift_lifecycle.models import AppEnvironment

    verdicts = environment_verdicts(env_rows, reader=ClusterReader() if read_cluster else None)
    now = timezone.now()
    for env_id, verdict in verdicts.items():
        AppEnvironment.all_objects.filter(pk=env_id).update(
            reachability_status=verdict.status,
            reachability_detail=verdict.detail[:2000],
            reachability_checked_at=now,
        )
    return verdicts


class RequestReachability:
    """The verdicts one GraphQL response needs, computed once and in bulk.

    Rows register as their types are built; the first field that asks for a
    verdict computes it for every row registered so far, so a list page costs
    one batch of queries and one read per cluster however many rows it has.
    """

    def __init__(self) -> None:
        self.reader = ClusterReader()
        self._apps: dict[int, Any] = {}
        self._envs: dict[int, Any] = {}
        self._app_verdicts: dict[int, Verdict] = {}
        self._env_verdicts: dict[int, Verdict] = {}

    def track_app(self, app: Any) -> RequestReachability:
        self._apps.setdefault(app.pk, app)
        return self

    def track_environment(self, env: Any) -> RequestReachability:
        self._envs.setdefault(env.pk, env)
        return self

    def app(self, app_id: int) -> Verdict:
        if app_id not in self._app_verdicts:
            todo = [a for pk, a in self._apps.items() if pk not in self._app_verdicts]
            computed, envs = app_verdicts(todo, reader=self.reader, known=self._env_verdicts)
            self._app_verdicts.update(computed)
            self._env_verdicts.update(envs)
        return self._app_verdicts[app_id]

    def environment(self, env_id: int) -> Verdict:
        if env_id not in self._env_verdicts:
            todo = [e for pk, e in self._envs.items() if pk not in self._env_verdicts]
            self._env_verdicts.update(environment_verdicts(todo, reader=self.reader))
        return self._env_verdicts[env_id]


def request_reachability(info: Any) -> RequestReachability:
    """The response's shared ``RequestReachability``; a private one without a context."""
    context = getattr(info, "context", None) if info is not None else None
    found = getattr(context, "_astrolift_reachability", None)
    if isinstance(found, RequestReachability):
        return found
    created = RequestReachability()
    if context is not None:
        try:
            context._astrolift_reachability = created
        except (AttributeError, TypeError):
            pass
    return created
