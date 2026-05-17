# Two sibling agents landed migrations on the same numbered slot:
# 0010_app_log_export (consumed by 0011) and 0010_notification_driver_sdk
# (a dangling leaf). Result — two leaf nodes in the graph, Django
# refuses to create a fresh test DB or run further migrations.
#
# This 0012 ties the dangling 0010 onto the 0011 line so the graph has
# a single leaf again. No-op operations on both ends.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_operations", "0010_notification_driver_sdk"),
        ("astrolift_operations", "0011_device_registration_and_preference"),
    ]

    operations = []
