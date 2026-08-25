"""Is this app actually built? (#1550)

Operators ask "is this app fully wired?" and the answer was scattered.
`autowire_state` covers the repo half -- webhook, CI file, deploy secret --
and nothing checked the cloud half at all. So an app could read as autowired
while its ECR repo, its IRSA role, its DNS record or the image its latest
deployment references did not exist.

This is the rollup. **DB-only, and that is a design choice rather than a
shortcut**: it runs on the app detail page, and a check that reaches four
cloud APIs turns a page load into a multi-second fan-out that fails when any
one provider is slow. Every check here answers from state the platform
already persisted at provision time.

The consequence is stated per check rather than hidden: a check whose answer
requires a live call reports `UNKNOWN`, never `PASS`. "We did not look" and
"we looked and it is fine" are different answers, and collapsing them is how
a doctor panel becomes a green light nobody trusts.
"""

from __future__ import annotations

import dataclasses
from enum import StrEnum


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    """State the platform never recorded, so it cannot be answered without a
    live call. Deliberately not PASS."""

    NOT_APPLICABLE = "not_applicable"
    """This app does not need the thing, e.g. no custom domain to resolve."""


@dataclasses.dataclass(frozen=True, slots=True)
class Check:
    key: str
    status: CheckStatus
    detail: str = ""
    fix: str = ""
    """The action that would repair it, as an operator-facing verb. Empty
    when nothing the platform offers would help."""


def diagnose(app) -> list[Check]:
    """Every dependency check for ``app``, in a stable order.

    Stable so the panel does not reorder between loads, and so a diff of two
    diagnoses is readable.
    """
    return [
        _check_manifest(app),
        _check_repo_wiring(app),
        _check_registry(app),
        _check_push_role(app),
        _check_identity(app),
        _check_image(app),
        _check_deployments(app),
    ]


def is_healthy(checks: list[Check]) -> bool:
    """True when nothing failed.

    UNKNOWN does not fail the app. It means the platform cannot answer from
    what it stored, and reporting an app broken on that basis would make the
    panel cry wolf -- but it is not PASS either, so the operator sees the gap.
    """
    return not any(c.status is CheckStatus.FAIL for c in checks)


# ---- individual checks ------------------------------------------------


def _check_manifest(app) -> Check:
    """The app's registration matches the manifest it was registered from.

    `manifest_hash` is the normalised astrolift.toml; `last_synced_hash` is
    what the repo last agreed to. A mismatch is the same signal the
    config-drift banner reports (#1603) and the fix is the same resync.
    """
    current = (app.manifest_hash or "").strip()
    if not current:
        return Check(
            key="manifest",
            status=CheckStatus.FAIL,
            detail="no manifest has been parsed for this app",
            fix="resync the manifest from the repo",
        )
    synced = (app.last_synced_hash or "").strip()
    if synced and synced != current:
        return Check(
            key="manifest",
            status=CheckStatus.FAIL,
            detail="the repo manifest differs from the registered one",
            fix="resync the manifest from the repo",
        )
    return Check(key="manifest", status=CheckStatus.PASS)


def _check_repo_wiring(app) -> Check:
    """Webhook, CI file and deploy secret, from the autowire snapshot.

    Reuses `autowire_state` rather than re-deriving it: #1108 already owns
    that vocabulary, and a second opinion here would be free to disagree with
    the callout the operator is already looking at.
    """
    state = app.autowire_state or {}
    if not state:
        return Check(
            key="repo_wiring",
            status=CheckStatus.UNKNOWN,
            detail="autowire has never run for this app",
            fix="run autowire",
        )
    errors = state.get("errors") or {}
    if errors:
        return Check(
            key="repo_wiring",
            status=CheckStatus.FAIL,
            detail="; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in sorted(errors.items())),
            fix="retry autowire",
        )
    return Check(key="repo_wiring", status=CheckStatus.PASS)


def _check_registry(app) -> Check:
    """A registry repo was provisioned and its URI recorded."""
    if not (app.registry_repo_uri or "").strip():
        return Check(
            key="registry",
            status=CheckStatus.FAIL,
            detail="no registry repository is recorded for this app",
            fix="reprovision",
        )
    return Check(key="registry", status=CheckStatus.PASS)


def _check_push_role(app) -> Check:
    """The CI push role exists.

    Whether its trust policy carries *both* OIDC subject patterns (#1532)
    cannot be answered from the row -- the trust document lives in IAM. So a
    recorded role is PASS on existence and the trust shape stays UNKNOWN
    rather than being assumed good, because getting that wrong is exactly the
    #1532 failure: a role that exists and refuses every assume.
    """
    ref = (app.push_role_ref or "").strip()
    if not ref:
        return Check(
            key="push_role",
            status=CheckStatus.FAIL,
            detail="no CI push role is recorded for this app",
            fix="reprovision",
        )
    return Check(
        key="push_role",
        status=CheckStatus.PASS,
        detail=f"{ref} (trust policy not verified from here)",
    )


def _check_identity(app) -> Check:
    """The platform can build a pod-identity annotation for this app.

    Nothing persists the binding: `_workload_identity_annotations` computes
    it at deploy time from the *cluster's* `provider_config`. So the check is
    whether that computation would produce anything -- and it is a real
    check, because the AWS branch returns `{}` when `account_id` is absent,
    which means the pod deploys with no identity and every AWS call it makes
    fails at runtime.

    A missing cluster is UNKNOWN rather than FAIL: an app registered and not
    yet bound to a cluster has not failed at anything.
    """
    cluster = getattr(app, "default_tenant_cluster", None)
    if cluster is None:
        return Check(
            key="identity",
            status=CheckStatus.UNKNOWN,
            detail="no cluster is bound, so no pod identity applies yet",
        )

    slug = str(getattr(getattr(cluster, "provider_plugin", None), "slug", "") or "")
    pc = getattr(cluster, "provider_config", None) or {}

    from core.app_deploy import _workload_identity_annotations

    annotations = _workload_identity_annotations(
        plugin_slug=slug,
        provider_config=pc,
        auth_config=getattr(cluster, "auth_config", None) or {},
        role_name=f"astrolift-{app.slug}",
    )
    if not annotations:
        return Check(
            key="identity",
            status=CheckStatus.FAIL,
            detail=(
                f"cluster {getattr(cluster, 'slug', '?')!r} is missing the "
                f"provider config needed to build a pod identity, so pods "
                f"deploy with none"
            ),
            fix="set the cluster's account/project identifier and reprovision",
        )
    return Check(key="identity", status=CheckStatus.PASS)


def _check_image(app) -> Check:
    """The latest deployment references an image, pinned by digest.

    Whether that digest is still present in the registry needs a live call.
    An unpinned tag is a FAIL rather than UNKNOWN, though: a deployment whose
    image is a floating tag cannot be reproduced, and that is knowable from
    the row.
    """
    from astrolift_lifecycle.models import Deployment

    latest = (
        Deployment.objects.filter(registered_app_id=app.pk, deleted_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if latest is None:
        return Check(
            key="image",
            status=CheckStatus.NOT_APPLICABLE,
            detail="this app has never been deployed",
        )
    if not (latest.image_digest or "").strip():
        return Check(
            key="image",
            status=CheckStatus.FAIL,
            detail="the latest deployment references an unpinned image tag",
            fix="redeploy to pin the image by digest",
        )
    return Check(
        key="image",
        status=CheckStatus.PASS,
        detail="pinned by digest (registry presence not verified from here)",
    )


def _check_deployments(app) -> Check:
    """No deployment stranded mid-flight (#1536).

    A row left in DEPLOYING blocks the next deploy's supersede logic and
    reads to an operator as "still working" forever.
    """
    from astrolift_lifecycle.models import Deployment

    stranded = Deployment.objects.filter(
        registered_app_id=app.pk,
        deleted_at__isnull=True,
        status__in=(Deployment.Status.DEPLOYING.value, Deployment.Status.REDEPLOYING.value),
    ).count()
    if stranded:
        return Check(
            key="deployments",
            status=CheckStatus.FAIL,
            detail=f"{stranded} deployment(s) stuck in flight",
            fix="abort the stranded deployment",
        )
    return Check(key="deployments", status=CheckStatus.PASS)
