from django.apps import AppConfig


class AstroliftOperationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "astrolift_operations"
    label = "astrolift_operations"
    verbose_name = "Astrolift Operations"

    def ready(self) -> None:
        # Wire Event.emit() to write to the persistent Event model
        # now that it exists.
        from astrolift_operations.event_writer import write_event_envelope
        from core.events import register_event_writer

        register_event_writer(write_event_envelope)
