from django.apps import AppConfig


class AstroliftOperationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "astrolift_operations"
    label = "astrolift_operations"
    verbose_name = "Astrolift Operations"

    def ready(self) -> None:
        # Wire Event.emit() and mutation_audit() to write to the
        # persistent Event / AuditEvent models now that they exist.
        from astrolift_operations.audit_writer import write_audit_entry
        from astrolift_operations.event_writer import write_event_envelope
        from astrolift_operations.notification_dispatch import dispatch_event
        from core.events import register_event_subscriber, register_event_writer
        from core.mutations import register_audit_writer

        register_event_writer(write_event_envelope)
        register_audit_writer(write_audit_entry)
        # Push fan-out runs as a subscriber so it observes the
        # envelope *after* the persistent Event row write — see
        # ``notification_dispatch.dispatch_event`` for the policy.
        register_event_subscriber(dispatch_event)

        # Outbound webhooks (#1598). The subscriber mechanism has existed
        # since events landed and nothing registered a webhook against it, so
        # `WebhookSubscription` rows were creatable, listable, testable --
        # and never delivered to.
        #
        # Registered alongside notification dispatch rather than inside it:
        # they are different egress channels with different failure
        # semantics, and a webhook endpoint being down must not affect
        # whether an in-app notification was written.
        from astrolift_operations.webhook_fanout import dispatch as dispatch_webhooks

        register_event_subscriber(dispatch_webhooks)

        # Flipping ZENTINELLE_GATEWAY_ENABLED deploys or removes the gateway in
        # every registered cluster (#1905), not only in clusters touched later.
        from constance.signals import config_updated

        config_updated.connect(_reconcile_gateways_on_flip, dispatch_uid="zentinelle-gateway-flip")


def _reconcile_gateways_on_flip(sender, key, old_value, new_value, **kwargs) -> None:
    if key != "ZENTINELLE_GATEWAY_ENABLED" or bool(old_value) == bool(new_value):
        return
    import logging
    import threading

    from django.db import close_old_connections, transaction

    log = logging.getLogger(__name__)

    def run() -> None:
        from astrolift_operations.zentinelle_connect import reconcile_gateways

        close_old_connections()
        try:
            for row in reconcile_gateways():
                log.info("zentinelle: gateway reconcile %s: %s", row.cluster_slug, row.outcome)
        finally:
            close_old_connections()

    # Off the request: each cluster is a Kubernetes round trip. The admin
    # change commits first, so the reconcile reads the new value.
    transaction.on_commit(
        lambda: threading.Thread(target=run, name="zentinelle-gateway-reconcile", daemon=True).start()
    )
