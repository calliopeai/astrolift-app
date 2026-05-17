# Two 0015 leaves landed from sibling agents (each merging the same
# 0014 split). Both are no-op merges with identical dependencies.
# This 0016 collapses them so the migration graph has a single leaf
# again and future migrations have a stable chain point.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0015_merge_20260517_2229"),
        ("astrolift_registry", "0015_merge_duplicate_0014_merges"),
    ]

    operations = []
