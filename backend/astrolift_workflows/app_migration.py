"""
App migration policy: export from instance A → import on instance B (#67).

Pure-Python policy. The ``ExportAppWorkflow`` (instance A) and
``ImportAppWorkflow`` (instance B) consult this module for:

* **Bundle structure** — what an export bundle carries (manifest,
  env config, managed-service variants, recent deployment refs).
  Importers refuse bundles missing required sections.
* **JWT signing/verification rules** — issuer + audience binding,
  15-minute TTL, single-use audit (jti dedup), nbf/exp clock-skew
  tolerance.
* **Mapping** — instance A's ``org-slug.app-slug`` becomes B's
  ``imported_from`` lineage stamp; managed-service variants map
  by kind+version, not by A's binding ID.
* **Source-instance pause** — Export pauses A's environment so
  the operator can decide to flip; this module decides which
  state the env should land in (Paused) and what gets unpaused
  if the operator aborts.

Cross-instance import is the federation primitive in #32. This
module is shared by both sides — the export workflow imports it
to *build* a bundle; the import workflow imports it to *verify
and unpack* one.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Mapping, Sequence
from enum import Enum


class MigrationError(ValueError):
    pass


# ---- bundle structure ----------------------------------------------


BUNDLE_VERSION = 1
"""Schema version. Importers refuse bundles with newer versions
(forward-compat is opt-in, not implicit) and warn on older
versions while still attempting load."""

JWT_TTL_SECONDS = 15 * 60
"""Spec acceptance: bundle JWT is short-lived. 15 minutes covers
the operator's typical export → import flow without leaving an
exfiltrated bundle reusable in steady state."""

CLOCK_SKEW_SECONDS = 30
"""nbf/exp tolerance. Federation = clocks across two installs;
small skew shouldn't fail otherwise valid bundles."""


@dataclasses.dataclass(frozen=True, slots=True)
class ManagedServiceVariant:
    """One managed service binding's portable descriptor.

    The import side maps ``kind`` + ``major_version`` to its
    own catalog (instance A's binding ID is meaningless on B).
    """

    kind: str
    """e.g. ``postgres``, ``redis``, ``s3-compatible-bucket``."""

    major_version: int
    """Major-version compat is sufficient for managed services;
    minor drift is the operator's problem during cutover."""

    role: str
    """The role the source app references this binding as
    (e.g. ``primary_db``). Carried over so the manifest's
    binding refs resolve."""


@dataclasses.dataclass(frozen=True, slots=True)
class DeploymentHistoryEntry:
    """Recent deployment markers carried for context — image
    digest + manifest sha so the import side can reproduce a
    last-known-good state without bringing the actual rollout
    history."""

    image_digest: str
    manifest_sha256: str
    deployed_at_unix: int


@dataclasses.dataclass(frozen=True, slots=True)
class ExportBundle:
    """The full bundle payload signed by instance A. Operators
    carry this (or the mobile app brokers it per #56) to
    instance B's import workflow."""

    bundle_version: int
    source_instance: str
    """Stable identifier of instance A (DNS zone of the install
    per spec 01 §1.5)."""

    source_org_slug: str
    source_app_slug: str

    manifest_normalized: str
    """Normalized manifest TOML — re-serialized so ordering /
    whitespace differences don't surface in the bundle digest."""

    env_config: Mapping[str, str]
    """Per-env scalar config. Secret VALUES are not included —
    only secret REFS (the operator must re-issue secrets on B)."""

    secret_refs: tuple[str, ...]
    """Names of secrets the manifest references. Import side
    surfaces this list for the operator to re-create on B."""

    managed_services: tuple[ManagedServiceVariant, ...]
    deployment_history: tuple[DeploymentHistoryEntry, ...]
    """Recent N deployments from A (typically last 3). The most
    recent's image_digest is what import will deploy first."""

    exported_at_unix: int

    def __post_init__(self) -> None:
        if self.bundle_version != BUNDLE_VERSION:
            # Construction-time guard; verifier checks again
            # at unpack-time when version field comes from JWT.
            raise MigrationError(
                f"unsupported bundle version {self.bundle_version} "
                f"(this code understands v{BUNDLE_VERSION})"
            )
        if not self.source_org_slug or not self.source_app_slug:
            raise MigrationError(
                "source_org_slug and source_app_slug both required"
            )
        if not self.source_instance:
            raise MigrationError("source_instance is required")
        if not self.manifest_normalized:
            raise MigrationError("manifest_normalized is required")
        if not self.deployment_history:
            raise MigrationError(
                "deployment_history must include at least one entry "
                "(the most recent rollout drives first import deploy)"
            )


# ---- JWT claim shape -----------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class BundleJwtClaims:
    """Claims encoded in the bundle JWT.

    Bundle payload itself is the JWT body's ``bundle`` claim.
    Standard claims (iss/aud/iat/nbf/exp/jti) wrap it.
    """

    iss: str
    """Issuer — always source instance's identifier."""

    aud: str
    """Audience — the destination instance the operator intends.
    Verifier on B refuses bundles where aud != B's identifier."""

    iat: int
    nbf: int
    exp: int
    jti: str
    """Single-use marker. Import side records jti in a dedup
    table; replay attempts return ``ALREADY_USED``."""

    bundle: ExportBundle


def build_claims(
    *,
    source_instance: str,
    destination_instance: str,
    bundle: ExportBundle,
    issued_at_unix: int,
    jti: str,
    ttl_seconds: int = JWT_TTL_SECONDS,
) -> BundleJwtClaims:
    """Construct claims with consistent iss/aud/exp."""
    if not destination_instance:
        raise MigrationError(
            "destination_instance is required (audience binding); "
            "an unbound bundle could be imported to any instance"
        )
    if destination_instance == source_instance:
        raise MigrationError(
            "destination must differ from source — migration "
            "exists to move between installs"
        )
    if ttl_seconds <= 0 or ttl_seconds > JWT_TTL_SECONDS:
        # Refuse longer-lived bundles even if caller asks for one.
        # The 15-min ceiling is a security invariant.
        raise MigrationError(
            f"ttl_seconds must be > 0 and <= {JWT_TTL_SECONDS} "
            f"(got {ttl_seconds})"
        )
    if not jti:
        raise MigrationError("jti is required (single-use marker)")

    return BundleJwtClaims(
        iss=source_instance,
        aud=destination_instance,
        iat=issued_at_unix,
        nbf=issued_at_unix,
        exp=issued_at_unix + ttl_seconds,
        jti=jti,
        bundle=bundle,
    )


# ---- verifier ------------------------------------------------------


class VerificationResult(str, Enum):
    """Why a bundle was accepted or rejected at import time."""

    OK = "ok"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"
    """Sometimes triggered by clock skew on instance A's side
    issuing iat slightly in the future. Tolerance applies."""

    AUDIENCE_MISMATCH = "audience_mismatch"
    UNSUPPORTED_VERSION = "unsupported_version"
    ALREADY_USED = "already_used"
    SAME_INSTANCE = "same_instance"
    """Operator pointed an export at the same install — no-op."""


@dataclasses.dataclass(frozen=True, slots=True)
class VerificationDecision:
    accepted: bool
    result: VerificationResult
    reason: str


def verify_claims(
    *,
    claims: BundleJwtClaims,
    expected_destination: str,
    now_unix: int,
    seen_jti_lookup,
) -> VerificationDecision:
    """Decide whether to accept an import bundle.

    ``seen_jti_lookup``: callable(jti) -> bool (True if already
    consumed). The import workflow records jti AFTER successful
    verification so a half-failed import can be retried with the
    same bundle.
    """
    if claims.bundle.bundle_version != BUNDLE_VERSION:
        return VerificationDecision(
            accepted=False,
            result=VerificationResult.UNSUPPORTED_VERSION,
            reason=(
                f"bundle version {claims.bundle.bundle_version} "
                f"unsupported (expected {BUNDLE_VERSION})"
            ),
        )

    if claims.aud != expected_destination:
        return VerificationDecision(
            accepted=False,
            result=VerificationResult.AUDIENCE_MISMATCH,
            reason=(
                f"bundle audience {claims.aud!r} does not match "
                f"this instance {expected_destination!r}"
            ),
        )

    if claims.iss == expected_destination:
        # Operator pointed export at the same install (typo or
        # accidental double-target). No work to do.
        return VerificationDecision(
            accepted=False,
            result=VerificationResult.SAME_INSTANCE,
            reason=(
                f"source and destination are the same instance "
                f"({expected_destination!r}); migration is a no-op"
            ),
        )

    if now_unix < claims.nbf - CLOCK_SKEW_SECONDS:
        return VerificationDecision(
            accepted=False,
            result=VerificationResult.NOT_YET_VALID,
            reason=f"bundle nbf={claims.nbf} > now={now_unix}",
        )

    if now_unix > claims.exp + CLOCK_SKEW_SECONDS:
        return VerificationDecision(
            accepted=False,
            result=VerificationResult.EXPIRED,
            reason=(
                f"bundle expired (exp={claims.exp}, now={now_unix})"
            ),
        )

    if seen_jti_lookup(claims.jti):
        return VerificationDecision(
            accepted=False,
            result=VerificationResult.ALREADY_USED,
            reason=f"bundle jti={claims.jti!r} already consumed",
        )

    return VerificationDecision(
        accepted=True,
        result=VerificationResult.OK,
        reason="bundle verified",
    )


# ---- source-instance pause / revert --------------------------------


class SourceState(str, Enum):
    """Tracks what happened on instance A during export."""

    LIVE = "live"
    """Pre-export."""

    PAUSED = "paused"
    """Post-export. Manifest unchanged, but env's ``deploys_paused``
    flag is set so a webhook from CI doesn't ship a divergent
    rollout while the import is mid-flight."""

    EXPORTED_LIVE = "exported_live"
    """Operator chose --no-pause (rare, for parallel-running
    A/B). Bundle is still issued; A continues to serve."""


@dataclasses.dataclass(frozen=True, slots=True)
class ExportPausePlan:
    """The source-side decision: does the export pause A?"""

    pause_source: bool
    target_state: SourceState
    """The state A's env should land in after export."""

    revert_state: SourceState
    """The state A's env should land in if the operator aborts
    or if import fails after a pause."""


def plan_pause_for_export(
    *,
    operator_requested_pause: bool,
    current_source_state: SourceState,
) -> ExportPausePlan:
    """Decide what to do with A's env state.

    Default is to pause; --no-pause is the operator's explicit
    parallel-mode signal. Already-paused source means the
    operator already paused (e.g. via the GraphQL mutation),
    so we don't re-pause and don't revert past their choice.
    """
    if not operator_requested_pause:
        return ExportPausePlan(
            pause_source=False,
            target_state=SourceState.EXPORTED_LIVE,
            revert_state=current_source_state,
        )

    if current_source_state == SourceState.PAUSED:
        # Already paused by operator; export piggybacks. Revert
        # would un-do their pause, so we leave it paused.
        return ExportPausePlan(
            pause_source=False,
            target_state=SourceState.PAUSED,
            revert_state=SourceState.PAUSED,
        )

    return ExportPausePlan(
        pause_source=True,
        target_state=SourceState.PAUSED,
        revert_state=SourceState.LIVE,
    )


# ---- import-side mapping -------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ImportTargetContext:
    """Where on instance B the app lands."""

    destination_org_slug: str
    destination_project_slug: str
    """The new app on B may live under a different org/project
    than on A — operator picks at import time."""

    org_exists: bool
    project_exists: bool


@dataclasses.dataclass(frozen=True, slots=True)
class ImportPlan:
    """The plan the import workflow executes."""

    needs_org_create: bool
    needs_project_create: bool
    new_app_slug: str
    imported_from: str
    """Lineage stamp: ``<source_instance>/<src_org>/<src_app>``.
    Stored on the new app so the UI can render 'imported from
    A' and the operator can audit federation links."""

    first_deploy_image_digest: str
    first_deploy_manifest_sha256: str


def plan_import(
    *,
    bundle: ExportBundle,
    target: ImportTargetContext,
) -> ImportPlan:
    """Build the import plan from a verified bundle + operator
    target choice."""
    if not target.destination_org_slug:
        raise MigrationError("destination_org_slug is required")
    if not target.destination_project_slug:
        raise MigrationError("destination_project_slug is required")

    if not bundle.deployment_history:
        # Verifier should have caught this; defense-in-depth
        # for callers building plans from synthesized bundles
        # in tests.
        raise MigrationError(
            "bundle has empty deployment_history — cannot pick "
            "first-deploy image"
        )

    most_recent = max(
        bundle.deployment_history, key=lambda d: d.deployed_at_unix,
    )

    return ImportPlan(
        needs_org_create=not target.org_exists,
        needs_project_create=(
            target.org_exists and not target.project_exists
        ),
        # ^ if org doesn't exist, both will get created in the
        # workflow but project_create is bundled inside the org
        # create step; flag stays False to avoid double-create
        new_app_slug=bundle.source_app_slug,
        imported_from=(
            f"{bundle.source_instance}/"
            f"{bundle.source_org_slug}/{bundle.source_app_slug}"
        ),
        first_deploy_image_digest=most_recent.image_digest,
        first_deploy_manifest_sha256=most_recent.manifest_sha256,
    )


# ---- managed service mapping ---------------------------------------


def required_service_kinds(
    *, bundle: ExportBundle,
) -> tuple[tuple[str, int], ...]:
    """The (kind, major_version) tuples B must satisfy. Catalog
    lookup on B's side picks the actual driver/variant; this
    keeps the policy layer driver-neutral."""
    seen: set[tuple[str, int]] = set()
    out: list[tuple[str, int]] = []
    for svc in bundle.managed_services:
        key = (svc.kind, svc.major_version)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return tuple(out)


def missing_secret_refs(
    *,
    bundle: ExportBundle,
    secrets_present_on_destination: Sequence[str],
) -> tuple[str, ...]:
    """Spec acceptance: import surfaces secrets the operator
    must re-create on B (since values aren't carried). This
    helper computes the diff for the import-time UI."""
    present = set(secrets_present_on_destination)
    return tuple(
        ref for ref in bundle.secret_refs if ref not in present
    )


# ---- helpers -------------------------------------------------------


def is_jwt_recently_issued(*, claims: BundleJwtClaims, now_unix: int) -> bool:
    """Heuristic for the 'fresh enough to use' check the import
    UI calls when the operator pastes a bundle — earlier than
    the verifier so we can show a friendly 'this bundle just
    expired, ask for a new one' message instead of a generic
    error.
    """
    return (
        claims.iat - CLOCK_SKEW_SECONDS
        <= now_unix
        <= claims.exp + CLOCK_SKEW_SECONDS
    )


def now_unix() -> int:
    """Tests inject explicit timestamps; production uses this.
    Centralized so the policy doesn't import time across N
    callsites."""
    return int(time.time())
