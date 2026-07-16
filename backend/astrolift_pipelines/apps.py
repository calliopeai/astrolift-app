from django.apps import AppConfig


class AstroliftPipelinesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "astrolift_pipelines"
    label = "astrolift_pipelines"
    verbose_name = "Astrolift Pipelines"

    def ready(self) -> None:
        from django.db.models.signals import post_save

        from astrolift_pipelines.models import Trigger
        from astrolift_pipelines.signals import on_trigger_save

        post_save.connect(on_trigger_save, sender=Trigger, dispatch_uid="pipelines_trigger_schedule_sync")
