"""Private configuration executor; it never activates the public native pipeline."""

import hashlib
import json
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from threading import Event

from django.db import connection, transaction
from django.db.models import Q
from gcp.gke_app_apply import ExecutionBinding, GKEAppApply, compile_app_plan
from gcp.gke_app_runtime_handoff import observe_handoff_runtime
from gcp.gke_identity_observation import GKEObservationContext, KSASubject
from gcp.gke_identity_preparation import GKEIdentityPreparation
from gcp.identity_owned import NativeGCPIdentity
from gcp.identity_source import NativeIdentitySource

from astrolift_lifecycle.deployment_execution_checkpoint import deployment_execution_checkpoint
from astrolift_lifecycle.deployment_execution_receipt import admitted_deployment_execution
from astrolift_lifecycle.deployment_identity_origin import deployment_app_identity_authority
from astrolift_lifecycle.models import Deployment
from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.render import render_manifests
from astrolift_services.gcp_app_identity_plan import (
    NativeEndpointObserver,
    endpoint_app_checkpoint,
    produce_endpoint_app_plan,
    refresh_endpoint_app_sources,
)
from astrolift_services.gcp_gke_app_apply_journal import AcceptedApply, SourceFence, app_apply_mutex
from astrolift_services.gcp_gke_preparation_journal import (
    PreparationOperation,
    PreparationStage,
    PreparationStore,
    PreparationTarget,
    preparation_journal_mutex,
)
from astrolift_services.gcp_identity_acknowledgement import retain_acknowledgement, retained_acknowledgements
from astrolift_services.gcp_identity_source import SourceOperation, bootstrap_original_identity
from astrolift_services.gcp_workload_identity_journal import JournalStore, OperationIdentity, journal_mutex
from astrolift_services.models import (
    AppSecretBundleRef,
    GCPGKEAppApplyJournal,
    GCPGKEAppApplyOperation,
    GCPGKEPreparationJournal,
    GCPWorkloadIdentityJournal,
    ManagedService,
    ManagedServiceAttachment,
    ManagedServiceBinding,
    ManagedServiceVolumeBinding,
)
from astrolift_services.secret_literals import literal_secrets_for_environment
from astrolift_workflows.activities.app_lifecycle import _bindings_secret_name
from astrolift_workflows.gcp_identity_inputs import LogicalSubject
from astrolift_workflows.native_identity_inputs import DeploymentAuthorityContext, NativeConfigurationReceipt
from core.app_deploy import render_resources_for_deployment
from core.gcp_prepared_identity_render import (
    prepared_identity_for_deployment,
    validate_prepared_workload_shapes,
)


class NativeConfigurationError(ValueError):
    """Only fixed reasons cross the registered activity boundary."""


# Rollout is observed inside the configuration activity, bound to the exact
# completed app journal handoff. Each observation keeps its own read budget.
_RUNTIME_OBSERVATIONS = 10
_RUNTIME_POLL_SECONDS = 15.0
_RUNTIME_RETRYABLE = frozenset({"RUNTIME_READ_BOUND_EXCEEDED"})


@dataclass(frozen=True)
class NativeConfigurationPorts:
    """Server dependencies, not workflow inputs or caller-provided authority."""

    source: object = NativeIdentitySource
    endpoints: object = NativeEndpointObserver
    preparation: object = GKEIdentityPreparation
    iam: object = NativeGCPIdentity
    apply: object = GKEAppApply


def _deployment(input):
    return Deployment.objects.select_related(
        "registered_app__organization",
        "registered_app__team",
        "registered_app__project",
        "app_environment__tenant_cluster__provider_plugin",
    ).get(
        pk=input.deployment_id,
        registered_app_id=input.registered_app_id,
        app_environment_id=input.app_environment_id,
    )


def _render_source(input):
    deployment = _deployment(input)
    app, env = deployment.registered_app, deployment.app_environment
    return hashlib.sha256(
        json.dumps(
            (
                app.manifest_raw,
                app.registry_repo_uri,
                deployment.image_tag,
                deployment.image_digest,
                deployment.secret_snapshot,
                env.deploy_config,
                env.managed_domain_id,
            ),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _selected_endpoint_bindings(deployment, snapshot):
    """Literal delivery follows selected consumers; IAM retains the full union.

    A project Endpoint can be desired by a sibling alias while absent or disabled
    in the selected environment. Its union membership never makes it a consumer.
    The caller's fresh complete snapshot fences all these rows before/after use.
    """
    app, env = deployment.registered_app, deployment.app_environment
    endpoint_ids = [row.service_guid for row in snapshot.endpoints]
    attachments = tuple(
        ManagedServiceAttachment.objects.filter(
            app_environment=env,
            desired_enabled=True,
            deleted_at__isnull=True,
            managed_service__guid__in=endpoint_ids,
        ).order_by("guid")
    )
    selectors = {row.managed_service_id: tuple(row.workload_names or ["*"]) for row in attachments}
    project_consumers = (
        Q(
            pk__in=selectors,
            registered_app__isnull=True,
            app_environment__isnull=True,
            project_id=app.project_id,
            tenant_cluster_id=env.tenant_cluster_id,
        )
        if app.project_id is not None
        else Q(pk__in=[])
    )
    services = tuple(
        ManagedService.objects.filter(
            Q(registered_app=app, app_environment=env) | project_consumers,
            guid__in=endpoint_ids,
            organization_id__isnull=True,
            deleted_at__isnull=True,
        ).order_by("kind", "name", "guid")
    )
    values_by_service = {row.pk: {} for row in services}
    for row in ManagedServiceBinding.objects.filter(managed_service__in=services).order_by("env_key", "guid"):
        if row.is_secret:
            raise NativeConfigurationError("CONFIGURATION_SECRET_DELIVERY_UNSUPPORTED")
        values_by_service[row.managed_service_id][row.env_key] = row.env_value_ref
    grouped = {}
    for service in services:
        workloads = (
            tuple(service.bind_workloads or ["*"])
            if service.registered_app_id == app.pk and service.app_environment_id == env.pk
            else selectors[service.pk]
        )
        names = (
            [_bindings_secret_name(app.slug)]
            if "*" in workloads
            else [_bindings_secret_name(app.slug, name) for name in workloads]
        )
        for name in names:
            grouped.setdefault(name, {}).update(values_by_service[service.pk])
    return grouped


def _preflight(input, context, current):
    current()
    if connection.in_atomic_block or not connection.get_autocommit():
        raise NativeConfigurationError("CONFIGURATION_REQUIRES_COMMITTED_DATABASE")
    with deployment_app_identity_authority(input.identity_authority, context) as env:
        app, cluster = env.registered_app, env.tenant_cluster
        # A platform build is a legacy in-cluster effect this slice does not compose.
        if app.effective_build_strategy != "off":
            raise NativeConfigurationError("SOURCE_BUILD_UNSUPPORTED")
        snapshot = _deployment(input).secret_snapshot
        if (
            not isinstance(snapshot, dict)
            or "literals" in snapshot
            and (not isinstance(snapshot["literals"], dict) or snapshot["literals"])
        ):
            raise NativeConfigurationError("CONFIGURATION_SECRET_DELIVERY_UNSUPPORTED")
        envs = tuple(
            type(env).objects.filter(registered_app=app, tenant_cluster=cluster).order_by("guid")[:65]
        )
        links = tuple(
            ManagedServiceAttachment.objects.filter(app_environment__in=envs).select_related(
                "managed_service", "app_environment"
            )[:65]
        )
        services = tuple(
            ManagedService.all_objects.filter(
                Q(app_environment__in=envs, deleted_at__isnull=True)
                | Q(pk__in=[row.managed_service_id for row in links])
            ).select_related("project__organization")[:65]
        )
        if not envs or any(len(rows) > 64 for rows in (envs, links, services)):
            raise NativeConfigurationError("CONFIGURATION_UNION_BOUND_EXCEEDED")
        env_ids = {row.pk for row in envs}
        for service in services:
            direct = (
                service.registered_app_id == app.pk
                and service.app_environment_id in env_ids
                and service.project_id is None
                and service.organization_id is None
                and service.tenant_cluster_id is None
            )
            project = (
                service.registered_app_id is None
                and service.app_environment_id is None
                and service.organization_id is None
                and app.project_id is not None
                and service.project_id == app.project_id
                and service.project.deleted_at is None
                and service.project.organization_id == app.organization_id
                and service.tenant_cluster_id == cluster.pk
            )
            if (
                not (direct or project)
                or service.kind != "model_endpoint"
                or service.variant not in ("", "vertex_ai")
                or any(
                    row.slice_handle or row.model_subscription
                    for row in links
                    if row.managed_service_id == service.pk
                )
            ):
                raise NativeConfigurationError("UNSUPPORTED_CONFIGURATION_SERVICE_UNION")
        if AppSecretBundleRef.objects.filter(registered_app=app, app_environment__in=envs).exists() or any(
            row.model_subscription for row in links
        ):
            raise NativeConfigurationError("CONFIGURATION_SECRET_DELIVERY_UNSUPPORTED")
        if (
            ManagedServiceBinding.objects.filter(managed_service__in=services, is_secret=True).exists()
            or ManagedServiceVolumeBinding.objects.filter(managed_service__in=services).exists()
            or literal_secrets_for_environment(app, env)
        ):
            raise NativeConfigurationError("CONFIGURATION_SECRET_DELIVERY_UNSUPPORTED")
        manifest = normalize(parse_raw(app.manifest_raw), defaults=NormalizationDefaults())
        # This private slice has no storage-class, filesystem or Secret producer.
        rendered = render_manifests(
            manifest,
            app_slug=app.slug,
            namespace="preflight",
            image_tag="preflight",
            image_repository=app.slug,
            environment_name=env.name,
        )
        validate_prepared_workload_shapes(rendered)
        for body in rendered:
            if body["kind"] not in (
                "Namespace",
                "Service",
                "Deployment",
                "StatefulSet",
                "DaemonSet",
                "PodMonitor",
                "NetworkPolicy",
                "Ingress",
                "HTTPRoute",
                "Gateway",
                "ConfigMap",
            ):
                raise NativeConfigurationError("CONFIGURATION_WORKLOAD_UNSUPPORTED")
            if body["kind"] in ("PersistentVolumeClaim", "Secret"):
                raise NativeConfigurationError("CONFIGURATION_STORAGE_OR_LITERAL_SECRET_UNSUPPORTED")
            spec = body.get("spec", {})
            pod = spec.get("template", {}).get("spec", {})
            if spec.get("volumeClaimTemplates") or pod.get("volumes"):
                raise NativeConfigurationError("CONFIGURATION_STORAGE_OR_LITERAL_SECRET_UNSUPPORTED")
            if "serviceAccountName" in pod or any(
                key in body.get("metadata", {}).get("annotations", {})
                or key in spec.get("template", {}).get("metadata", {}).get("annotations", {})
                for key in (
                    "iam.gke.io/gcp-service-account",
                    "eks.amazonaws.com/role-arn",
                    "azure.workload.identity/client-id",
                    "astrolift.io/prepared-identity-sha256",
                )
            ):
                raise NativeConfigurationError("CONFIGURATION_IDENTITY_OVERRIDE_UNSUPPORTED")
        current()
        return manifest


def _refuse_unresolved_prior(authority, operation_id):
    """A different operation may proceed only over fully resolved journals.

    A lost reply, crashed worker or expired heartbeat never proves an old send
    was cancelled. Any prior journal that is not terminal blocks every new
    effect, including IAM, before original-source bootstrap.
    """
    owned = {"registered_app__guid": authority.app_guid, "tenant_cluster__guid": authority.cluster_guid}
    for model, resolved in (
        (GCPGKEPreparationJournal, ("OBSERVED", "RESOLVED")),
        (GCPWorkloadIdentityJournal, ("OBSERVED", "RESOLVED")),
        (GCPGKEAppApplyJournal, ("OBSERVED", "REJECTED", "RESOLVED")),
    ):
        for row in model.all_objects.filter(**owned)[:65]:
            if str(row.operation_id) == operation_id:
                continue
            if row.state in ("SENT", "UNKNOWN"):
                raise NativeConfigurationError("PRIOR_SEND_UNRESOLVED")
            if row.deleted_at is not None or row.state not in resolved:
                raise NativeConfigurationError("PRIOR_OPERATION_INCOMPLETE")


def _target(original, plan, provider_id):
    current_context = GKEObservationContext(
        original.identity,
        original.cluster.location,
        original.cluster.cluster_name,
        original.cluster.native_cluster_id,
        tuple(KSASubject(row.namespace, row.name, "", "") for row in plan.template.subjects),
    )
    candidate = PreparationTarget(
        current_context, provider_id, plan.snapshot.credential_sha256, plan.template.subjects
    )
    rows = tuple(
        GCPGKEPreparationJournal.all_objects.filter(
            registered_app__guid=original.identity.app_id, tenant_cluster__guid=original.identity.cluster_id
        )[:2]
    )
    if not rows:
        return candidate
    if len(rows) != 1 or rows[0].deleted_at is not None:
        raise NativeConfigurationError("ORIGINAL_PREPARATION_UNAVAILABLE")
    row = rows[0]
    saved = row.target_snapshot
    try:
        aliases = tuple(
            LogicalSubject(tuple(item["environment_ids"]), item["namespace"], item["name"])
            for item in saved["original_aliases"]
        )
        retained = PreparationTarget(
            replace(current_context, subjects=tuple(KSASubject(**item) for item in saved["subjects"])),
            provider_id,
            plan.snapshot.credential_sha256,
            aliases,
        )
    except (KeyError, TypeError, ValueError):
        raise NativeConfigurationError("ORIGINAL_PREPARATION_INTEGRITY_FAILED") from None
    if retained.payload != saved or retained.sha256 != row.target_sha256:
        raise NativeConfigurationError("ORIGINAL_PREPARATION_CHANGED")
    if {(s.namespace, s.name) for s in retained.original_subjects} != {
        (s.namespace, s.name) for s in plan.template.subjects
    }:
        raise NativeConfigurationError("PHYSICAL_SUBJECT_EVOLUTION_UNSUPPORTED")
    return retained


def execute_native_endpoint_configuration(input, *, ports=None):
    """Actual activity only, configuration metadata only; no public dispatch hook."""
    ports = ports or NativeConfigurationPorts()
    if type(ports) is not NativeConfigurationPorts or input.identity_authority is None:
        raise NativeConfigurationError("PROTECTED_NATIVE_INPUT_REQUIRED")
    with admitted_deployment_execution(input, bind=True) as execution:
        pass
    context = DeploymentAuthorityContext(execution.deployment_guid)
    with deployment_execution_checkpoint(input) as scope:
        render_source = _render_source(input)
        _preflight(input, context, scope)
        _refuse_unresolved_prior(input.identity_authority, execution.operation_uuid)

        def render_current():
            if _render_source(input) != render_source:
                raise NativeConfigurationError("CONFIGURATION_RENDER_SOURCE_CHANGED")

        scope = scope.with_checks(render_current)
        original = bootstrap_original_identity(
            input.identity_authority,
            SourceOperation(execution.operation_uuid, execution.workflow_id, execution.run_id),
            native_factory=ports.source,
            deployment_context=context,
            execution_checkpoint=scope,
        )
        plan = produce_endpoint_app_plan(
            input.identity_authority,
            original.identity,
            observer_factory=ports.endpoints,
            deployment_context=context,
            execution_checkpoint=scope,
        )

        # These synchronous DB fences share one execution boundary, not an
        # admission cache. Preserve render checks at both ends and re-admit the
        # original caller/run before and after every complete invocation.
        def checked(check):
            return scope.with_checks(check).with_checks(render_current)

        endpoint_current = endpoint_app_checkpoint(plan)

        def accepted_current():
            endpoint_current()

        db_current = checked(accepted_current)
        db_current()
        target = _target(original, plan, input.identity_authority.provider_guid)
        prior = GCPGKEPreparationJournal.all_objects.filter(
            registered_app__guid=original.identity.app_id, tenant_cluster__guid=original.identity.cluster_id
        ).first()
        # A later Deployment is the next generation over resolved history and
        # must explicitly supply the previous completed IAM fence.
        next_generation = prior is not None and str(prior.operation_id) != execution.operation_uuid
        revision = prior.desired_revision + 1 if next_generation else prior.desired_revision if prior else 1
        operation = PreparationOperation(
            execution.operation_uuid,
            execution.workflow_id,
            execution.run_id,
            revision,
            plan.template,
            input.identity_authority,
        )

        def admitted(checkpoint):
            db_current()
            if checkpoint.target != target or checkpoint.operation != operation:
                raise NativeConfigurationError("PREPARATION_ACCEPTANCE_CHANGED")

        def prepared_current(prep, reservation, *, iam=None):
            return checked(lambda: prep.validate_current(reservation, checkpoint=admitted, iam=iam))

        def prior_admitted(checkpoint):
            db_current()
            if checkpoint.target != target or str(checkpoint.journal.guid) != str(prior.guid):
                raise NativeConfigurationError("PRIOR_PREPARATION_CHANGED")

        with (
            preparation_journal_mutex(target) as prep,
            ports.preparation(target.context) as preparer,
            ExitStack() as mutexes,
        ):
            iam_store = previous_iam = previous_iam_row = None
            if next_generation:
                previous_iam_row = GCPWorkloadIdentityJournal.all_objects.filter(
                    registered_app__guid=original.identity.app_id,
                    tenant_cluster__guid=original.identity.cluster_id,
                ).first()
            if next_generation and previous_iam_row is None and prior.state == "RESOLVED":
                # An operator-settled operation that never reached IAM: the
                # IAM journal is created fresh below, as in the first generation.
                reservation = prep.reserve(operation, checkpoint=admitted, expected_version=prior.version)
            elif next_generation:
                # IAM follows preparation in mutex order; the same IAM mutex
                # then serves this generation, never a second competing one.
                retained = PreparationStore._reservation(prior)
                iam_store = mutexes.enter_context(
                    journal_mutex(prep.iam_target(retained, checkpoint=prior_admitted))
                )
                previous_iam_row = GCPWorkloadIdentityJournal.all_objects.filter(
                    registered_app__guid=original.identity.app_id,
                    tenant_cluster__guid=original.identity.cluster_id,
                ).first()
                if previous_iam_row is None:
                    raise NativeConfigurationError("PREVIOUS_IAM_UNAVAILABLE")
                previous_iam = (iam_store, JournalStore._reservation(previous_iam_row))
                reservation = prep.reserve(
                    operation, checkpoint=admitted, expected_version=prior.version, previous_iam=previous_iam
                )
            else:
                reservation = prep.reserve(operation, checkpoint=admitted)
            prep_current = prepared_current(prep, reservation)
            state = GCPGKEPreparationJournal.all_objects.get(guid=reservation.journal_id).state
            if state not in ("PREPARED", "OBSERVED"):
                result = preparer.prepare(
                    operation_id=reservation.operation_id,
                    ledger=prep.read(reservation, checkpoint=admitted),
                    checkpoint=prep_current,
                    commit_submission=lambda value: prep.commit_submission(
                        reservation, value, checkpoint=admitted
                    ),
                    commit_observation=lambda value: prep.commit_observation(
                        reservation, value, checkpoint=admitted
                    ),
                    strict_acknowledgements=True,
                    acknowledgement_hook=lambda value: retain_acknowledgement(
                        reservation, value, execution=execution
                    ),
                    acknowledgements=retained_acknowledgements(reservation, execution=execution),
                )
                prep.record_result(
                    reservation, result, stage=PreparationStage.PREPARE, checkpoint=admitted, completed=True
                )
            refresh_endpoint_app_sources(plan, observer_factory=ports.endpoints, execution_checkpoint=scope)
            native_sha = (
                prep.bind_native_union(reservation, checkpoint=admitted)
                if state != "OBSERVED"
                else GCPGKEPreparationJournal.all_objects.get(
                    guid=reservation.journal_id
                ).derived_native_union_sha256
            )
            current_iam_target = prep.iam_target(reservation, checkpoint=admitted)
            if iam_store is None:
                iam_store = mutexes.enter_context(journal_mutex(current_iam_target))
            elif iam_store.target != current_iam_target:
                raise NativeConfigurationError("IAM_ORIGINAL_TARGET_CHANGED")
            with ports.iam(original.identity) as iam_driver:
                iam_operation = OperationIdentity(
                    execution.operation_uuid,
                    execution.workflow_id,
                    execution.run_id,
                    revision,
                    native_sha,
                    operation.authority_reference_sha256,
                )

                def iam_admitted(checkpoint):
                    db_current()
                    if checkpoint.operation != iam_operation or checkpoint.target != iam_store.target:
                        raise NativeConfigurationError("IAM_ACCEPTANCE_CHANGED")

                iam_reservation = iam_store.reserve(
                    iam_operation,
                    checkpoint=iam_admitted,
                    expected_version=previous_iam_row.version if previous_iam_row is not None else None,
                )
                iam = (iam_store, iam_reservation)
                iam_current = checked(
                    lambda: iam_store.validate_current(iam_reservation, checkpoint=iam_admitted)
                )
                result = iam_driver.reconcile(
                    tuple(asdict(grant) for grant in plan.template.permissions),
                    service_account_uids=tuple(
                        s.service_account_uid for s in iam_store.target.ksa_identities
                    ),
                    ledger=iam_store.read(iam_reservation, checkpoint=iam_admitted),
                    checkpoint=iam_current,
                    persist=lambda value: iam_store.persist_ledger(
                        iam_reservation, value, checkpoint=iam_admitted
                    ),
                    submission_hook=lambda value: iam_store.commit_submission(
                        iam_reservation, value, checkpoint=iam_admitted
                    ),
                    acknowledgement_hook=lambda value: retain_acknowledgement(
                        iam_reservation, value, execution=execution
                    ),
                )
                if (
                    GCPWorkloadIdentityJournal.all_objects.get(guid=iam_reservation.journal_id).state
                    != "OBSERVED"
                ):
                    iam_store.record_result(
                        iam_reservation, result.receipt, checkpoint=iam_admitted, completed=True
                    )
                verified = prep.verified_annotation_receipt(
                    reservation, iam=iam, checkpoint=admitted, iam_checkpoint=iam_admitted
                )
                annotation_current = prepared_current(prep, reservation, iam=iam)
                result = preparer.annotate(
                    operation_id=reservation.operation_id,
                    ledger=prep.read(reservation, checkpoint=admitted),
                    checkpoint=annotation_current,
                    iam_receipt=verified,
                    expected_desired_union_sha256=native_sha,
                    commit_submission=lambda value: prep.commit_submission(
                        reservation, value, checkpoint=admitted, iam=iam
                    ),
                    commit_observation=lambda value: prep.commit_observation(
                        reservation, value, checkpoint=admitted, iam=iam
                    ),
                    strict_acknowledgements=True,
                    acknowledgement_hook=lambda value: retain_acknowledgement(
                        reservation, value, execution=execution
                    ),
                    acknowledgements=retained_acknowledgements(reservation, execution=execution),
                )
                if GCPGKEPreparationJournal.all_objects.get(guid=reservation.journal_id).state != "OBSERVED":
                    prep.record_result(
                        reservation,
                        result,
                        stage=PreparationStage.ANNOTATE,
                        checkpoint=admitted,
                        completed=True,
                        iam=iam,
                    )
                identity = prepared_identity_for_deployment(
                    _deployment(input),
                    prep,
                    reservation,
                    iam=iam,
                    checkpoint=admitted,
                    iam_checkpoint=iam_admitted,
                )
                pinned = replace(
                    target.context,
                    subjects=(
                        KSASubject(
                            identity.namespace,
                            identity.service_account_name,
                            identity.namespace_uid,
                            identity.service_account_uid,
                        ),
                    ),
                )
                annotation_current()
                deployment = _deployment(input)
                bodies = render_resources_for_deployment(
                    deployment,
                    include_managed_filesystems=False,
                    prepared_gcp_identity=identity,
                    prepared_literal_snapshot={},
                )
                # Endpoint ADC bindings are literal metadata, never a secrets-backend lookup.
                for name, values in _selected_endpoint_bindings(deployment, plan.snapshot).items():
                    bodies.append(
                        {
                            "apiVersion": "v1",
                            "kind": "Secret",
                            "metadata": {
                                "name": name,
                                "namespace": identity.namespace,
                                "labels": identity.owner_labels,
                            },
                            "type": "Opaque",
                            "stringData": values,
                        }
                    )
                annotation_current()
                compiled, bodies = compile_app_plan(
                    pinned,
                    ExecutionBinding(
                        execution.deployment_guid,
                        execution.operation_uuid,
                        execution.workflow_id,
                        execution.run_id,
                    ),
                    bodies,
                    identity_sha256=identity.fingerprint,
                )

                def configured_current():
                    prep.validate_current(reservation, checkpoint=admitted, iam=iam)
                    iam_store.validate_current(iam_reservation, checkpoint=iam_admitted)

                current = checked(configured_current)
                with ports.apply(pinned) as app_driver:
                    compiled = app_driver.capture_placement(compiled, checkpoint=current)
                    accepted = AcceptedApply(
                        compiled, identity, SourceFence.from_original(original), execution
                    )
                    with app_apply_mutex(
                        accepted,
                        preparation=prep,
                        reservation=reservation,
                        iam=iam,
                        preparation_checkpoint=admitted,
                        iam_checkpoint=iam_admitted,
                    ) as store:

                        def apply_admitted(checkpoint):
                            db_current()
                            if checkpoint.accepted != accepted:
                                raise NativeConfigurationError("APP_ACCEPTANCE_CHANGED")

                        apply_reservation = store.reserve(checkpoint=apply_admitted)
                        apply_current = checked(
                            lambda: store.validate_current(apply_reservation, checkpoint=apply_admitted)
                        )
                        result = app_driver.apply(
                            compiled,
                            bodies,
                            ledger=store.read(apply_reservation, checkpoint=apply_admitted),
                            checkpoint=apply_current,
                            commit_submission=lambda value: store.commit_submission(
                                apply_reservation, value, checkpoint=apply_admitted
                            ),
                            commit_evidence=lambda value: store.commit_evidence(
                                apply_reservation, value, checkpoint=apply_admitted
                            ),
                            commit_observation=lambda value: store.commit_observation(
                                apply_reservation, value, checkpoint=apply_admitted
                            ),
                            commit_rejection=lambda value: store.commit_rejection(
                                apply_reservation, value, checkpoint=apply_admitted
                            ),
                        )
                        store.record_result(apply_reservation, result, checkpoint=apply_admitted)
                        handoff = store.runtime_handoff(apply_reservation, checkpoint=apply_admitted)
                        runtime_current = checked(
                            lambda: store.validate_runtime_handoff(
                                apply_reservation, handoff, checkpoint=apply_admitted
                            )
                        )
                        runtime = observe_handoff_runtime(app_driver, handoff, checkpoint=runtime_current)
                        for _ in range(_RUNTIME_OBSERVATIONS - 1):
                            if runtime.workload_ready or not (
                                runtime.configuration_observed or runtime.reason in _RUNTIME_RETRYABLE
                            ):
                                break
                            Event().wait(_RUNTIME_POLL_SECONDS)
                            runtime = observe_handoff_runtime(app_driver, handoff, checkpoint=runtime_current)
                        runtime_current()
                        return NativeConfigurationReceipt(
                            execution.deployment_guid,
                            execution.operation_uuid,
                            original.source_id,
                            reservation.journal_id,
                            iam_reservation.journal_id,
                            apply_reservation.journal_id,
                            compiled.sha256,
                            result.configuration_observed,
                            runtime.reason,
                            workload_ready=runtime.workload_ready,
                        )


def complete_native_endpoint_configuration(input, receipt):
    """Guarded completion: observed rollout plus the exact committed journals.

    Configuration alone never completes a Deployment. The original caller and
    actual run are re-admitted, and every journal named by the receipt must be
    terminal for this operation, before the ordinary RUNNING transition.
    """
    from astrolift_workflows.activities.app_lifecycle import _mark_running_sync

    if (
        type(receipt) is not NativeConfigurationReceipt
        or not receipt.configuration_observed
        or not receipt.workload_ready
    ):
        raise NativeConfigurationError("NATIVE_ROLLOUT_UNOBSERVED")
    if connection.in_atomic_block or not connection.get_autocommit():
        raise NativeConfigurationError("CONFIGURATION_REQUIRES_COMMITTED_DATABASE")
    with admitted_deployment_execution(input) as execution:
        if (receipt.deployment_id, receipt.operation_id) != (
            execution.deployment_guid,
            execution.operation_uuid,
        ):
            raise NativeConfigurationError("NATIVE_RECEIPT_CHANGED")
        authority = input.identity_authority
        owned = {"registered_app__guid": authority.app_guid, "tenant_cluster__guid": authority.cluster_guid}
        for model, guid in (
            (GCPGKEPreparationJournal, receipt.preparation_id),
            (GCPWorkloadIdentityJournal, receipt.iam_id),
            (GCPGKEAppApplyJournal, receipt.apply_id),
        ):
            row = model.all_objects.filter(guid=guid, **owned).first()
            if (
                row is None
                or row.deleted_at is not None
                or row.state != "OBSERVED"
                or row.observed_at is None
                or str(row.operation_id) != execution.operation_uuid
            ):
                raise NativeConfigurationError("NATIVE_RECEIPT_UNCONFIRMED")
        if not GCPGKEAppApplyOperation.all_objects.filter(
            journal__guid=receipt.apply_id,
            operation_id=execution.operation_uuid,
            accepted_plan_sha256=receipt.plan_sha256,
            state="OBSERVED",
            deleted_at__isnull=True,
        ).exists():
            raise NativeConfigurationError("NATIVE_RECEIPT_UNCONFIRMED")
        with transaction.atomic():
            _retire_rolled_back(input.deployment_id)
            _mark_running_sync(input.deployment_id)


def _retire_rolled_back(deployment_id):
    """A native rollback, once observed live, marks what it replaced ROLLED_BACK.

    Ordinary completion would mark it SUPERSEDED. Idempotent on a retried
    completion: rows already rolled back are no longer RUNNING.
    """
    with transaction.atomic():
        row = Deployment.all_objects.select_for_update().get(pk=deployment_id)
        if row.trigger_kind != Deployment.TriggerKind.ROLLBACK.value:
            return
        for live in (
            Deployment.all_objects.select_for_update()
            .filter(
                registered_app_id=row.registered_app_id,
                app_environment_id=row.app_environment_id,
                status=Deployment.Status.RUNNING.value,
            )
            .exclude(pk=row.pk)
        ):
            live.transition_to(Deployment.Status.ROLLED_BACK)
