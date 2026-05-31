"""Add agent_variant and agent_runtime fields to Workload (#40).

Both fields are blank CharField with TextChoices so the migration is
additive and non-breaking for existing rows — the empty string default
means "no variant / no runtime preset".
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_registry", "0024_workload_kind_function"),
    ]

    operations = [
        migrations.AddField(
            model_name="workload",
            name="agent_variant",
            field=models.CharField(
                blank=True,
                choices=[
                    ("headless", "Headless"),
                    ("terminal_novnc", "Terminal Novnc"),
                    ("terminal_novnc_browser", "Terminal Novnc Browser"),
                ],
                default="",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="workload",
            name="agent_runtime",
            field=models.CharField(
                blank=True,
                choices=[
                    ("claude", "Claude"),
                    ("gemini", "Gemini"),
                    ("codex", "Codex"),
                    ("universal", "Universal"),
                ],
                default="",
                max_length=32,
            ),
        ),
    ]
