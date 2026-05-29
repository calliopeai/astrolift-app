from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Add UserPreferences model — per-user settings outside auth.User.

    Starts with the ``timezone`` field (IANA name, #775). The table is
    lazily created on first preference write; no backfill needed.
    """

    dependencies = [
        ("astrolift_identity", "0016_astroliftsession_login_method"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserPreferences",
            fields=[
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="preferences",
                        serialize=False,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "timezone",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text=(
                            "IANA timezone name (e.g. 'America/New_York'). "
                            "Empty means the UI falls back to the browser-detected zone."
                        ),
                        max_length=64,
                    ),
                ),
            ],
            options={
                "verbose_name": "user preferences",
                "verbose_name_plural": "user preferences",
                "app_label": "astrolift_identity",
            },
        ),
    ]
