import enum

import strawberry

from core.models import NotificationStatus, Profile


@strawberry.enum(name="AstroliftObservabilityPanelReason")
class ObservabilityPanelReason(enum.Enum):
    """Why an observability panel is empty (#1111).

    Every observability-panel resolver used to collapse four distinct
    states into a bare ``[]`` / ``None``, so the UI could only hedge
    ("either not configured OR the plugin doesn't implement it OR no
    data"). This discriminator labels the branch the resolver actually
    took so the FE can render one honest, actionable message.

    * ``OK`` — the panel has data.
    * ``NOT_CONFIGURED`` — the cluster/endpoint/capability the panel
      needs isn't wired (no Prometheus endpoint, no cluster, no public
      hostname, no log aggregator). Operator action fixes it.
    * ``NOT_SUPPORTED_BY_PROVIDER`` — the cluster's provider plugin
      driver raised ``NotImplementedError`` /
      ``UnsupportedOperationError`` (a capability that only some clouds
      implement). Only ever surfaced on the cloud that lacks it.
    * ``NO_DATA_YET`` — the backing query ran and succeeded but returned
      nothing (zero rows). The benign empty state.
    * ``ERROR`` — an unexpected failure while loading (logged
      server-side). The FE offers a retry.
    """

    OK = "ok"
    NOT_CONFIGURED = "not_configured"
    NOT_SUPPORTED_BY_PROVIDER = "not_supported_by_provider"
    NO_DATA_YET = "no_data_yet"
    ERROR = "error"


CoreProfileDocumentOptionChoices = strawberry.enum(
    Profile.DocumentOptions,
    name="CoreProfileDocumentOptionChoices",
    description="Profile document option choices.",
)

EnumNotificationStatus = strawberry.enum(
    NotificationStatus,
    name="EnumNotificationStatus",
    description="Notification status values.",
)
