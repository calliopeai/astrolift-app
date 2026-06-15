import logging
import os

from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from health_check.plugins import plugin_dir

        from config.health_graphql import GraphQLHealthCheck

        plugin_dir.register(GraphQLHealthCheck)

        from django.conf import settings

        from config.telemetry import setup as telemetry_setup

        telemetry_setup(
            service_name="astrolift",
            service_version=settings.VERSION,
            environment=settings.CONFIGURATION,
            is_local=settings.IS_LOCAL,
        )

        post_migrate.connect(self.register_objects, sender=self)

        # Register core file exporters
        self._register_file_exporters()

        # Register core processors
        self._register_processors()

        # Wire the production WebSocket exec backend (#423). The
        # default backend in core.schema.exec_ws is a stub that
        # emits a 'not wired' message; replacing it here means the
        # console terminal opens a real kubernetes-client exec
        # session as soon as Django boots.
        from core.cluster_exec import K8sExecBackend
        from core.schema.exec_ws import set_exec_backend

        set_exec_backend(K8sExecBackend())

        # Wire the production WebSocket VNC backend (#877). Same shape
        # as the exec backend above — the default in core.schema.vnc_ws
        # is a stub that EOFs immediately; replacing it here means the
        # agent-task VNC viewer opens a real kubernetes port-forward
        # to the pod's raw RFB port (5900) as soon as Django boots.
        from core.cluster_vnc import K8sVncBackend
        from core.schema.vnc_ws import set_vnc_backend

        set_vnc_backend(K8sVncBackend())

    @staticmethod
    def _register_file_exporters():
        """Register core file exporters."""
        from core.utils.file_export_registry import register_file_exporter
        from core.utils.file_processor.file_export import ChatHistory

        register_file_exporter("rocket-channel-history", ChatHistory)

    @staticmethod
    def _register_processors():
        """Register core data processors."""
        from core.models.process import EntityType
        from core.utils.file_processor.permissions_processor import PermissionsProcessor
        from core.utils.file_processor.site_label_processor import SiteLabelProcessor
        from core.utils.processor_registry import register_processor

        register_processor(EntityType.SITE_LABEL, SiteLabelProcessor)
        register_processor(EntityType.PERMISSIONS, PermissionsProcessor)

    @classmethod
    def register_objects(cls, sender, **kwargs):
        from django.contrib.auth.models import User

        try:
            if not User.objects.filter(username="admin").exists():
                User.objects.create_superuser(
                    "admin", "admin@astrolift.dev", os.environ.get("DJANGO_SUPERUSER_PASSWORD", "changeme")
                )
        except Exception:
            logging.info("Unable to create admin user.")

        from .emails import Emails

        Emails.register(sender)

        from .models.interval import Intervals

        Intervals.register(sender)

        from .models.authorization.actions import SharedFilePermissions

        SharedFilePermissions.register(sender)
