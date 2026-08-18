"""Which cloud resources belong to a certification campaign (spec 43 §3.1).

An orphan scan is only as good as the question it asks the cloud. Ask by a key
nothing writes and every scan comes back clean, which is worse than not
scanning at all: it certifies a teardown path that leaks.

There is no single handle that reaches everything a campaign creates, so this
module declares two and matches on either.

*The campaign tag.* A manifest declares ``[campaign] tag``. That is an operator
tag, and each cloud's write path spells operator tags differently:
``astrolift.io/extra/<key>`` on AWS, ``astrolift-extra-<key>`` on GCP, and a
digest-suffixed ``astrolift-extra-<key>-<digest>`` on Azure. The Azure spelling
is computed here through the same serializer the drivers write with, rather
than transcribed, because a scanner reading a spelling nobody writes is the
exact failure this module exists to prevent.

The campaign tag does not reach a resource yet. ``_provision_sync`` in
``astrolift_workflows/activities/managed_service_lifecycle.py`` builds its
``ProvisionSpec`` without ``tags=``, so a manifest's operator tags stop at the
database, and ``gcp/managed/object_store_gcs.py`` and
``gcp/managed/queue_pubsub.py`` drop ``spec.tags`` even when it is populated.
Until both are fixed the campaign tag is declared and read but never written.

*The app slug.* Every managed-service driver stamps the app slug on what it
creates, and IAM surfaces carry no usable tag at all -- a GCP service account
has no labels, and an Azure user-assigned identity is created with only
``astrolift-managed-by``. Their sole handle is a deterministic name. So every
campaign app is named ``<campaign-slug>-<cell>``: that puts the campaign's
identity inside a value which is already written on every resource and inside
the only handle IAM offers.

The app-key ledgers below are deliberately explicit, like ``LEGACY_KEYS`` in
``_sdk/managed_service_tags.py``. Drivers spell the app key four ways on GCP
and two on Azure; a scanner that knows one of them silently misses the rest.
``tests/_cert/test_campaign.py`` scans the driver tree and fails when a
spelling appears that these ledgers do not read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from _sdk.azure_tags import serialize_azure_arm_tags

if TYPE_CHECKING:
    from collections.abc import Mapping

CLOUDS: tuple[str, ...] = ("aws", "gcp", "azure")

CAMPAIGN_TAG_KEY = "campaign"
"""The operator tag key a manifest's ``[campaign]`` block declares. Not in the
``astrolift.io`` namespace, which ``core.cloud_tags.CloudTagSet`` reserves for
the platform and refuses to let an operator tag occupy."""


def _azure_campaign_key() -> str:
    """The ARM tag name Azure's serializer produces for the campaign tag.

    Computed, never transcribed: ``_custom_key`` appends a digest of the key,
    so a hand-written constant would drift the first time that scheme changes.
    """
    serialized = serialize_azure_arm_tags({}, custom_tags={CAMPAIGN_TAG_KEY: "probe"})
    return next(iter(serialized))


#: Where each cloud's write path puts the campaign tag. AWS is
#: ``aws/managed/_base.py::tags_for``; GCP is the ``astrolift-extra-<key>``
#: convention every GCP ``_tags_for`` uses; Azure comes from the serializer.
CAMPAIGN_KEYS: dict[str, tuple[str, ...]] = {
    "aws": (f"astrolift.io/extra/{CAMPAIGN_TAG_KEY}",),
    "gcp": (f"astrolift-extra-{CAMPAIGN_TAG_KEY}",),
    "azure": (_azure_campaign_key(),),
}

#: Every spelling of the app key a driver writes, per cloud. Read-only: nothing
#: here is a key this repo should start writing, and dropping one makes the
#: scanner blind to whichever driver still writes it.
APP_KEYS: dict[str, tuple[str, ...]] = {
    # aws/managed/_base.py::tags_for is the single AWS write path.
    "aws": ("astrolift.io/app",),
    # Four spellings: `astrolift-app` (cloudsql, memorystore, bigtable, ...),
    # `astrolift-io-app` (gcs, filestore, eventarc, cloud functions),
    # `astrolift_app` (bigquery) and `astrolift_io_app` (cloud operations,
    # workflows).
    "gcp": ("astrolift-app", "astrolift-io-app", "astrolift_app", "astrolift_io_app"),
    # `astrolift-app` is what `azure/managed/tags.py::arm_tags_for` emits after
    # ARM serialization; `astrolift_io_app` is blob-container metadata, which
    # is a different surface with its own naming rules.
    "azure": ("astrolift-app", "astrolift_io_app"),
}

_SLUG = re.compile(r"^[a-z][a-z0-9]{2,}$")


class UnknownCloud(KeyError):
    """A cloud with no declared campaign or app key spellings."""


@dataclass(frozen=True)
class Campaign:
    """One certification campaign, identified by a slug.

    The slug carries no hyphen on purpose. Every app in the collection is named
    ``<slug>-<cell>``, so a hyphen-free slug keeps the boundary between the
    campaign and the cell unambiguous when reading a resource name back off a
    console.
    """

    slug: str

    def __post_init__(self) -> None:
        if not _SLUG.match(self.slug):
            raise ValueError(
                f"campaign slug {self.slug!r} must be lowercase alphanumeric, start with a "
                f"letter, and contain no hyphen; it is embedded in app names, GCP label "
                f"values and Azure resource names, which is the narrowest of those rules"
            )

    @property
    def app_prefix(self) -> str:
        return f"{self.slug}-"

    def owns_app(self, app_slug: str) -> bool:
        return app_slug == self.slug or app_slug.startswith(self.app_prefix)

    def owns_name(self, name: str) -> bool:
        """Whether a resource *name* belongs to this campaign.

        Anywhere in the name rather than at the start: drivers prepend their own
        namespace (``astrolift-<org>-<app>-<env>-<hint>``), so anchoring at the
        front would miss nearly all of them.

        Bounded by non-alphanumerics on both sides, so campaign ``cert2026q3``
        does not claim ``cert2026q3b``'s resources. Two campaigns whose slugs
        differ only by a suffix are otherwise indistinguishable on a surface
        with no tags, and a scan that deletes another campaign's database is
        worse than one that misses an orphan.

        The boundary has a known cost: a name that cannot contain a separator
        (an Azure storage account is 3-24 characters of lowercase alphanumerics)
        never matches. Those are install-level resources the campaign does not
        create. If that changes, the answer is a tag on them, not a looser name
        rule.
        """
        return re.search(rf"(?<![a-z0-9]){re.escape(self.slug)}(?![a-z0-9])", name) is not None


def campaign_keys(cloud: str) -> tuple[str, ...]:
    try:
        return CAMPAIGN_KEYS[cloud]
    except KeyError:
        raise UnknownCloud(
            f"no campaign tag key declared for cloud {cloud!r}; declare how that "
            f"cloud serializes an operator tag before scanning it, or the scan "
            f"reports clean because it asked the wrong question"
        ) from None


def app_keys(cloud: str) -> tuple[str, ...]:
    try:
        return APP_KEYS[cloud]
    except KeyError:
        raise UnknownCloud(
            f"no app tag keys declared for cloud {cloud!r}; declare every spelling its drivers write before scanning it"
        ) from None


def match(
    campaign: Campaign,
    cloud: str,
    *,
    tags: Mapping[str, str] | None = None,
    name: str = "",
) -> str | None:
    """Why this resource belongs to ``campaign``, or ``None``.

    Returns the handle that matched rather than a bare boolean so a scan report
    can say which one fired. When only the name matches, the resource was found
    by the weaker handle, and that is worth seeing in the output: it means the
    tag never landed.
    """
    tags = tags or {}
    for key in campaign_keys(cloud):
        if tags.get(key) == campaign.slug:
            return f"tag {key}={campaign.slug}"
    for key in app_keys(cloud):
        value = tags.get(key, "")
        if value and campaign.owns_app(value):
            return f"tag {key}={value}"
    if name and campaign.owns_name(name):
        return f"name {name}"
    return None
