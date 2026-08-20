"""
Universal cloud-resource tagging schema (#30 backbone).

Every cloud resource the platform provisions — compute instances,
S3 buckets, RDS DBs, Pub/Sub topics, NAT gateways, IAM roles,
disks, the lot — MUST carry this tag set. Native tags / labels
are how every cloud's billing API breaks down spend; without
universal tagging, per-app and per-binding cost attribution is
impossible.

Three layers:

* **Canonical tag schema** (``CloudTagSet``) — the platform's
  universal vocabulary. ``astrolift.io/*`` namespace so it can't
  collide with operator-defined tags.
* **Per-cloud serialization** (``to_aws`` / ``to_gcp`` / ``to_azure``)
  — each cloud has its own rules (AWS allows mixed case + most
  symbols; GCP requires lowercase + hyphen + 63-char limit; Azure
  is case-insensitive but forbids several characters, including ``/``).
  Caller doesn't worry about it.
* **Required-tag enforcement** (``MIN_REQUIRED_KEYS``) — the set a
  freshly-provisioned resource is meant to carry.

.. warning::

   Nothing in this module has a production caller. The only callers
   of ``assert_required_tags`` are this package's own tests, and no
   driver builds a ``CloudTagSet`` (#1500).

   The header above describes an intended design, not shipped
   behaviour, and the previous version of this text claimed a
   providers-repo CI test enforced ``MIN_REQUIRED_KEYS``. No such
   test exists here. That claim is the reason the gap read as
   covered for as long as it did.

   What actually happens: platform tags *are* applied on provision,
   by per-cloud builders that read ``ProvisionSpec`` scalar fields
   and hand-roll their own dicts — ``aws/managed/_base.tags_for``,
   ``azure/managed/tags.arm_tags_for``, and per-driver GCP labels.
   Their spellings differ from each other and from this module's, so
   ``assert_required_tags`` would reject every resource the platform
   has ever created; ``astrolift.io/install`` in particular is
   written by no builder on any cloud.

   ``providers/_sdk/managed_service_tags.py`` is the narrow fix that
   did ship (#1419), scoped to the one key cost attribution groups
   on. Read it before treating anything here as authoritative.

Pairs with:
  * #11 ``ProvisionSpec.tags`` — *not* the platform envelope. It is
    the operator custom-tag channel (AWS namespaces it under
    ``astrolift.io/extra/``, Azure passes it as ``custom_tags``), and
    ``build_provision_spec`` never populates it, so ``cost_center``
    and operator-defined tags currently reach no resource (#1500).
  * #30 ``CostSnapshot.managed_service_binding_id`` — the cost
    collector queries the cloud billing API by tag, joins on
    ``astrolift.io/binding`` to write the snapshot.
  * #30 LLM usage — ``agent_id`` lives in the tag set so when a
    platform-run agent provisions cloud resources, those resources
    bill back to the agent run.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping

from _sdk.azure_tags import serialize_azure_arm_tags

# Reserved namespace. Operators set custom tags on resources via
# their own keys; the platform owns ``astrolift.io/*``.
PLATFORM_NAMESPACE = "astrolift.io"


# Canonical key set. Adding a key requires updating MIN_REQUIRED_KEYS
# below if it's mandatory.
TAG_INSTALL = f"{PLATFORM_NAMESPACE}/install"
TAG_ORG = f"{PLATFORM_NAMESPACE}/org"
TAG_TEAM = f"{PLATFORM_NAMESPACE}/team"
TAG_PROJECT = f"{PLATFORM_NAMESPACE}/project"
TAG_APP = f"{PLATFORM_NAMESPACE}/app"
TAG_ENV = f"{PLATFORM_NAMESPACE}/env"
TAG_BINDING = f"{PLATFORM_NAMESPACE}/binding"
TAG_MANAGED_SVC_ID = f"{PLATFORM_NAMESPACE}/managed_service_id"
TAG_AGENT_ID = f"{PLATFORM_NAMESPACE}/agent_id"
TAG_AGENT_RUN_ID = f"{PLATFORM_NAMESPACE}/agent_run_id"
TAG_COST_CENTER = f"{PLATFORM_NAMESPACE}/cost_center"
TAG_DEPLOYMENT_ID = f"{PLATFORM_NAMESPACE}/deployment_id"


# These MUST be present on every resource the platform provisions.
# CI test in astrolift-providers asserts this on smoke-deployed
# fixtures — catches plugin authors who forgot to thread tags
# through.
MIN_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        TAG_INSTALL,
        TAG_ORG,
        TAG_APP,
        TAG_ENV,
    }
)


@dataclasses.dataclass(frozen=True, slots=True)
class CloudTagSet:
    """The canonical tag set the platform asserts on every
    provisioned resource. Optional fields default to empty; the
    serializer drops empty values so AWS/GCP/Azure never see
    ``key=""`` (some cloud billing reports treat empty values as
    a separate bucket from missing).
    """

    install_slug: str
    org_slug: str
    app_slug: str
    env_slug: str
    team_slug: str = ""
    project_slug: str = ""
    binding_name: str = ""
    managed_service_id: str = ""
    agent_id: str = ""
    """Set when the resource is provisioned by a platform-run
    agent (an LLM agent that creates cloud resources mid-run).
    Cost attributes back to the agent run."""

    agent_run_id: str = ""
    cost_center: str = ""
    """Operator-defined billing code (e.g. 'engineering-r&d').
    Org config supplies a default; manifest can override per app."""

    deployment_id: str = ""
    extra: Mapping[str, str] = dataclasses.field(default_factory=dict)
    """Operator-defined tags. Validated against the platform
    namespace so an operator can't accidentally clobber
    ``astrolift.io/*``."""

    def __post_init__(self) -> None:
        # Required fields must be non-empty. A missing org slug on
        # an actual provisioned resource means cost can't be
        # attributed to any tenant — fail loudly.
        for label, value in (
            ("install_slug", self.install_slug),
            ("org_slug", self.org_slug),
            ("app_slug", self.app_slug),
            ("env_slug", self.env_slug),
        ):
            if not value:
                raise ValueError(
                    f"{label} is required for cloud tag set (platform-mandated for cost attribution)"
                )
        # Reserved-namespace check on extra keys
        for key in self.extra:
            if key.startswith(PLATFORM_NAMESPACE):
                raise ValueError(
                    f"extra key {key!r} clashes with the platform namespace {PLATFORM_NAMESPACE!r}"
                )

    def as_canonical_dict(self) -> dict[str, str]:
        """Cloud-agnostic dict with every set tag. Empty fields
        are dropped (don't push ``key=""`` to the cloud)."""
        out: dict[str, str] = {
            TAG_INSTALL: self.install_slug,
            TAG_ORG: self.org_slug,
            TAG_APP: self.app_slug,
            TAG_ENV: self.env_slug,
        }
        for tag, value in (
            (TAG_TEAM, self.team_slug),
            (TAG_PROJECT, self.project_slug),
            (TAG_BINDING, self.binding_name),
            (TAG_MANAGED_SVC_ID, self.managed_service_id),
            (TAG_AGENT_ID, self.agent_id),
            (TAG_AGENT_RUN_ID, self.agent_run_id),
            (TAG_COST_CENTER, self.cost_center),
            (TAG_DEPLOYMENT_ID, self.deployment_id),
        ):
            if value:
                out[tag] = value
        for k, v in self.extra.items():
            if v:
                out[k] = v
        return out


# ---- per-cloud serialization ---------------------------------------


# AWS tags: Key/Value pairs. Key max 128 chars, Value max 256.
# Allowed: letters, digits, spaces, ``+ - = . _ : / @``.
# Note '/' is allowed, so ``astrolift.io/app`` is fine.
_AWS_VALUE_MAX = 256
_AWS_INVALID = re.compile(r"[^A-Za-z0-9 +\-=._:/@]")


# GCP labels: keys lowercase, [a-z0-9-_], 63-char limit on each.
# Values lowercase, [a-z0-9-_], 63-char limit. Slashes / dots
# are NOT allowed — must transform.
_GCP_VALUE_MAX = 63
_GCP_INVALID_VALUE = re.compile(r"[^a-z0-9_-]")


def _truncate(value: str, max_len: int) -> str:
    return value[:max_len]


def to_aws(tags: CloudTagSet) -> dict[str, str]:
    """AWS-format tag dict. Keys are kept as ``astrolift.io/app``
    style (slash allowed). Values truncated to 256."""
    raw = tags.as_canonical_dict()
    out: dict[str, str] = {}
    for k, v in raw.items():
        # Replace any disallowed chars with underscore (rare for
        # platform-controlled values, but operator extras might
        # contain them).
        clean = _AWS_INVALID.sub("_", v)
        out[k] = _truncate(clean, _AWS_VALUE_MAX)
    return out


def to_gcp(tags: CloudTagSet) -> dict[str, str]:
    """GCP-format label dict. Keys + values lowercased; slashes
    + dots replaced with underscore. ``astrolift.io/app`` →
    ``astrolift_io_app``."""
    raw = tags.as_canonical_dict()
    out: dict[str, str] = {}
    for k, v in raw.items():
        gcp_key = _gcp_normalize(k)
        gcp_value = _gcp_normalize(v)
        out[gcp_key] = _truncate(gcp_value, _GCP_VALUE_MAX)
    return out


def _gcp_normalize(s: str) -> str:
    """Lowercase + replace any disallowed char with underscore.
    GCP requires keys to start with a letter; we prefix if needed
    (but our keys all start with 'astrolift_io_*' so this is rare)."""
    lower = s.lower()
    cleaned = _GCP_INVALID_VALUE.sub("_", lower)
    if not cleaned or not cleaned[0].isalpha():
        cleaned = "k_" + cleaned
    return cleaned


def to_azure(tags: CloudTagSet) -> dict[str, str]:
    """Azure-format tag dict with valid, collision-safe ARM names.

    ARM forbids ``<>%&\\?/`` in tag names, so canonical
    ``astrolift.io/app`` becomes ``astrolift-app``. Values that exceed Azure's
    limit fail closed instead of being silently truncated.
    """
    raw = tags.as_canonical_dict()
    platform = {key: value for key, value in raw.items() if key.startswith(f"{PLATFORM_NAMESPACE}/")}
    return serialize_azure_arm_tags(platform, custom_tags=tags.extra)


# ---- enforcement helpers -------------------------------------------


class TagEnforcementError(ValueError):
    """A resource is missing required platform tags."""


def assert_required_tags(tags: Mapping[str, str]) -> None:
    """Used by the providers-repo CI test and by post-provision
    smoke checks: every resource freshly provisioned must carry
    at minimum the MIN_REQUIRED_KEYS set with non-empty values."""
    missing = [k for k in MIN_REQUIRED_KEYS if not tags.get(k)]
    if missing:
        raise TagEnforcementError(
            f"resource missing required platform tags: {sorted(missing)}; "
            "every plugin must apply CloudTagSet at provision time"
        )


# ---- agent extension --------------------------------------------------


def for_agent_run(
    *,
    base: CloudTagSet,
    agent_id: str,
    agent_run_id: str,
) -> CloudTagSet:
    """Stamp an existing tag set with agent_id + agent_run_id.
    Used when a platform-run agent provisions cloud resources
    mid-run; the resulting tags route the bill back to the agent
    run for cost attribution."""
    if not agent_id or not agent_run_id:
        raise ValueError("agent_id and agent_run_id are both required")
    return dataclasses.replace(
        base,
        agent_id=agent_id,
        agent_run_id=agent_run_id,
    )
