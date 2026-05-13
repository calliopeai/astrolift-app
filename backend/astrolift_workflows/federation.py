"""
Federation policy: cross-install trust + transfer (#32).

Pure-Python policy. Astrolift's topology is **mesh-shaped**:
each install (DNS zone + DB; HA-scalable; per memory) is an
independent peer, and any install can talk to any other via
opt-in pairwise trust. No fixed topology constraint — operators
choose the shape (one ↔ one, hub ↔ spoke, full mesh, partial
mesh) per their needs.

**Use cases this enables:**
- **HA setups** — federated installs serve as hot-standby for
  each other; manifest + env state stays in sync so failover
  is a DNS swap, not a rebuild.
- **Geo-distributed multi-cloud** — apps spread across regions
  and clouds; one install can push manifests to peers in other
  regions/clouds for active-active or follow-the-sun rollouts.
- **App migration** — one-shot move of an app from install A
  to install B (e.g. cloud cost optimization).
- **Read-only discovery** — see what's deployed on a peer
  without granting write.

Federation is the trust + typed-RPC layer that makes the mesh
work. Per-kind primitives:

* **App manifest push/pull** — TOML + parsed metadata.
* **Env config push/pull** — per-env scalar config.
* **Secret push/pull** — value-level secret sharing (high-risk;
  separate capability).
* **App discovery query** — read-only inventory.

**Operator owns the policy** — what auto-syncs (vs manual
trigger), which peers, which kinds. Settings + permissions
modeled here; the operator UI is downstream.

This module owns the trust model. NO master, NO shared DB,
NO transitive trust. Each install treats peers as OIDC-style
IdPs for the narrow federation operations only.

* **Trust establishment** — pairwise, mutual, explicit. Each
  side imports the other's JWKS; trust expires unless renewed.
* **Audience binding** — every cross-install token names BOTH
  the source and destination so a stolen token can't be replayed
  against any third install.
* **Anti-loop** — no transitive trust; A trusts B doesn't imply
  A trusts C-via-B. No self-trust.
* **Capability scoping** — trust is per-capability (app_transfer,
  app_discovery_readonly); operator picks which to grant.
* **Revocation** — either side can revoke; revocation is
  authoritative even if the JWKS is still valid.

Pairs with #67 (app_migration policy — federation extends it
with multi-install audience).
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from enum import StrEnum


class FederationError(ValueError):
    pass


# ---- federation capabilities ---------------------------------------


class FederationCapability(StrEnum):
    """Locked vocabulary. Trust is granted per-capability so an
    operator can allow narrow flows without auto-granting the
    full surface. Capabilities are intentionally fine-grained:
    secret sharing is way more dangerous than manifest transfer
    and gets its own capability so operators can grant one
    without the other."""

    APP_DISCOVERY_READONLY = "app_discovery_readonly"
    """Read-only inventory queries. Returns app slugs + last
    deployed version + health badge. Doesn't expose secrets,
    manifest contents, or deployment logs."""

    APP_TRANSFER = "app_transfer"
    """Permits #67-style app migration: manifest TOML + parsed
    metadata + environment config. Does NOT include secrets —
    those need SECRET_SHARING separately. Operator grants this
    when they want apps to move between installs but expect
    secrets to be re-issued on the receiving side."""

    ENV_CONFIG_SHARE = "env_config_share"
    """Permits push/pull of per-environment scalar config
    (env vars). Often paired with APP_TRANSFER but split out
    so an install can update only env values across federation
    without re-shipping the manifest."""

    SECRET_SHARING = "secret_sharing"
    """Permits push/pull of secret VALUES (not just refs).
    High-risk: distinct capability so it must be granted
    explicitly. Use case: control-plane mesh where service in
    install A needs creds for a service in install B and
    operator chooses to source them from A's secrets backend."""


# ---- trust record --------------------------------------------------


_INSTALL_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{1,253}[a-z0-9])$")
"""Install identifiers are DNS zones (per memory). RFC 1035
hostname rules apply."""


def normalize_install_id(*, value: str) -> str:
    """Canonical install ID: lowercase + trailing-dot-stripped.
    Single-source-of-truth so trust lookup is consistent across
    JWT verification + revocation checks."""
    canonical = value.strip(".").lower()
    if not canonical or not _INSTALL_ID_RE.match(canonical):
        raise FederationError(f"install identifier {value!r} is not a valid DNS zone")
    return canonical


@dataclasses.dataclass(frozen=True, slots=True)
class FederationTrust:
    """One direction of a pairwise trust. The OTHER direction
    is a separate row.

    A grants APP_TRANSFER to B → row(local=A, remote=B,
    capabilities={app_transfer}). For B to push to A, B grants
    SEPARATELY: row(local=B, remote=A, ...).
    """

    local_install: str
    """The install whose DB this row lives in."""

    remote_install: str
    """The install we're trusting to invoke the listed
    capabilities AGAINST us."""

    capabilities: frozenset[FederationCapability]
    jwks_url: str
    r"""Where to fetch the remote's signing keys. Each install
    publishes JWKS at \`/.well-known/astrolift-federation/jwks\`."""

    granted_at_unix: int
    expires_at_unix: int
    """Trust expires unless renewed. Default 1 year per spec
    (operator override possible up to 5 years)."""

    is_revoked: bool = False
    revoke_reason: str = ""

    def __post_init__(self) -> None:
        # Validation. Cross-install rule: local != remote (no
        # self-trust).
        local = normalize_install_id(value=self.local_install)
        remote = normalize_install_id(value=self.remote_install)
        if local == remote:
            raise FederationError(f"install {local!r} cannot federate with itself")
        if not self.capabilities:
            raise FederationError("trust must grant at least one capability")
        if not self.jwks_url:
            raise FederationError("jwks_url is required")
        if self.expires_at_unix <= self.granted_at_unix:
            raise FederationError("expires_at_unix must be > granted_at_unix")


# ---- trust validity ------------------------------------------------


DEFAULT_TRUST_TTL_SECONDS = 365 * 86400
"""1 year default. Operator can override to 5 years (security
ceiling — longer = exposed cross-install attack surface)."""

MAX_TRUST_TTL_SECONDS = 5 * 365 * 86400


def validate_trust_ttl(*, seconds: int) -> int:
    if seconds <= 0:
        raise FederationError(f"trust TTL must be positive, got {seconds}s")
    if seconds > MAX_TRUST_TTL_SECONDS:
        raise FederationError(f"trust TTL {seconds}s exceeds maximum {MAX_TRUST_TTL_SECONDS}s (5 years)")
    return seconds


def is_trust_active(*, trust: FederationTrust, now_unix: int) -> bool:
    """Active = not revoked AND within TTL window. Verifier
    consults this BEFORE checking JWT signatures (revocation
    is authoritative; expired JWKS is also rejected even if
    the signature would otherwise verify)."""
    if trust.is_revoked:
        return False
    if now_unix >= trust.expires_at_unix:
        return False
    return True


def grants_capability(
    *,
    trust: FederationTrust,
    capability: FederationCapability,
    now_unix: int,
) -> bool:
    """Combined check. Used by federated-request handlers as
    the single auth gate after JWT verification."""
    if not is_trust_active(trust=trust, now_unix=now_unix):
        return False
    return capability in trust.capabilities


# ---- DNS forwarding note ------------------------------------------
#
# Install DNS zones can change (operator renames, migrates to a new
# domain, splits orgs). The fingerprint stored on each side IS the
# stable identity — DNS resolves you to a JWKS endpoint, and as
# long as the served pubkey's fingerprint matches what the trust
# row pinned, you're talking to the same install. DNS forwards
# (HTTP 308 / DNS CNAME) flow through transparently because the
# verifier compares fingerprints, not hostnames.
#
# Caveat: if an install rotates its keypair, the fingerprint
# changes and trust rebuild is required (out of band). This is
# the right tradeoff — silent rebinding to a different keypair
# would be a downgrade attack vector.
#
# Out of scope for this module: auto-shipping / replicating data
# between installs. That's a separate follow-up; this module
# only owns the trust + per-RPC envelope.


# ---- handshake (symmetric, mutual challenge-response) -------------
#
# Trust establishment is a "double 3-way handshake" — TCP-style
# 3WH run TWICE so each side actively authenticates the other.
# JWKS-fetch alone is too weak: anyone could host a JWKS at a
# domain. Mutual challenge-response proves the peer holds the
# private key matching the JWKS the OTHER side fetched.
#
# Each install ends up with the peer's pubkey + a confirmed-trust
# marker persisted privately in its OWN DB. No third party is
# trusted; no shared state.
#
# Round 1 (A initiates):
#   1. A → B: { iss: A, nonce_a, jwks_url_a, requested_caps }
#   2. B → A: { iss: B, nonce_b, sig_b(nonce_a), jwks_url_b,
#               granted_caps_a→b, fingerprint_b }
#   3. A: verifies sig_b using fetched-from-jwks_url_b key.
#      Operator out-of-band confirms fingerprint_b.
#      A → B: { sig_a(nonce_b), fingerprint_a }
#   4. B: verifies sig_a using fetched-from-jwks_url_a key.
#      Operator out-of-band confirms fingerprint_a.
#      Both sides persist a half-trust row.
#
# Round 2 (B initiates symmetric reverse for capabilities B wants
# A to grant). Same shape, opposite direction. Optional — if B
# only needs to consume and not be consumed, Round 2 grants no
# capabilities.
#
# Result: mutual auth + per-direction capability grants. Either
# side can unilaterally revoke at any time.


class HandshakeStep(StrEnum):
    """Steps the workflow walks through. Steps are PER-DIRECTION
    (run twice for full mutual establishment)."""

    INIT = "init"
    """Local operator entered remote install identifier +
    desired capabilities."""

    SENT_HELLO = "sent_hello"
    """Local sent {iss, nonce_local, jwks_url_local, caps} to
    remote. Awaiting remote's signed response."""

    RECEIVED_CHALLENGE_RESPONSE = "received_challenge_response"
    """Remote returned {nonce_remote, sig_remote(nonce_local),
    granted_caps, fingerprint_remote}. Local fetched
    jwks_url_remote and verified sig_remote.
    Local UI displays fingerprint_remote for operator
    out-of-band confirmation."""

    OPERATOR_CONFIRMED_REMOTE_FINGERPRINT = "operator_confirmed_remote_fingerprint"
    """Operator clicked 'fingerprint matches'. Local now signs
    remote's nonce."""

    SENT_FINAL_ACK = "sent_final_ack"
    """Local → remote: {sig_local(nonce_remote), fingerprint_local}.
    Awaiting remote operator to confirm fingerprint_local
    out-of-band."""

    REMOTE_OPERATOR_CONFIRMED = "remote_operator_confirmed"
    """Remote signaled (via callback / poll) that their operator
    confirmed local's fingerprint. Both sides have completed
    challenge-response verification."""

    PERSISTED_TRUST = "persisted_trust"
    """Local DB row written: stores remote's pubkey, granted
    capabilities, fingerprint. Trust is now live for THIS
    direction; the symmetric reverse is a separate handshake."""


HANDSHAKE_ORDER = (
    HandshakeStep.INIT,
    HandshakeStep.SENT_HELLO,
    HandshakeStep.RECEIVED_CHALLENGE_RESPONSE,
    HandshakeStep.OPERATOR_CONFIRMED_REMOTE_FINGERPRINT,
    HandshakeStep.SENT_FINAL_ACK,
    HandshakeStep.REMOTE_OPERATOR_CONFIRMED,
    HandshakeStep.PERSISTED_TRUST,
)


def next_handshake_step(
    *,
    current: HandshakeStep,
) -> HandshakeStep | None:
    """Returns the next step or None when at the end."""
    try:
        idx = HANDSHAKE_ORDER.index(current)
    except ValueError as exc:
        raise FederationError(f"unknown handshake step {current!r}") from exc
    if idx + 1 >= len(HANDSHAKE_ORDER):
        return None
    return HANDSHAKE_ORDER[idx + 1]


# ---- handshake message shapes -------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class HelloMessage:
    """Round-1 step 1: A → B."""

    iss: str
    """Initiating install."""

    nonce: str
    """32-byte random; B signs this to prove it holds the
    private key matching jwks_url's pubkey."""

    jwks_url: str
    """Where B should fetch A's pubkeys."""

    requested_capabilities: tuple[FederationCapability, ...]
    """What A wants B to grant. B's operator decides whether
    to grant; the response carries granted_capabilities (may
    be a subset)."""

    def __post_init__(self) -> None:
        if not self.iss:
            raise FederationError("HelloMessage requires iss")
        if not self.nonce:
            raise FederationError("HelloMessage requires nonce")
        if len(self.nonce) < 32:
            raise FederationError(
                f"HelloMessage nonce too short ({len(self.nonce)}); minimum 32 chars (256 bits of randomness)"
            )
        if not self.jwks_url:
            raise FederationError("HelloMessage requires jwks_url")
        if not self.requested_capabilities:
            raise FederationError(
                "HelloMessage requires at least one requested capability — a no-cap handshake is meaningless"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class ChallengeResponse:
    """Round-1 step 2: B → A."""

    iss: str
    """Responding install (B)."""

    nonce: str
    """B's random; A signs this in step 3."""

    signed_peer_nonce: str
    """Hex(sig_B(hello.nonce)). Receiver re-fetches B's JWKS
    and verifies."""

    jwks_url: str
    granted_capabilities: tuple[FederationCapability, ...]
    """Subset of requested capabilities B's operator approved.
    Empty = B refused — handshake aborts."""

    fingerprint: str
    """Hex SHA-256 of B's primary pubkey. UI shows this for
    A's operator to OOB-confirm."""

    def __post_init__(self) -> None:
        if not self.iss or not self.nonce or not self.signed_peer_nonce:
            raise FederationError("ChallengeResponse requires iss, nonce, signed_peer_nonce")
        if not self.fingerprint:
            raise FederationError("ChallengeResponse requires fingerprint for OOB confirm")


@dataclasses.dataclass(frozen=True, slots=True)
class FinalAck:
    """Round-1 step 3: A → B."""

    iss: str
    signed_peer_nonce: str
    """Hex(sig_A(challenge.nonce))."""

    fingerprint: str
    """A's pubkey fingerprint for B's operator OOB-confirm."""


def verify_handshake_pair(
    *,
    local_hello: HelloMessage,
    remote_response: ChallengeResponse,
) -> None:
    """Validate the round-1 nonce-tying invariants. Signature
    verification is delegated to the activity layer (which holds
    the keys); this module checks the structural binding."""
    if local_hello.iss == remote_response.iss:
        raise FederationError("remote response iss must differ from local hello iss")
    if not remote_response.granted_capabilities:
        raise FederationError("remote granted no capabilities — operator declined; handshake aborts")
    bad = set(remote_response.granted_capabilities) - set(local_hello.requested_capabilities)
    if bad:
        raise FederationError(f"remote granted capabilities not in request: {sorted(c.value for c in bad)}")


def jwks_url_for(*, install_id: str) -> str:
    """Spec: each install publishes JWKS at the federation
    well-known path. Locked path so trust establishment
    doesn't need a discovery doc."""
    install = normalize_install_id(value=install_id)
    return f"https://{install}/.well-known/astrolift-federation/jwks"


# ---- cross-install JWT claims --------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class FederatedRequestClaims:
    """JWT claims for cross-install requests. Different from
    #67's BundleJwtClaims — that's for app-migration bundles;
    these are for ongoing federated API calls (discovery
    queries, transfer initiations).
    """

    iss: str
    """Issuer install — who's making the request."""

    aud: str
    """Audience install — who should verify. Doubly-bound:
    BOTH iss and aud must match the trust row, so a stolen
    token can't be replayed against any third install."""

    sub: str
    """Subject — typically the local user identifier on iss-side.
    Audit trail; not used for authz."""

    iat: int
    nbf: int
    exp: int
    jti: str
    """Single-use marker for state-changing requests; readonly
    queries don't dedupe by jti."""

    capability: FederationCapability
    """Locked vocabulary — refused if not in the granting
    install's trust capabilities."""


JWT_TTL_SECONDS = 5 * 60
"""Cross-install JWTs are short-lived. 5 min covers a single
RPC + retries; longer would expand the replay window."""

CLOCK_SKEW_SECONDS = 30


def build_federated_claims(
    *,
    iss: str,
    aud: str,
    sub: str,
    issued_at_unix: int,
    jti: str,
    capability: FederationCapability,
    ttl_seconds: int = JWT_TTL_SECONDS,
) -> FederatedRequestClaims:
    iss_norm = normalize_install_id(value=iss)
    aud_norm = normalize_install_id(value=aud)
    if iss_norm == aud_norm:
        raise FederationError("iss and aud must differ — federated request to self is meaningless")
    if not jti:
        raise FederationError("jti is required")
    if not sub:
        raise FederationError("sub is required (audit trail)")
    if ttl_seconds <= 0 or ttl_seconds > JWT_TTL_SECONDS:
        raise FederationError(f"ttl_seconds must be in (0, {JWT_TTL_SECONDS}]")
    return FederatedRequestClaims(
        iss=iss_norm,
        aud=aud_norm,
        sub=sub,
        iat=issued_at_unix,
        nbf=issued_at_unix,
        exp=issued_at_unix + ttl_seconds,
        jti=jti,
        capability=capability,
    )


# ---- verification --------------------------------------------------


class VerifyResult(StrEnum):
    OK = "ok"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"
    AUDIENCE_MISMATCH = "audience_mismatch"
    NO_TRUST = "no_trust"
    """No trust row matching (iss → aud) on the local side."""

    TRUST_REVOKED = "trust_revoked"
    TRUST_EXPIRED = "trust_expired"
    INSUFFICIENT_CAPABILITY = "insufficient_capability"
    REPLAY = "replay"
    """jti already consumed (state-changing requests only)."""


@dataclasses.dataclass(frozen=True, slots=True)
class VerifyDecision:
    accepted: bool
    result: VerifyResult
    reason: str


def verify_federated_request(
    *,
    claims: FederatedRequestClaims,
    expected_local_install: str,
    trust_lookup,
    seen_jti_lookup,
    now_unix: int,
    require_jti_uniqueness: bool = True,
) -> VerifyDecision:
    """Verify a cross-install JWT.

    ``trust_lookup``: callable(local, remote) → FederationTrust | None.
    ``seen_jti_lookup``: callable(jti) → bool. Only consulted
    when require_jti_uniqueness=True (state-changing flows).
    Read-only queries skip jti dedup since replays are harmless.
    """
    local = normalize_install_id(value=expected_local_install)

    if claims.aud != local:
        return VerifyDecision(
            accepted=False,
            result=VerifyResult.AUDIENCE_MISMATCH,
            reason=(f"claim audience {claims.aud!r} != this install {local!r}"),
        )

    if now_unix < claims.nbf - CLOCK_SKEW_SECONDS:
        return VerifyDecision(
            accepted=False,
            result=VerifyResult.NOT_YET_VALID,
            reason=f"nbf={claims.nbf} > now={now_unix}",
        )

    if now_unix > claims.exp + CLOCK_SKEW_SECONDS:
        return VerifyDecision(
            accepted=False,
            result=VerifyResult.EXPIRED,
            reason=f"exp={claims.exp} < now={now_unix}",
        )

    trust = trust_lookup(local, claims.iss)
    if trust is None:
        return VerifyDecision(
            accepted=False,
            result=VerifyResult.NO_TRUST,
            reason=(f"no trust granted from {local!r} to {claims.iss!r}"),
        )

    if trust.is_revoked:
        return VerifyDecision(
            accepted=False,
            result=VerifyResult.TRUST_REVOKED,
            reason=(f"trust revoked: {trust.revoke_reason or 'no reason'}"),
        )

    if now_unix >= trust.expires_at_unix:
        return VerifyDecision(
            accepted=False,
            result=VerifyResult.TRUST_EXPIRED,
            reason=(f"trust expired at {trust.expires_at_unix}; renew"),
        )

    if claims.capability not in trust.capabilities:
        return VerifyDecision(
            accepted=False,
            result=VerifyResult.INSUFFICIENT_CAPABILITY,
            reason=(
                f"trust grants {sorted(c.value for c in trust.capabilities)}; "
                f"request needs {claims.capability.value}"
            ),
        )

    if require_jti_uniqueness and seen_jti_lookup(claims.jti):
        return VerifyDecision(
            accepted=False,
            result=VerifyResult.REPLAY,
            reason=f"jti {claims.jti!r} already consumed",
        )

    return VerifyDecision(
        accepted=True,
        result=VerifyResult.OK,
        reason="federated request accepted",
    )


# ---- shareable kinds + capability mapping --------------------------


class ShareableKind(StrEnum):
    """What can flow over federation. Each kind requires a
    matching capability on the granting side; the receiving side
    enforces."""

    APP_INVENTORY = "app_inventory"
    """Read-only discovery query result. No values, just
    slugs + health + env names."""

    APP_MANIFEST = "app_manifest"
    """The astrolift.toml + parsed metadata. Source of truth
    is the TOML; metadata is a re-derivable cache. Receiver
    re-parses the TOML and validates against the shipped
    metadata for byte-equality."""

    ENV_CONFIG = "env_config"
    """Per-environment scalar config (env vars). Often
    co-shipped with manifest; split out so an env-only update
    doesn't ship the whole manifest."""

    SECRET_VALUE = "secret_value"
    """Actual secret bytes. High-risk; explicit capability
    required."""


# Authoritative kind → capability mapping. Federation handler
# rejects any push/pull where the trust row doesn't grant the
# required capability for the kind.
_KIND_CAPABILITY = {
    ShareableKind.APP_INVENTORY: FederationCapability.APP_DISCOVERY_READONLY,
    ShareableKind.APP_MANIFEST: FederationCapability.APP_TRANSFER,
    ShareableKind.ENV_CONFIG: FederationCapability.ENV_CONFIG_SHARE,
    ShareableKind.SECRET_VALUE: FederationCapability.SECRET_SHARING,
}


def capability_for_kind(*, kind: ShareableKind) -> FederationCapability:
    if kind not in _KIND_CAPABILITY:
        raise FederationError(f"unknown shareable kind {kind!r}")
    return _KIND_CAPABILITY[kind]


# ---- per-peer sync settings ---------------------------------------


class FederationSyncMode(StrEnum):
    """Operator-controlled per (peer install, kind) tuple."""

    DISABLED = "disabled"
    """No automatic federation; manual push/pull only when
    operator triggers explicitly. Default — least-surprise."""

    MANUAL = "manual"
    """Same as DISABLED for auto-flow but the UI surfaces
    'send to peer' buttons. Distinguishes 'we set up the
    federation but use it manually' from 'never use this peer
    for this kind'."""

    AUTO_PUSH = "auto_push"
    """When local resource changes, push to the peer. HA-style:
    primary install pushes manifest changes to standby."""

    AUTO_PULL = "auto_pull"
    """Periodically pull from the peer. Geo-distributed
    follower style: replica installs poll the primary."""


@dataclasses.dataclass(frozen=True, slots=True)
class FederationSyncSetting:
    """Per-peer, per-kind sync policy. Operator owns this row."""

    local_install: str
    peer_install: str
    kind: ShareableKind
    mode: FederationSyncMode
    pull_interval_seconds: int = 0
    """Only meaningful for AUTO_PULL. 0 = use platform default
    (60s); operator can dial down to 30s or up to 1h."""

    def __post_init__(self) -> None:
        local = normalize_install_id(value=self.local_install)
        peer = normalize_install_id(value=self.peer_install)
        if local == peer:
            raise FederationError("sync setting requires distinct local + peer")
        if self.mode == FederationSyncMode.AUTO_PULL:
            interval = self.pull_interval_seconds or DEFAULT_PULL_INTERVAL_SECONDS
            if interval < MIN_PULL_INTERVAL_SECONDS:
                raise FederationError(f"pull interval {interval}s below minimum {MIN_PULL_INTERVAL_SECONDS}s")
            if interval > MAX_PULL_INTERVAL_SECONDS:
                raise FederationError(
                    f"pull interval {interval}s exceeds maximum {MAX_PULL_INTERVAL_SECONDS}s"
                )
        elif self.pull_interval_seconds:
            raise FederationError(
                f"pull_interval_seconds set but mode is {self.mode.value} — only AUTO_PULL uses it"
            )


DEFAULT_PULL_INTERVAL_SECONDS = 60
MIN_PULL_INTERVAL_SECONDS = 30
MAX_PULL_INTERVAL_SECONDS = 3600


def is_auto_sync_enabled(*, setting: FederationSyncSetting) -> bool:
    """Convenience for the activity layer to skip the auto-sync
    workflow when operator hasn't opted in."""
    return setting.mode in (
        FederationSyncMode.AUTO_PUSH,
        FederationSyncMode.AUTO_PULL,
    )


def authorize_share(
    *,
    trust: FederationTrust,
    kind: ShareableKind,
    now_unix: int,
) -> None:
    """Single auth gate for any federated push/pull. Composes:
    1. Trust still active (not revoked / expired)
    2. Trust grants the capability matching the kind
    Raises FederationError on refusal so caller surfaces 403.
    """
    required = capability_for_kind(kind=kind)
    if not is_trust_active(trust=trust, now_unix=now_unix):
        raise FederationError(
            f"trust between {trust.local_install} and "
            f"{trust.remote_install} is not active "
            f"(revoked={trust.is_revoked}, expired={now_unix >= trust.expires_at_unix})"
        )
    if required not in trust.capabilities:
        raise FederationError(
            f"sharing kind {kind.value} requires capability "
            f"{required.value}; trust grants only "
            f"{sorted(c.value for c in trust.capabilities)}"
        )


# ---- discovery query (read-only inventory) -------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class DiscoveryAppEntry:
    """One row in the federated discovery response. Carefully
    minimal — secrets, manifest contents, deployment logs are
    NEVER exposed via federation."""

    app_slug: str
    last_deployed_at_unix: int | None
    health_badge: str
    """'healthy' / 'degraded' / 'down' / 'unknown'."""

    environments: tuple[str, ...]
    """Env slugs, no per-env config."""


def filter_discovery_response(
    *,
    apps: Sequence[DiscoveryAppEntry],
    requestor_install: str,
    granting_install: str,
) -> tuple[DiscoveryAppEntry, ...]:
    """Defensive filter at response time. The trust check
    already gated the request, but this enforces the never-leak
    invariant: the response shape is bounded to DiscoveryAppEntry
    fields, no extras can sneak through.

    Currently a pass-through (the dataclass type IS the filter),
    but lives here so that future privacy rules (per-app opt-out
    of cross-install discovery, env-name redaction in regulated
    industries) have a single point of insertion.
    """
    # Defensive: refuse self-discovery
    if normalize_install_id(value=requestor_install) == normalize_install_id(value=granting_install):
        raise FederationError("discovery response from self — should never happen")
    return tuple(apps)
