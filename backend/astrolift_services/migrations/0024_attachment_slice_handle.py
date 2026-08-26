"""Record the slice a preview attachment owns (#1670).

Preview teardown had nothing to drop: `provision_slice` returned a
`slice_handle` and the attachment kept only the env overrides, so the
carved database outlived the preview as an orphan in the parent instance,
invisible until someone read the instance's database list.

Blank default rather than null: every existing attachment reaches its
parent directly, and "" says that without a caller having to distinguish
None from empty on a lookup that happens on every teardown.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0023_manifest_managed_services"),
    ]

    operations = [
        migrations.AddField(
            model_name="managedserviceattachment",
            name="slice_handle",
            field=models.CharField(
                blank=True,
                default="",
                max_length=512,
                help_text=(
                    "Driver handle for the isolated slice carved for this consumer, "
                    "when the attachment is to a shared service (#1578). Blank means "
                    "the consumer reaches the parent instance directly. Teardown drops "
                    "the slice this names; without it the slice is an orphan database "
                    "in the parent that nobody sees (#1670)."
                ),
            ),
        ),
    ]
