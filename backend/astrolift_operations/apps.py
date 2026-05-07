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
        from core.events import register_event_writer
        from core.mutations import register_audit_writer

        register_event_writer(write_event_envelope)
        register_audit_writer(write_audit_entry)
