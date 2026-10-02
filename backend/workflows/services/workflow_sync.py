"""Sync WorkflowDefinitions + WorkflowStages from an in-repo YAML DSL (#866).

Fetches ``.astrolift/workflows.yaml`` from a tenant's source repo,
parses it via ``dsl_parser``, and upserts the resulting
``WorkflowDefinition`` and ``WorkflowStage`` rows.

Idempotent — re-syncing the same file produces the same DB state.
Existing stages are replaced atomically on update: the old stage set
is deleted and the new one is written inside the same transaction so
a partial write can never leave the definition in a half-updated state.

Entry point::

    result = sync_workflows_from_repo(registered_app, connection, ref)
    # result == {"created": 1, "updated": 0, "errors": []}
"""

from __future__ import annotations

import logging

from django.db import transaction

from workflows.services.dsl_parser import DslParseError, parse_workflows_dsl, validate_workflow_dsl

logger = logging.getLogger(__name__)

DSL_PATH = ".astrolift/workflows.yaml"


def sync_workflows_from_repo(
    registered_app,
    connection,
    ref: str,
) -> dict:
    """Fetch, parse, and upsert WorkflowDefinitions from the repo's DSL file.

    Parameters
    ----------
    registered_app:
        ``astrolift_registry.models.RegisteredApp`` instance. Used to
        read ``source_repo`` and scope the upserted definitions.
    connection:
        ``astrolift_scm.models.SourceConnection`` instance with an
        active credential. Passed to ``astrolift_scm.providers.fetch_file``.
    ref:
        Git ref (branch name, tag, or commit SHA) to read the file from.
        For push events this is the after-push HEAD ref.

    Returns
    -------
    dict with keys ``created`` (int), ``updated`` (int), ``errors`` (list[str]).
    The ``errors`` list contains per-workflow error strings; a non-empty
    list does not raise — callers log it and carry on so one bad workflow
    entry never blocks the others.
    """
    from astrolift_scm.providers import ProviderError, fetch_file

    result: dict = {"created": 0, "updated": 0, "errors": []}

    # Fetch the DSL file from the remote repo.
    try:
        content = fetch_file(
            connection,
            repo_full_name=registered_app.source_repo,
            path=DSL_PATH,
            ref=ref,
        )
    except ProviderError as exc:
        logger.warning(
            "workflow_sync: could not fetch %s from %s@%s: %s",
            DSL_PATH,
            registered_app.source_repo,
            ref,
            exc.message,
        )
        result["errors"].append(f"fetch failed: {exc.message}")
        return result

    if content is None:
        # File does not exist in the repo — nothing to sync.
        logger.debug(
            "workflow_sync: %s not found in %s@%s, skipping",
            DSL_PATH,
            registered_app.source_repo,
            ref,
        )
        return result

    # Parse the YAML.
    try:
        definitions = parse_workflows_dsl(content)
    except DslParseError as exc:
        logger.warning(
            "workflow_sync: DSL parse error in %s@%s: %s",
            registered_app.source_repo,
            ref,
            exc,
        )
        result["errors"].append(f"parse error: {exc}")
        return result

    # Upsert each definition.
    for defn in definitions:
        slug = defn["slug"]
        validation_errors = validate_workflow_dsl(defn)
        if validation_errors:
            msg = f"workflow {slug!r}: " + "; ".join(validation_errors)
            logger.warning("workflow_sync: validation failed: %s", msg)
            result["errors"].append(msg)
            continue

        try:
            created = _upsert_definition(defn)
        except Exception as exc:
            msg = f"workflow {slug!r}: upsert failed: {exc}"
            logger.exception("workflow_sync: %s", msg)
            result["errors"].append(msg)
            continue

        if created:
            result["created"] += 1
        else:
            result["updated"] += 1

    return result


def _upsert_definition(defn: dict) -> bool:
    """Upsert one WorkflowDefinition + its stages inside a transaction.

    Returns True when a new row was created, False on update.
    """
    from workflows.models import WorkflowDefinition, WorkflowStage

    slug = defn["slug"]

    with transaction.atomic():
        # model_label defaults to an empty string in the DSL; the unique
        # constraint is on (slug, model_label) so an empty model_label is
        # valid for DSL-authored definitions that aren't tied to a
        # specific Django model.
        obj, created = WorkflowDefinition.objects.get_or_create(
            slug=slug,
            model_label=defn["model_label"],
            defaults={
                "name": defn["name"],
                "pattern_kind": defn["pattern_kind"],
                "states": defn["states"],
                "transitions": defn["transitions"],
                "is_enabled": defn["is_enabled"],
            },
        )

        if not created:
            # Update mutable fields; leave slug + model_label (the lookup
            # key) untouched.
            obj.name = defn["name"]
            obj.pattern_kind = defn["pattern_kind"]
            obj.states = defn["states"]
            obj.transitions = defn["transitions"]
            obj.is_enabled = defn["is_enabled"]
            obj.save(
                update_fields=[
                    "name",
                    "pattern_kind",
                    "states",
                    "transitions",
                    "is_enabled",
                    "updated_at",
                ]
            )

        # Replace stages atomically: delete the old set, insert the new
        # set. Using delete+create is simpler than a three-way diff and
        # correct because stage ``order`` values shift whenever the author
        # reorders the list.
        WorkflowStage.objects.filter(definition=obj).delete()
        for stage in defn["stages"]:
            # BaseCoreModel.save() calls slugify(self.slug); pass an
            # explicit slug so multiple stages don't collide on the
            # unique constraint (which treats non-NULL values as unique).
            stage_slug = f"{obj.slug or obj.pk}-stage-{stage['order']}"
            stage_name = f"{obj.name or obj.slug} stage {stage['order']}"
            WorkflowStage.objects.create(
                slug=stage_slug,
                name=stage_name,
                definition=obj,
                order=stage["order"],
                kind=stage["kind"],
                skill_refs=stage["skill_refs"],
                fan_out_count=stage["fan_out_count"],
                on_failure=stage["on_failure"],
                max_attempts=stage["max_attempts"],
                timeout_seconds=stage["timeout_seconds"],
            )

    return created
