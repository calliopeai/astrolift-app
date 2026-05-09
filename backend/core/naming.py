"""
Naming convention rules + validation (#170 part 1, spec 01 §9).

Single source of truth for the platform's naming conventions across
K8s objects, cloud resources, and platform entities. Tests in CI
import this module directly to assert real strings (DNS names,
bucket names, namespace names) match the rules — no separate YAML
config to drift from the code.

Conventions are intentionally tight: lowercase, dash-separated,
length-bounded. Cloud providers all impose stricter rules of their
own (S3 / DNS / IAM each have their own length and character sets);
this module enforces the *platform's* policy, which is the
intersection of those rules so any name we generate is valid
everywhere.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from typing import Final

# ---- character class --------------------------------------------------
#
# All names: lowercase letters + digits + dashes. No underscores
# (DNS, IAM, S3 all reject), no dots (would conflict with DNS
# zone semantics for app names), no uppercase (S3 rejects in
# US-East-1; some IAM principals fold to lowercase).

_BASE_CHARS = "[a-z0-9-]"
_ALPHA_START = "[a-z]"
_ALPHANUM_END = "[a-z0-9]"


@dataclasses.dataclass(frozen=True, slots=True)
class NamingRule:
    """One named convention. Matches against ``regex``; if not, the
    error message includes ``description`` plus the user's input so
    they know *which* convention failed."""

    name: str
    description: str
    pattern: re.Pattern[str]
    max_length: int

    def validate(self, value: str) -> None:
        """Raise :class:`NamingViolation` if ``value`` doesn't match."""
        if not isinstance(value, str):
            raise NamingViolation(rule=self, value=value, reason="not a string")
        if not value:
            raise NamingViolation(rule=self, value=value, reason="empty")
        if len(value) > self.max_length:
            raise NamingViolation(
                rule=self,
                value=value,
                reason=f"too long ({len(value)} > {self.max_length})",
            )
        if not self.pattern.fullmatch(value):
            raise NamingViolation(
                rule=self, value=value, reason="does not match pattern"
            )


class NamingViolation(ValueError):
    """A name didn't satisfy a convention. Carries the rule + value
    so CI test output points the operator at the exact rule."""

    def __init__(self, *, rule: NamingRule, value: object, reason: str):
        self.rule_name = rule.name
        self.value = value
        self.reason = reason
        super().__init__(
            f"{rule.name}: {reason} for value {value!r}; "
            f"pattern {rule.pattern.pattern!r}, max_length={rule.max_length}; "
            f"{rule.description}"
        )


def _rule(*, name: str, description: str, regex: str, max_length: int) -> NamingRule:
    return NamingRule(
        name=name,
        description=description,
        pattern=re.compile(regex),
        max_length=max_length,
    )


# ---- the rule book ---------------------------------------------------


# Platform entities. Slugs are user-typed; we want them to map
# cleanly into namespaces, DNS labels, and IAM principals.
ORG_SLUG: Final = _rule(
    name="org_slug",
    description="lowercase alphanumeric + dashes; first char alpha; no leading/trailing dash",
    regex=rf"{_ALPHA_START}({_BASE_CHARS}*{_ALPHANUM_END})?",
    max_length=40,
)
APP_NAME: Final = _rule(
    name="app_name",
    description="lowercase alphanumeric + dashes; first char alpha; max 32 chars (k8s label limit budget)",
    regex=rf"{_ALPHA_START}({_BASE_CHARS}*{_ALPHANUM_END})?",
    max_length=32,
)
ENV_NAME: Final = _rule(
    name="env_name",
    description="short lowercase identifier (prod, sbx, preview-foo)",
    regex=rf"{_ALPHA_START}({_BASE_CHARS}*{_ALPHANUM_END})?",
    max_length=24,
)
PROJECT_SLUG: Final = _rule(
    name="project_slug",
    description="lowercase alphanumeric + dashes; first char alpha",
    regex=rf"{_ALPHA_START}({_BASE_CHARS}*{_ALPHANUM_END})?",
    max_length=40,
)


# K8s objects. Most apply RFC 1123 label rules (DNS-compatible).
K8S_NAMESPACE: Final = _rule(
    name="k8s_namespace",
    description="RFC 1123 label: lowercase alphanumeric + dashes, max 63 chars, alpha-start",
    regex=rf"{_ALPHA_START}({_BASE_CHARS}{{0,61}}{_ALPHANUM_END})?",
    max_length=63,
)
K8S_RESOURCE: Final = _rule(
    name="k8s_resource",
    description="K8s object name: RFC 1123 subdomain, max 253 chars, allows dots between labels",
    regex=r"[a-z]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z]([a-z0-9-]{0,61}[a-z0-9])?)*",
    max_length=253,
)


# Cloud resources. The intersection of S3 / GCS / Azure Blob / DNS
# rules so anything that passes is valid everywhere.
DNS_LABEL: Final = _rule(
    name="dns_label",
    description="single DNS label: lowercase alphanumeric + dashes, no leading/trailing dash, max 63",
    regex=rf"{_ALPHA_START}({_BASE_CHARS}{{0,61}}{_ALPHANUM_END})?",
    max_length=63,
)
S3_BUCKET: Final = _rule(
    name="s3_bucket",
    description="S3 bucket: 3-63 chars, lowercase alphanumeric + dashes, alpha-start, alphanumeric-end",
    regex=rf"{_ALPHA_START}({_BASE_CHARS}{{1,61}}{_ALPHANUM_END})",
    max_length=63,
)
IAM_ROLE: Final = _rule(
    name="iam_role",
    description="IAM role name: 1-64 chars, alphanumeric + plus/equals/comma/period/at/dash/underscore — we tighten to lowercase + dashes",
    regex=rf"{_ALPHA_START}({_BASE_CHARS}*{_ALPHANUM_END})?",
    max_length=64,
)


ALL_RULES: tuple[NamingRule, ...] = (
    ORG_SLUG, APP_NAME, ENV_NAME, PROJECT_SLUG,
    K8S_NAMESPACE, K8S_RESOURCE,
    DNS_LABEL, S3_BUCKET, IAM_ROLE,
)


# ---- composite + bulk validators ------------------------------------


def validate_app_namespace(*, org_slug: str, app_name: str) -> str:
    """Compose a k8s namespace from org+app and validate.

    Convention is ``<org_slug>-<app_name>`` so cross-org name
    collisions are impossible. Returns the composed namespace.
    """
    ORG_SLUG.validate(org_slug)
    APP_NAME.validate(app_name)
    composed = f"{org_slug}-{app_name}"
    K8S_NAMESPACE.validate(composed)
    return composed


def validate_app_subdomain(*, app_name: str, env_name: str, base_zone: str) -> str:
    """Compose a deploy subdomain from app + env + zone and validate.

    Convention is ``<app>-<env>.<base_zone>`` for previews,
    ``<app>.<base_zone>`` for the default env. Returns the FQDN.
    """
    APP_NAME.validate(app_name)
    ENV_NAME.validate(env_name)
    label = app_name if env_name == "prod" else f"{app_name}-{env_name}"
    DNS_LABEL.validate(label)
    return f"{label}.{base_zone}"


def validate_all(values: Sequence[tuple[NamingRule, str]]) -> list[NamingViolation]:
    """Run multiple validations, collect every violation. Used by CI
    so a contributor sees ALL their naming bugs in one test run, not
    one-at-a-time. Empty list = all valid."""
    violations: list[NamingViolation] = []
    for rule, value in values:
        try:
            rule.validate(value)
        except NamingViolation as v:
            violations.append(v)
    return violations
