# Merge migration: reconcile the two 0016_merge_* leaves created when #497
# (optimistic concurrency) and #519 (dispatcher dedup) both shipped no-op
# merge migrations on top of the same 0015 leaves at the same time.
#
# Both 0016_* nodes are themselves no-op merges; this node merges them so
# Django can resolve a single leaf on astrolift_registry.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0016_merge_0015_leaves"),
        ("astrolift_registry", "0016_merge_519_duplicate_0015_leaves"),
    ]

    operations = []
