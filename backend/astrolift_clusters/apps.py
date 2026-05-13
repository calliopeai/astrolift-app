from django.apps import AppConfig


class AstroliftClustersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "astrolift_clusters"
    label = "astrolift_clusters"
    verbose_name = "Astrolift Clusters"

    def ready(self) -> None:
        # In-process provider plugin discovery. Walks astrolift.providers
        # entry points, adapts each to the local PluginManifest, and seats
        # them in the plugins registry so driver lookups in the dispatch
        # path resolve without an extra bootstrap step. DB-side row seeding
        # lives in the bootstrap_provider_plugins management command since
        # ready() runs before migrations on a fresh database.
        from astrolift_clusters.plugin_loader import discover_and_register

        discover_and_register()
