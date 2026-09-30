"""Activities for ``InstallClusterPrereqsWorkflow`` (#66).

The bootstrap recipe lives in each provider's driver (see
``BootstrapComponent`` in ``_sdk/cluster.py``). The activity here
takes the operator's selection (which components, plus per-option
overrides), renders a Flux ``HelmRelease`` per selected component (plus a
``HelmRepository`` per unique upstream chart registry) with the
driver's pre-tuned helm values merged with the operator's overrides,
and applies them to the cluster via ``cluster_driver.apply_manifests``.
Each cloud driver provides its own chart coordinates (chart name,
upstream Helm repo URL, pinned version) so EKS, GKE, AKS, and vanilla
k8s all install from the right upstream registries.

Why HelmRelease (Flux) instead of running ``helm`` from a Job:
- A HelmRelease is declarative + idempotent — re-running the
  activity converges the cluster to the operator's current set;
  unselected components are deleted, selected ones reconcile.
- Status surfaces back via the HelmRelease's ``status`` subresource,
  which the cluster-status tab (#68) can stream into the UI.

Flux bootstrap: if the Flux CRDs are absent the activity installs
Flux automatically before applying the HelmRepository/HelmRelease
resources. This makes the UI "Install / reconcile" flow fully
self-sufficient — no manual CLI prerequisite step needed.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.request
from typing import Any

from temporalio import activity

_FLUX_VERSION = os.environ.get("ASTROLIFT_FLUX_VERSION", "v2.4.0")
_FLUX_INSTALL_URL = os.environ.get(
    "ASTROLIFT_FLUX_INSTALL_URL",
    f"https://github.com/fluxcd/flux2/releases/download/{_FLUX_VERSION}/install.yaml",
)

log = logging.getLogger("astrolift_workflows.activities.install_prereqs")


def _merge_helm_values(
    base: dict[str, Any],
    overrides: dict[str, str],
) -> dict[str, Any]:
    """Apply per-option overrides on top of the driver's helm_values.

    Overrides are flat (key → string value) because the SDK's
    BootstrapOption surface is flat — operators pick from enumerated
    choices, not arbitrary structured input. The driver embeds each
    choice's effect in helm_values via a top-level key the override
    targets. Unknown override keys are passed through verbatim so a
    driver can introduce a new option without the activity changing.
    """
    out = dict(base)
    for key, value in (overrides or {}).items():
        out[key] = value
    return out


# Maps a prometheus_storage option value to the Kubernetes StorageClass
# name it requires to be pre-installed on the cluster. Used by the preflight
# check that gates kube-prometheus-stack installation on StorageClass presence
# so operators get an actionable error instead of a silent PVC binding failure.
_PERSISTENT_STORAGE_CLASS_NAMES: dict[str, str] = {
    "efs_persistent": "efs-prometheus",
    "filestore_persistent": "filestore-prometheus",
    "azurefile_persistent": "azurefile-prometheus",
}

# StorageSpec blocks for persistent Prometheus storage on each cloud.
# Each entry is a ready-to-embed kube-prometheus-stack storageSpec value.
# The StorageClass name must exist in the cluster before the HelmRelease
# is installed — operators apply the SC manifest emitted by Terraform
# (EFS CSI on EKS, Filestore CSI on GKE, Azure Files CSI on AKS) before
# running the bootstrap recipe.
_PROMETHEUS_PERSISTENT_STORAGE_SPECS: dict[str, dict] = {
    # EFS-backed on EKS Fargate (#772). EBS can't attach to Fargate pods;
    # EFS (NFS) is the only AWS-native persistent storage available there.
    "efs_persistent": {
        "volumeClaimTemplate": {
            "spec": {
                "storageClassName": "efs-prometheus",
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "50Gi"}},
            }
        }
    },
    # Filestore CSI on GKE (NFS-backed, works across AZ-distributed nodes).
    "filestore_persistent": {
        "volumeClaimTemplate": {
            "spec": {
                "storageClassName": "filestore-prometheus",
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "50Gi"}},
            }
        }
    },
    # Azure Files CSI on AKS (SMB/NFS, works on virtual-node Fargate-style
    # pods and standard node pools alike).
    "azurefile_persistent": {
        "volumeClaimTemplate": {
            "spec": {
                "storageClassName": "azurefile-prometheus",
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "50Gi"}},
            }
        }
    },
}


def _apply_semantic_options(
    component_key: str,
    values: dict[str, Any],
    options: dict[str, str],
) -> dict[str, Any]:
    """Convert semantic option keys (not valid Helm keys) into proper nested
    Helm values, stripping the synthetic key so it doesn't land in the
    HelmRelease spec.

    Currently handles ``prometheus_storage`` on ``kube-prometheus-stack``:
      - ``"ephemeral"`` (or absent) → empty storageSpec, 24h retention
      - ``"efs_persistent"`` → EFS StorageClass PVC, 30d retention (EKS)
      - ``"filestore_persistent"`` → Filestore StorageClass PVC, 30d (GKE)
      - ``"azurefile_persistent"`` → Azure Files StorageClass PVC, 30d (AKS)

    Any unrecognised ``prometheus_storage`` value is treated as ephemeral so
    a typo doesn't silently push invalid YAML into the HelmRelease.
    """
    if component_key != "kube-prometheus-stack":
        return values

    storage_mode = options.get("prometheus_storage", "ephemeral")
    # Strip the synthetic key regardless of mode — it isn't a valid Helm value.
    result = {k: v for k, v in values.items() if k != "prometheus_storage"}

    storage_spec = _PROMETHEUS_PERSISTENT_STORAGE_SPECS.get(storage_mode)
    if storage_spec is None:
        # ephemeral or unknown — leave storageSpec empty, 24h retention.
        return result

    # Deep-merge: update the nested prometheusSpec without clobbering other
    # top-level keys (nodeExporter, grafana, etc.).
    prom_block = dict(result.get("prometheus", {}))
    prom_spec = dict(prom_block.get("prometheusSpec", {}))
    prom_spec["storageSpec"] = storage_spec
    prom_spec["retention"] = "30d"
    prom_block["prometheusSpec"] = prom_spec
    result["prometheus"] = prom_block
    return result


def _assert_storage_class_preflight(
    driver: Any,
    cluster_slug: str,
    component_key: str,
    component_options: dict[str, str],
) -> None:
    """Raise if a persistent storage mode is selected but the required
    StorageClass is absent from the cluster.

    Guards kube-prometheus-stack installation: if the operator picks
    ``efs_persistent`` (or any other cloud-native persistent mode) but
    hasn't applied the StorageClass manifest from the Terraform output
    yet, the HelmRelease would otherwise bind the PVC to a non-existent
    StorageClass and stall silently. This preflight surfaces the gap as
    an actionable error before Flux even tries.

    Falls back to a no-op if the driver doesn't implement
    ``storage_class_exists`` (e.g. cloud-native drivers where the check
    would need a different API surface — they get the old behavior).
    """
    if component_key != "kube-prometheus-stack":
        return
    storage_mode = component_options.get("prometheus_storage", "ephemeral")
    required_sc = _PERSISTENT_STORAGE_CLASS_NAMES.get(storage_mode)
    if not required_sc:
        return
    check = getattr(driver, "storage_class_exists", None)
    if check is None:
        return
    if not check(cluster_slug, required_sc):
        from core.app_deploy import AppDeployError

        raise AppDeployError(
            f"kube-prometheus-stack storage mode '{storage_mode}' requires "
            f"StorageClass '{required_sc}' on cluster '{cluster_slug}' but it "
            f"was not found. Apply the StorageClass manifest from the Terraform "
            f"output (efs_prometheus_storage_class_yaml) before running the "
            f"bootstrap recipe. See issue #772 for the Terraform setup."
        )


def _flux_crd_missing(errors: list) -> bool:
    """Return True if any ApplyError indicates the Flux CRDs don't exist yet.

    The kubernetes client raises a ``404 Not Found`` on the CRD discovery
    endpoint when the CRD group hasn't been registered, which surfaces
    as "No matches found for kind" or "the server could not find the
    requested resource" in the apply error message.
    """
    _CRD_MISS_MARKERS = (
        "no matches found for",  # kubernetes-client: CRD group not registered
        "the server could not find the requested resource",
        "no kind",
    )
    for err in errors:
        msg = str(err).lower()
        if any(marker.lower() in msg for marker in _CRD_MISS_MARKERS):
            return True
    return False


def _operator_not_serving(errors: list) -> bool:
    """A post-install object failed because its operator is not up yet.

    Either its CRD is not registered, or the operator's admission webhook
    has no ready endpoint. The chart that brings both was applied moments
    ago, so an object sent while the controller is still starting is refused
    the same way a CR sent before its CRD is. Kept apart from
    ``_flux_crd_missing``, which decides whether to bootstrap Flux and must
    not fire on a webhook error.
    """
    return _flux_crd_missing(errors) or any("failed calling webhook" in str(err).lower() for err in errors)


def _ensure_flux_installed(driver, ctx_slug: str) -> None:
    """Fetch the pinned Flux install manifest and apply it to the cluster.

    Applies CRD objects first (so the API server registers the group
    before we attempt HelmRepository/HelmRelease). Non-CRD objects
    (Namespace, Deployment, Service, …) are applied in a second pass.

    This is intentionally a best-effort fire-and-wait: we apply and
    then sleep briefly so the flux-system controllers have time to
    register their handlers before the caller retries the HelmRelease
    apply. A full readiness probe would require watching Deployments,
    which is overkill for the bootstrap path — the activity is
    retried by Temporal anyway, so a partially-ready Flux converges on
    the next attempt.
    """
    import yaml

    log.info(
        "Flux CRDs absent — fetching install manifest from %s",
        _FLUX_INSTALL_URL,
    )
    with urllib.request.urlopen(_FLUX_INSTALL_URL, timeout=60) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8")

    docs = list(yaml.safe_load_all(raw))
    docs = [d for d in docs if d]  # drop None separators

    crds = [d for d in docs if d.get("kind") == "CustomResourceDefinition"]
    others = [d for d in docs if d.get("kind") != "CustomResourceDefinition"]

    log.info(
        "Flux install manifest: %d CRDs, %d other resources",
        len(crds),
        len(others),
    )

    flux_ns = "flux-system"

    if crds:
        result = driver.apply_manifests(ctx_slug, flux_ns, crds)
        if not result.ok:
            from core.app_deploy import AppDeployError

            raise AppDeployError(
                "Flux CRD bootstrap failed: " + "; ".join(str(e) for e in result.errors),
            )

    if others:
        result = driver.apply_manifests(ctx_slug, flux_ns, others)
        if not result.ok:
            from core.app_deploy import AppDeployError

            raise AppDeployError(
                "Flux controller bootstrap failed: " + "; ".join(str(e) for e in result.errors),
            )

    log.info("Flux bootstrap applied — caller should let Temporal retry for CRD registration")


# Kinds applied first within a component's post_install_manifests: they
# establish the scope (Namespace) or the type (CRD) that later manifests in
# the same batch depend on, so a CR that lands in the Namespace / uses the
# CRD doesn't 404 within one apply pass.
_POST_INSTALL_FOUNDATIONAL_KINDS: frozenset[str] = frozenset({"Namespace", "CustomResourceDefinition"})


def _order_by_depends_on(components: list) -> list:
    """Return ``components`` topologically ordered by ``depends_on``.

    Each component appears after every component it depends on. Stable:
    among independent components the original list order is preserved.
    Missing deps (a key that isn't in this list) are ignored, and a
    dependency cycle degrades gracefully to best-effort original order for
    the offending nodes rather than raising — this ordering is an
    optimization for apply sequencing, not a correctness gate (the applies
    are individually idempotent + Temporal-retried).
    """
    by_key = {c.key: c for c in components}
    ordered: list = []
    placed: set[str] = set()
    in_progress: set[str] = set()

    def visit(component) -> None:  # noqa: ANN001
        if component.key in placed or component.key in in_progress:
            # Already placed, or a cycle led us back here — stop recursing.
            return
        in_progress.add(component.key)
        for dep_key in component.depends_on:
            dep = by_key.get(dep_key)
            if dep is not None:
                visit(dep)
        in_progress.discard(component.key)
        placed.add(component.key)
        ordered.append(component)

    for component in components:
        visit(component)
    return ordered


def _post_install_namespace(manifests: list[dict[str, Any]], *, default: str) -> str:
    """Namespace to pass to ``apply_manifests`` for a post-install batch.

    The driver's server-side apply uses the *passed* namespace (not each
    manifest's ``metadata.namespace``) for namespaced kinds, and ignores it
    for cluster-scoped kinds (Namespace, CRD) — so one call can carry both
    as long as the passed namespace matches the namespaced objects. We take
    it from the first manifest that declares one (e.g. the KnativeServing CR
    in ``knative-serving``); if every manifest is cluster-scoped we fall
    back to ``default``.
    """
    for manifest in manifests:
        ns = (manifest.get("metadata") or {}).get("namespace")
        if ns:
            return ns
    return default


def _apply_post_install_manifests(
    driver: Any,
    ctx_slug: str,
    components: list,
    selected_set: set[str],
    target_namespace: str,
) -> tuple[bool, list[str]]:
    """Apply each selected component's ``post_install_manifests`` (idempotent
    SSA) after its HelmRelease has been applied.

    Ordering: components are visited in ``depends_on`` order; within a
    component, foundational kinds (Namespace / CRD) apply before the rest.

    Best-effort per component — a failure never rolls back a sibling's
    HelmRelease. Returns ``(crd_not_ready, errors)``:

      - ``crd_not_ready`` is True when a manifest failed only because its
        CRD isn't registered yet (the component's operator chart was just
        applied and Flux hasn't finished installing it). The caller raises a
        retriable error so Temporal re-runs; by then the operator is up and
        the idempotent re-apply lands the CR. This mirrors the Flux-own-CRD
        convergence path (``_ensure_flux_installed`` + the retriable raise in
        ``_install_cluster_prereqs_sync``).
      - ``errors`` is the surfaced list of every per-manifest error string
        (CRD-not-ready included) for the activity result + logs.
    """
    ordered_components = _order_by_depends_on(
        [c for c in components if c.key in selected_set and c.post_install_manifests]
    )
    crd_not_ready = False
    errors: list[str] = []

    for component in ordered_components:
        # Foundational kinds first (stable sort preserves author order within
        # each group) so a CR doesn't 404 on a Namespace/CRD in the same batch.
        manifests = sorted(
            component.post_install_manifests,
            key=lambda m: 0 if m.get("kind") in _POST_INSTALL_FOUNDATIONAL_KINDS else 1,
        )
        namespace = _post_install_namespace(manifests, default=target_namespace)
        result = driver.apply_manifests(ctx_slug, namespace, manifests)

        if any(
            getattr(error, "exception_type", "")
            in {
                "CustomDomainAuthPending",
                "CustomDomainAuthApplyFailed",
            }
            for error in result.errors
        ):
            from temporalio.exceptions import ApplicationError

            raise ApplicationError(
                "custom-domain authentication is not ready; its backend remains guarded and the edge install must retry",
                non_retryable=False,
            )

        if result.ok:
            log.info(
                "install_cluster_prereqs: post-install %s applied "
                "created=%d updated=%d unchanged=%d (ns=%s)",
                component.key,
                len(result.created),
                len(result.updated),
                len(result.unchanged),
                namespace,
            )
            continue

        if _operator_not_serving(result.errors):
            crd_not_ready = True
            log.info(
                "install_cluster_prereqs: post-install %s deferred; its "
                "operator isn't serving yet (CRD or webhook); Temporal will retry",
                component.key,
            )
        for err in result.errors:
            errors.append(str(err))
            if not _operator_not_serving([err]):
                log.warning(
                    "install_cluster_prereqs: post-install %s manifest error " "(non-fatal): %s",
                    component.key,
                    err,
                )

    return crd_not_ready, errors


# Bootstrap-component key for the AWS EBS CSI driver (mirrors the key
# EKSCluster.bootstrap_components emits). The component's IRSA role must be
# minted by the platform before its controller can provision EBS volumes.
_EBS_CSI_COMPONENT_KEY = "aws-ebs-csi-driver"


def _provision_ebs_csi_irsa_role(cluster: Any, selected_set: set[str]) -> str | None:
    """Mint the EBS-CSI controller's IRSA role when the aws-ebs-csi-driver
    component is enabled (#1032).

    #1024 added ``IRSADriver.provision_ebs_csi_role`` + the in-cluster Helm
    component + the default gp3 StorageClass, but nothing ever called the
    mint — so the controller ServiceAccount was annotated with a role no one
    created, the controller couldn't assume it, and PVCs hung ``Pending``
    forever. This wires the call into the prereqs install: when the component
    is selected on an AWS cluster, discover the cluster's OIDC issuer (cache
    it on the row) and self-provision the role, scoping its trust to the
    ``kube-system:ebs-csi-controller-sa`` subject the chart runs as.

    The minted role name matches the ServiceAccount annotation
    ``EKSCluster.bootstrap_components`` emits (``_irsa_arn``): an explicit
    override ARN from ``auth_config['irsa_roles']`` if set, else the
    convention ``<cluster_name>-aws-ebs-csi-driver``.

    Idempotent — ``provision_ebs_csi_role`` reconciles the trust subject and
    re-attaches the managed policy on re-run. No-op (returns ``None``) when
    the component is disabled or the cluster isn't AWS (other clouds use their
    own CSI path)."""
    if _EBS_CSI_COMPONENT_KEY not in selected_set:
        return None
    if getattr(getattr(cluster, "provider_plugin", None), "slug", "") != "aws":
        return None

    from astrolift_workflows.activities.workload_identity import (
        _ensure_cluster_oidc_issuer,
    )
    from core.app_deploy import driver_for_capability

    # The IRSA trust binds to the cluster's OIDC issuer; discover + cache it
    # before resolving the identity driver (whose IRSAConfig reads the issuer
    # off the cluster row) so the minted trust isn't malformed by an empty
    # issuer.
    _ensure_cluster_oidc_issuer(cluster)

    ac = cluster.auth_config or {}
    pc = cluster.provider_config or {}
    override = (ac.get("irsa_roles") or {}).get(_EBS_CSI_COMPONENT_KEY)
    if override:
        role_name = str(override).split("/")[-1]
    else:
        cluster_name = ac.get("cluster_name") or pc.get("cluster_name") or cluster.slug
        role_name = f"{cluster_name}-{_EBS_CSI_COMPONENT_KEY}"

    identity_driver = driver_for_capability(cluster, "identity")
    role_arn = identity_driver.provision_ebs_csi_role(role_name)
    log.info(
        "install_cluster_prereqs: provisioned EBS-CSI IRSA role %s (%s)",
        role_name,
        role_arn,
    )
    return role_arn


# Bootstrap-component keys for the AWS controllers whose IRSA roles the
# platform mints (mirrors the keys EKSCluster.bootstrap_components emits). Each
# maps to a mint method on the AWS IRSADriver.
_ALB_CONTROLLER_COMPONENT_KEY = "aws-load-balancer-controller"
_EXTERNAL_DNS_COMPONENT_KEY = "external-dns"
_CLOUDWATCH_EXPORTER_COMPONENT_KEY = "cloudwatch-exporter"
_S3_CSI_COMPONENT_KEY = "aws-mountpoint-s3-csi-driver"


def _provision_aws_controller_irsa_role(
    cluster: Any,
    selected_set: set[str],
    *,
    component_key: str,
    mint_method: str,
) -> str | None:
    """Mint the IRSA role for an AWS in-cluster controller when its component
    is enabled (#1044).

    Generalizes the EBS-CSI wiring (#1032) to the aws-load-balancer-controller
    and external-dns components: before #1044 neither had a mint function on
    the identity driver *or* a call site, so their controller ServiceAccounts
    were annotated with role ARNs that nothing created — AssumeRoleWithWebIdentity
    403'd and ingress / Route53 sync silently never worked. When the component
    is selected on an AWS cluster, discover + cache the cluster's OIDC issuer,
    then call the named mint method (``provision_alb_controller_role`` /
    ``provision_external_dns_role``), which scopes the trust to the chart's
    pinned ServiceAccount subject and writes the inline policy.

    Role name matches the SA annotation ``EKSCluster.bootstrap_components``
    emits (``_irsa_arn``): an explicit override ARN from
    ``auth_config['irsa_roles']`` if set, else ``<cluster_name>-<component_key>``.

    Idempotent — the mint method reconciles the trust subject + re-writes the
    inline policy on re-run. No-op (returns ``None``) when the component is
    deselected or the cluster isn't AWS."""
    if component_key not in selected_set:
        return None
    if getattr(getattr(cluster, "provider_plugin", None), "slug", "") != "aws":
        return None

    from astrolift_workflows.activities.workload_identity import (
        _ensure_cluster_oidc_issuer,
    )
    from core.app_deploy import driver_for_capability

    _ensure_cluster_oidc_issuer(cluster)

    ac = cluster.auth_config or {}
    pc = cluster.provider_config or {}
    override = (ac.get("irsa_roles") or {}).get(component_key)
    if override:
        role_name = str(override).split("/")[-1]
    else:
        cluster_name = ac.get("cluster_name") or pc.get("cluster_name") or cluster.slug
        role_name = f"{cluster_name}-{component_key}"

    identity_driver = driver_for_capability(cluster, "identity")
    role_arn = getattr(identity_driver, mint_method)(role_name)
    log.info(
        "install_cluster_prereqs: provisioned %s IRSA role %s (%s)",
        component_key,
        role_name,
        role_arn,
    )
    return role_arn


_LEGACY_HELM_RELEASE_NAMES: frozenset[str] = frozenset(
    [
        # tls_issuer was renamed to cert-manager across all cloud drivers.
        # Clusters that ran bootstrap before the rename will have a stale
        # astrolift-tls-issuer HelmRelease that the activity no longer emits;
        # we delete it so Flux stops trying to install an invalid chart.
        "astrolift-tls-issuer",
    ]
)

_LEGACY_HELM_REPO_NAMES: frozenset[str] = frozenset(
    [
        # The old umbrella chart approach emitted an "astrolift-prereqs"
        # HelmRepository pointing at the GHCR OCI registry. That chart
        # doesn't exist and was replaced by per-component upstream repos.
        "astrolift-prereqs",
    ]
)


def _without_secrets(text: str, secrets: list[str]) -> str:
    """``text`` with each secret masked, raw and base64-encoded.

    Apply errors carry the apiserver's reply verbatim, and they end up in
    the run's error message, which operators read.
    """
    import base64

    for secret in secrets:
        if not secret:
            continue
        for form in (secret, base64.b64encode(secret.encode("utf-8")).decode("ascii")):
            text = text.replace(form, "***REDACTED***")
    return text


def _apply_edge_oidc_secret(driver: Any, ctx_slug: str, cluster: Any, selected_set: set[str]) -> None:
    """Write the edge's OIDC client Secret from the cluster row (#2055).

    Envoy's SecurityPolicy references the client secret by name. Built here,
    at apply time, rather than in the recipe, which operators read over
    GraphQL, and written with its namespace before the releases, so the
    policy never renders against a missing Secret. When the row carries no
    ``client_secret`` an existing Secret is kept (the platform does not own
    it); the run refuses only when the gate is configured and neither exists.
    No log line or error carries the secret.
    """
    from providers.k8s_native.edge_gateway import (
        EDGE_COMPONENT_KEY,
        EDGE_NAMESPACE,
        EDGE_OIDC_SECRET_NAME,
        edge_configured,
        edge_oidc_secret_manifest,
    )

    if EDGE_COMPONENT_KEY not in selected_set:
        return
    from core.app_deploy import AppDeployError

    config = getattr(cluster, "oidc_auth_config", None) or {}
    manifest = edge_oidc_secret_manifest(config)
    if manifest is None:
        if (
            edge_configured(config)
            and driver.get_manifest(ctx_slug, EDGE_NAMESPACE, "Secret", EDGE_OIDC_SECRET_NAME) is None
        ):
            raise AppDeployError(
                f"the Envoy edge reads Secret {EDGE_OIDC_SECRET_NAME} in {EDGE_NAMESPACE}, which does not "
                "exist, and the cluster's oidcAuthConfig has no client_secret to write it from. Set it "
                "with updateTenantCluster, then run the install again.",
            )
        return
    namespace = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": EDGE_NAMESPACE, "labels": {"astrolift.io/managed-by": "platform"}},
    }
    result = driver.apply_manifests(ctx_slug, EDGE_NAMESPACE, [namespace, manifest])
    if not result.ok:
        raise AppDeployError(
            f"could not write Secret {EDGE_OIDC_SECRET_NAME} in {EDGE_NAMESPACE}: "
            + _without_secrets(
                "; ".join(str(e) for e in result.errors), [str(config.get("client_secret") or "")]
            ),
        )
    log.info(
        "install_cluster_prereqs: edge OIDC Secret %s/%s created=%d updated=%d unchanged=%d",
        EDGE_NAMESPACE,
        EDGE_OIDC_SECRET_NAME,
        len(result.created),
        len(result.updated),
        len(result.unchanged),
    )


def _edge_controllers_for_additive_run(
    cluster: Any, components: list[Any], selected_set: set[str]
) -> set[str]:
    """The controllers the control plane's own edge install brings along (#2130).

    Probed live, not read from the stored capabilities: on a fresh cluster the
    probe may never have run, and guessing "absent" there would install a
    second copy of a controller someone put in by hand. A probe that fails
    raises, so Temporal retries rather than the run going ahead blind.
    """
    from astrolift_clusters.edge_install import edge_controllers_to_add
    from core.cluster_management import probe_cluster_capabilities_dispatch
    from providers.k8s_native.edge_gateway import EDGE_COMPONENT_KEY

    if EDGE_COMPONENT_KEY not in selected_set:
        return set()
    recipe_keys = {c.key for c in components}
    capabilities = probe_cluster_capabilities_dispatch(cluster=cluster)
    added = edge_controllers_to_add(recipe_keys, capabilities)
    if added:
        log.info(
            "install_cluster_prereqs: the edge install adds %s, which the cluster does not run",
            sorted(added),
        )
    return added


def _install_cluster_prereqs_sync(
    cluster_id: int,
    selected_keys: list[str],
    option_overrides: dict[str, dict[str, str]],
    additive: bool = False,
) -> dict[str, Any]:
    from astrolift_clusters.models import TenantCluster
    from core.app_deploy import AppDeployError
    from core.cluster_management import (
        _context_for_cluster,  # type: ignore[attr-defined]
        _driver_for_cluster,  # type: ignore[attr-defined]
        bootstrap_components_dispatch,
    )

    cluster = TenantCluster.objects.select_related(
        "provider_plugin",
    ).get(pk=cluster_id)
    if "envoy-gateway" in selected_keys:
        from astrolift_clusters.edge_install import edge_support_refusal

        if refusal := edge_support_refusal(cluster):
            raise AppDeployError(refusal)
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)

    components = bootstrap_components_dispatch(cluster=cluster)
    selected_set = set(selected_keys)
    target_namespace = "astrolift-system"

    if additive:
        selected_set |= _edge_controllers_for_additive_run(cluster, components, selected_set)

    # Self-provision the AWS controllers' IRSA roles before their HelmReleases
    # land, so each controller can assume its role as soon as its pods start
    # (#1032 EBS-CSI, #1044 ALB controller + external-dns). No-op for non-AWS
    # clusters or when the component is deselected.
    _provision_ebs_csi_irsa_role(cluster, selected_set)
    _provision_aws_controller_irsa_role(
        cluster,
        selected_set,
        component_key=_ALB_CONTROLLER_COMPONENT_KEY,
        mint_method="provision_alb_controller_role",
    )
    _provision_aws_controller_irsa_role(
        cluster,
        selected_set,
        component_key=_EXTERNAL_DNS_COMPONENT_KEY,
        mint_method="provision_external_dns_role",
    )
    _provision_aws_controller_irsa_role(
        cluster,
        selected_set,
        component_key=_CLOUDWATCH_EXPORTER_COMPONENT_KEY,
        mint_method="provision_cloudwatch_exporter_role",
    )
    _provision_aws_controller_irsa_role(
        cluster,
        selected_set,
        component_key=_S3_CSI_COMPONENT_KEY,
        mint_method="provision_s3_csi_role",
    )

    _apply_edge_oidc_secret(driver, ctx.slug, cluster, selected_set)

    # The releases this run renders. The stale cleanup below deletes every
    # other recipe release, so a dependsOn naming one can never be met and
    # Flux holds the dependent forever: the Dex a Cognito-backed auth host
    # never uses, or a controller the cluster runs outside Flux.
    rendered_release_keys = {c.key for c in components if c.key in selected_set and c.chart_repo_url}

    resources: list[dict[str, Any]] = []
    applied: list[dict[str, str]] = []
    skipped: list[str] = []

    # Collect HelmRepository manifests first (one per unique repo URL,
    # deduplicated so multiple components sharing a registry emit only
    # one HelmRepository resource). We build them in a dict keyed by
    # repo URL, then prepend them to the apply list so Flux can resolve
    # the source reference before the HelmRelease objects land.
    repo_manifests: dict[str, dict[str, Any]] = {}

    # Track which HelmRelease names this run actively manages — any
    # platform-managed release NOT in this set is either deselected or
    # legacy and should be removed so Flux stops reconciling it.
    expected_release_names: set[str] = set()

    for component in components:
        if component.key not in selected_set:
            skipped.append(component.key)
            continue
        if not component.chart_repo_url:
            # Chart-less component: emit NO HelmRelease. This branch only skips
            # the HelmRelease render — it must NOT drop the component's
            # post_install_manifests, which the post-install pass below applies
            # (in depends_on order, foundational kinds first) regardless of
            # whether the component had a chart. Two flavors:
            #   - carries post_install_manifests (e.g. knative-serving: the
            #     vendored Knative operator YAML + a KnativeServing CR) — those
            #     stand the component up in place of a chart.
            #   - no post_install_manifests — a true no-op (cloud-native
            #     annotation-based TLS: ACM on EKS, GKE-managed certs, AppGW on
            #     AKS handle certs without an in-cluster controller).
            # Either way, record it in ``applied`` so the UI reflects the
            # operator's choice.
            if component.post_install_manifests:
                log.info(
                    "install_cluster_prereqs: component %s is chart-less — no "
                    "HelmRelease; its %d post-install manifest(s) install it",
                    component.key,
                    len(component.post_install_manifests),
                )
            else:
                log.info(
                    "install_cluster_prereqs: component %s is chart-less with "
                    "no post-install manifests — cloud-native no-op path",
                    component.key,
                )
            applied.append({"name": component.key, "version": ""})
            continue

        component_options = option_overrides.get(component.key, {})
        merged_values = _merge_helm_values(component.helm_values, component_options)
        merged_values = _apply_semantic_options(component.key, merged_values, component_options)
        _assert_storage_class_preflight(driver, ctx.slug, component.key, component_options)
        depends_on = [dep for dep in component.depends_on if dep in rendered_release_keys]

        # Slug the repo URL into a valid K8s resource name:
        # strip scheme, replace non-alphanumeric with '-', truncate to 52 chars
        # (HelmRepository name limit is 63; "helmrepo-" prefix + 52 = 61).
        repo_slug = re.sub(r"[^a-z0-9]+", "-", component.chart_repo_url.lower().split("//")[-1].rstrip("/"))
        repo_slug = repo_slug.strip("-")[:52]
        repo_name = f"helmrepo-{repo_slug}"

        if component.chart_repo_url not in repo_manifests:
            repo_spec: dict[str, Any] = {
                "interval": "1h",
                "url": component.chart_repo_url,
            }
            if component.chart_repo_type == "oci":
                repo_spec["type"] = "oci"
            repo_manifests[component.chart_repo_url] = {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "HelmRepository",
                "metadata": {
                    "name": repo_name,
                    "namespace": target_namespace,
                    "labels": {"astrolift.io/managed-by": "platform"},
                },
                "spec": repo_spec,
            }

        release_name = f"astrolift-{component.key.replace('_', '-')}"
        expected_release_names.add(release_name)

        chart_spec: dict[str, Any] = {
            "chart": component.chart_name,
            "sourceRef": {
                "kind": "HelmRepository",
                "name": repo_name,
                "namespace": target_namespace,
            },
        }
        if component.chart_version:
            chart_spec["version"] = component.chart_version

        helm_release = {
            "apiVersion": "helm.toolkit.fluxcd.io/v2",
            "kind": "HelmRelease",
            "metadata": {
                "name": release_name,
                "namespace": target_namespace,
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/bootstrap-component": component.key,
                },
            },
            "spec": {
                "interval": "5m",
                "timeout": component.install_timeout,
                "chart": {"spec": chart_spec},
                "install": {"createNamespace": True, "remediation": {"retries": 3}},
                "upgrade": {"remediation": {"retries": 3}},
                "values": merged_values,
                **(
                    {
                        "dependsOn": [
                            {
                                "name": f"astrolift-{dep.replace('_', '-')}",
                                "namespace": target_namespace,
                            }
                            for dep in depends_on
                        ]
                    }
                    if depends_on
                    else {}
                ),
            },
        }
        resources.append(helm_release)
        applied.append({"name": component.key, "version": component.chart_version})

    # Prepend HelmRepository manifests so Flux registers the chart
    # sources before the HelmRelease objects that reference them.
    resources = list(repo_manifests.values()) + resources

    result = driver.apply_manifests(ctx.slug, target_namespace, resources)
    if not result.ok:
        if _flux_crd_missing(result.errors):
            # Flux CRDs aren't registered yet — bootstrap Flux and then let
            # Temporal retry the activity. We don't retry inline because CRD
            # group registration is async and takes 20-60 s after apply; a
            # naive in-process sleep would either be too short (same failure)
            # or waste the activity heartbeat budget. Instead we raise a
            # retriable ApplicationError so Temporal reschedules the attempt
            # after its configured retry delay; by then Flux is fully ready.
            log.info(
                "install_cluster_prereqs: Flux CRDs missing on cluster %s — "
                "bootstrapping Flux; Temporal will retry",
                ctx.slug,
            )
            _ensure_flux_installed(driver, ctx.slug)
            from temporalio.exceptions import ApplicationError

            raise ApplicationError(
                f"Flux bootstrapped on cluster {ctx.slug!r}; "
                "waiting for CRD group registration — Temporal will retry",
                non_retryable=False,
            )

    if not result.ok:
        raise AppDeployError(
            "install_cluster_prereqs apply failed: " + "; ".join(str(e) for e in result.errors),
        )

    # ---- Stale resource cleanup -----------------------------------------
    # Delete HelmRelease objects that this run no longer manages:
    #   • Deselected components: operator unchecked a component on re-run.
    #   • Legacy names: components renamed across platform versions.
    # Best-effort — a delete failure is logged but doesn't fail the activity;
    # the apply above already succeeded so the cluster is in the right
    # desired state for selected components.
    deleted: list[str] = []

    # An additive run applies its selection and removes nothing: the other
    # recipe releases were not deselected, just not part of this run (#2130).
    # Deselected components that had charts (skipped components without
    # chart_repo_url never emitted a HelmRelease, so nothing to delete).
    deselected_releases = [
        f"astrolift-{c.key.replace('_', '-')}"
        for c in components
        if c.key not in selected_set and c.chart_repo_url
    ]
    to_delete_releases = set(deselected_releases) | _LEGACY_HELM_RELEASE_NAMES
    to_delete_repos = _LEGACY_HELM_REPO_NAMES

    stale_manifests: list[dict[str, Any]] = [
        {
            "apiVersion": "helm.toolkit.fluxcd.io/v2",
            "kind": "HelmRelease",
            "metadata": {"name": name, "namespace": target_namespace},
        }
        for name in to_delete_releases
    ] + [
        {
            "apiVersion": "source.toolkit.fluxcd.io/v1",
            "kind": "HelmRepository",
            "metadata": {"name": name, "namespace": target_namespace},
        }
        for name in to_delete_repos
    ]

    if stale_manifests and not additive:
        try:
            del_result = driver.delete_manifests(ctx.slug, target_namespace, stale_manifests)
            deleted = list(del_result.deleted)
            log.info(
                "install_cluster_prereqs: stale cleanup deleted=%d not_found=%d on cluster %s",
                len(deleted),
                len(del_result.not_found),
                ctx.slug,
            )
            if del_result.errors:
                log.warning(
                    "install_cluster_prereqs: stale cleanup errors (non-fatal): %s",
                    "; ".join(del_result.errors),
                )
        except Exception as exc:
            log.warning(
                "install_cluster_prereqs: stale cleanup failed (non-fatal): %s",
                exc,
            )

    # ---- Post-install manifests -----------------------------------------
    # A component may ship raw k8s objects that complete its install once its
    # HelmRelease is applied (e.g. the knative-operator chart installs the
    # operator + CRDs; the KnativeServing CR + its namespace stand Knative up).
    # Applied idempotently via SSA, in depends_on order. On the first run the
    # operator chart Flux just started reconciling hasn't registered its CRDs
    # yet, so the CR apply fails with "no matches for kind" — we treat that as
    # transient and raise a retriable error so Temporal re-runs; the operator
    # comes up and the idempotent re-apply lands the CR. Same convergence path
    # the Flux-own-CRD bootstrap above uses.
    post_install_crd_not_ready, post_install_errors = _apply_post_install_manifests(
        driver,
        ctx.slug,
        components,
        selected_set,
        target_namespace,
    )
    if post_install_crd_not_ready:
        from temporalio.exceptions import ApplicationError

        log.info(
            "install_cluster_prereqs: post-install CRs on cluster %s are waiting "
            "for their operator's CRDs to register — Temporal will retry",
            ctx.slug,
        )
        raise ApplicationError(
            f"post-install custom resources on cluster {ctx.slug!r} are waiting "
            "for their operator's CRDs to register — Temporal will retry",
            non_retryable=False,
        )

    # What the bootstrap-run row records as the recipe's set. An additive run
    # added to the recipe rather than replacing it, so it records what it
    # applied on top of what the last run left. Recording only its own
    # releases would make the next operator run read every other recipe
    # release as running outside the recipe, leave it unchecked, and delete it.
    recorded = applied
    if additive:
        from astrolift_clusters.recipe_detection import components_installed_by_recipe

        mine = {a["name"] for a in applied}
        recorded = applied + [
            {"name": key, "version": ""}
            for key in sorted(components_installed_by_recipe(cluster))
            if key not in mine
        ]

    return {
        "applied": applied,
        "recorded": recorded,
        "skipped": skipped,
        "deleted": deleted,
        "namespace": target_namespace,
        "created": list(result.created),
        "updated": list(result.updated),
        "unchanged": list(result.unchanged),
        "post_install_errors": post_install_errors,
    }


def _record_bootstrap_run_sync(
    cluster_id: int,
    actor_user_id: int | None,
    status: str,
    applied: list[dict[str, str]],
    error_message: str,
    started_at_iso: str,
    ended_at_iso: str,
    organization_id: int | None = None,
) -> None:
    """Persist a ``ClusterBootstrapRun`` row for a UI-triggered install.

    Mirror of what ``recordClusterBootstrapRun`` (called by the CLI)
    writes, so the "Last bootstrap" panel populates regardless of how
    the operator initiated the install. Best-effort — callers should
    catch exceptions and not let a write failure mask the real outcome.

    ``organization_id`` is the org that asked for the install (#1955).
    A workflow started before it was passed sends none; only the owning
    org can install on an org-owned cluster, so that cluster's org is
    the right owner, and a shared cluster's legacy run stays unowned.
    """
    from datetime import datetime

    from astrolift_clusters.models import ClusterBootstrapRun, TenantCluster

    cluster = TenantCluster.objects.get(pk=cluster_id)
    user = None
    if actor_user_id is not None:
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.filter(pk=actor_user_id).first()

    ClusterBootstrapRun.objects.create(
        tenant_cluster=cluster,
        organization_id=organization_id if organization_id is not None else cluster.organization_id,
        triggered_by=user,
        status=status,
        installed_releases=applied,
        chart_version="",  # Flux manages chart versions; not known at apply time
        cli_version="",
        host_info={"triggered_via": "ui"},
        error_message=error_message,
        started_at=datetime.fromisoformat(started_at_iso),
        ended_at=datetime.fromisoformat(ended_at_iso),
    )


@activity.defn(name="astrolift.cluster.record_bootstrap_run")
async def record_cluster_bootstrap_run(
    cluster_id: int,
    actor_user_id: int | None,
    status: str,
    applied: list[dict[str, str]],
    error_message: str,
    started_at_iso: str,
    ended_at_iso: str,
    organization_id: int | None = None,
) -> None:
    """Write a ``ClusterBootstrapRun`` record so the UI 'Last bootstrap'
    panel reflects UI-triggered installs, not just CLI runs."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_record_bootstrap_run_sync)(
        cluster_id,
        actor_user_id,
        status,
        applied,
        error_message,
        started_at_iso,
        ended_at_iso,
        organization_id,
    )
    log.info(
        "record_cluster_bootstrap_run cluster_id=%d status=%s applied=%d",
        cluster_id,
        status,
        len(applied),
    )


@activity.defn(name="astrolift.cluster.install_prereqs")
async def install_cluster_prereqs(
    cluster_id: int,
    selected_keys: list[str],
    option_overrides: dict[str, dict[str, str]],
    additive: bool = False,
) -> dict[str, Any]:
    """Apply Flux ``HelmRelease`` CRDs for each selected bootstrap
    component to the cluster. Idempotent — re-run converges; an
    operator who unchecks a component on a subsequent run will see
    the HelmRelease delete + the cluster reconcile away the install.

    ``option_overrides`` is keyed by component.key; the inner dict is
    the operator's option-key → value-from-choices selection (e.g.
    ``{"tls_issuer": {"mode": "acm"}}``).
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_install_cluster_prereqs_sync)(
        cluster_id,
        selected_keys,
        option_overrides,
        additive,
    )
    log.info(
        "install_cluster_prereqs applied=%d skipped=%d",
        len(result["applied"]),
        len(result["skipped"]),
        extra={"cluster_id": cluster_id},
    )
    return result
