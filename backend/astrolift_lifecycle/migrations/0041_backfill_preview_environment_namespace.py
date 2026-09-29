# Existing previews render into their own namespace (#1922).
#
# Every preview recorded a namespace in ``PreviewEnvironment.namespace`` and
# ``BuildPreviewWorkflow`` created it, but the preview's deploys went to the
# app namespace, over the objects of the app's other environments on that
# cluster. This records the preview's namespace on its ``AppEnvironment`` so
# the next deploy lands there instead.
#
# Only a live environment with a PreviewEnvironment row changes. Every other
# environment keeps a blank ``k8s_namespace`` and renders where it always has.
#
# A preview namespace that an app, the platform or another preview already
# holds is not reused: ``<org>-<app>-pr-<n>`` is also org ``<org>``'s app
# ``<app>-pr-<n>``, and a manual preview of branch ``pr-3`` computed PR #3's
# name. Such an environment gets the name with a short hash suffix, and its
# preview rows take the same value, so teardown deletes that namespace and not
# the one the name used to share. First by primary key keeps the plain name.
#
# Soft-deleted apps and previews count as holders: their namespace can still
# exist on a cluster until teardown finishes. Every read goes through
# ``_base_manager``, which never filters them, whichever registry runs this.

import hashlib

from django.db import migrations

_RESERVED_EXACT = frozenset({"default", "kube-system", "kube-public", "kube-node-lease", "astrolift-system"})
_RESERVED_PREFIXES = ("kube-", "astrolift-agents-", "astrolift-system-")


def _reserved(namespace):
    return namespace in _RESERVED_EXACT or namespace.startswith(_RESERVED_PREFIXES)


def _app_namespaces(RegisteredApp):
    from _sdk.k8s_naming import app_namespace

    out = set()
    for recorded, app_slug, org_slug in RegisteredApp._base_manager.values_list(
        "k8s_namespace", "slug", "organization__slug"
    ):
        recorded = (recorded or "").strip()
        if recorded:
            out.add(recorded)
        elif app_slug:
            try:
                out.add(app_namespace(organization_slug=org_slug or "", app_slug=app_slug))
            except ValueError:
                continue
    return out


def _suffixed(namespace, seed, taken):
    from _sdk.k8s_naming import dns_label

    for attempt in range(100):
        token = hashlib.sha256(f"{seed}:{attempt}".encode()).hexdigest()[:8]
        candidate = dns_label(namespace, token)
        if candidate not in taken and not _reserved(candidate):
            return candidate
    raise RuntimeError(f"no free namespace derived from {namespace!r}")


def record_preview_namespaces(apps, schema_editor):
    AppEnvironment = apps.get_model("astrolift_lifecycle", "AppEnvironment")
    PreviewEnvironment = apps.get_model("astrolift_lifecycle", "PreviewEnvironment")
    RegisteredApp = apps.get_model("astrolift_registry", "RegisteredApp")

    previews_by_env = {}
    for row in PreviewEnvironment._base_manager.order_by("pk").only(
        "pk", "app_environment_id", "namespace", "deleted_at"
    ):
        previews_by_env.setdefault(row.app_environment_id, []).append(row)
    if not previews_by_env:
        return

    envs = list(
        AppEnvironment._base_manager.filter(
            pk__in=list(previews_by_env), deleted_at__isnull=True, k8s_namespace=""
        )
        .select_related("registered_app")
        .order_by("pk")
    )
    placing = {env.pk for env in envs}
    taken = _app_namespaces(RegisteredApp)
    taken |= set(
        AppEnvironment._base_manager.exclude(k8s_namespace="").values_list("k8s_namespace", flat=True)
    )
    # A preview whose environment is not placed here keeps its name on the
    # cluster until its teardown runs, so nothing placed here may take it.
    for env_id, rows in previews_by_env.items():
        if env_id not in placing:
            taken |= {(row.namespace or "").strip() for row in rows if (row.namespace or "").strip()}

    for env in envs:
        rows = previews_by_env[env.pk]
        live = [row for row in rows if row.deleted_at is None]
        source = (live or rows)[-1]
        namespace = (source.namespace or "").strip()
        if not namespace:
            continue
        if namespace in taken or _reserved(namespace):
            fresh = _suffixed(namespace, f"{env.registered_app.guid}:{env.name}", taken)
            PreviewEnvironment._base_manager.filter(
                app_environment_id=env.pk, namespace=source.namespace
            ).update(namespace=fresh)
            namespace = fresh
        env.k8s_namespace = namespace
        env.save(update_fields=["k8s_namespace"])
        taken.add(namespace)


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_lifecycle", "0040_appenvironment_k8s_namespace"),
        ("astrolift_registry", "0041_populate_hostname_claims"),
    ]

    operations = [
        migrations.RunPython(record_preview_namespaces, migrations.RunPython.noop),
    ]
