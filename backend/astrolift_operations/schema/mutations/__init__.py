"""OperationsMutation — assembled from per-feature mixin modules."""

from __future__ import annotations

# Bound at the package level so tests can patch
# ``astrolift_operations.schema.mutations.urllib.request.urlopen`` (the
# webhook/notification test resolvers call ``urllib.request.urlopen``, which
# resolves to this shared module object regardless of the mixin submodule).
import urllib.request  # noqa: F401

import strawberry

from astrolift_operations.schema.mutations.alert_subscriptions import AlertSubscriptionMutations
from astrolift_operations.schema.mutations.alerts import AlertMutations
from astrolift_operations.schema.mutations.bulk_ops import BulkOpsMutations
from astrolift_operations.schema.mutations.exports import ExportMutations
from astrolift_operations.schema.mutations.helpers import (  # noqa: F401
    _BULK_APP_CAP,
    _WEBHOOK_TEST_TIMEOUT_SECONDS,
    _build_app_log_export_url,
    _build_audit_export_url,
    _caller_org_id,
    _deliver_test_webhook,
    _materialize_app_log_lines,
    log,
)
from astrolift_operations.schema.mutations.notifications import NotificationMutations

# Re-exported for the public import surface (tests / cross-app importers).
from astrolift_operations.schema.mutations.types import (  # noqa: F401
    AcknowledgeAlertEventInput,
    BulkAppResultItem,
    BulkOperationResult,
    BulkPushSecretsInput,
    BulkResyncManifestInput,
    BulkRollingRestartInput,
    ClearAlertSubscriptionInput,
    CreateAlertRuleInput,
    CreateWebhookSubscriptionInput,
    DeleteAlertRuleInput,
    DeleteWebhookSubscriptionInput,
    ExportAppLogsInput,
    ExportAuditEventsInput,
    MarkNotificationReadInput,
    MuteAlertRuleInput,
    RegisterMobileDeviceInput,
    RevokeMobileDeviceInput,
    RotateOutboundWebhookSecretInput,
    SetAlertSubscriptionInput,
    SetNotificationPreferenceInput,
    TestNotificationInput,
    TestWebhookInput,
    UnmuteAlertRuleInput,
    UpdateAlertRuleInput,
    UpdateWebhookSubscriptionInput,
    WebhookSecretReveal,
    _AlertRuleDeletedPayload,
    _MarkAllReadPayload,
    _RevokeMobileDevicePayload,
    _SoftDeletePayload,
)
from astrolift_operations.schema.mutations.webhooks import WebhookMutations


@strawberry.type
class OperationsMutation(
    WebhookMutations,
    NotificationMutations,
    AlertMutations,
    ExportMutations,
    AlertSubscriptionMutations,
    BulkOpsMutations,
):
    """Root mutation type — inherits fields from each domain mixin."""
