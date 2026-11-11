"""Read-only correspondence between retained analysis and node captures."""

from copy import deepcopy

from django.db import transaction
from nvke import _operator_install_plan_pull_runtime as protocol
from nvke.client import NvkeError

from astrolift_clusters import nvke_install_plan_node_platforms as nodes
from astrolift_clusters import nvke_install_plan_pull_secret_analysis as analyses


def _snapshot(row):
    return {
        field.attname: bytes(value) if isinstance(value, memoryview) else value
        for field in row._meta.concrete_fields
        for value in [getattr(row, field.attname)]
    }


def read(
    *,
    analysis_id,
    expected_analysis_version,
    analysis_digest,
    node_platform_id,
    expected_node_platform_version,
    node_platform_digest,
    **values,
):
    for version, digest in (
        (expected_analysis_version, analysis_digest),
        (expected_node_platform_version, node_platform_digest),
    ):
        if (
            type(version) is not int
            or version < 1
            or not isinstance(digest, str)
            or not protocol.SHA.fullmatch(digest)
        ):
            raise NvkeError("INVALID_INPUT", "Review the exact prepared analysis and node capture versions")
    _caller, original, selected, plan, charts, tree, binding, source, parent_check = analyses._source(values)
    node_source = {
        key: value
        for key, value in source.items()
        if key
        not in {
            "pull_authority",
            "pull_authority_version",
            "pull_authority_digest",
            "pull_authority_summary_sha256",
        }
    }

    def retained(*, locked=False):
        parent_check()
        analysis = analyses._retained(analysis_id, binding, source, locked=locked)
        node = nodes._retained(node_platform_id, binding, node_source, locked=locked)
        for row, version, digest, digest_field in (
            (analysis, expected_analysis_version, analysis_digest, "authorityDigest"),
            (node, expected_node_platform_version, node_platform_digest, "platformDigest"),
        ):
            if row.status != "prepared" or row.version != version or row.summary.get(digest_field) != digest:
                raise NvkeError("SOURCE_CHANGED", "A reviewed analysis or node capture changed")
        analyses._summary(analysis.summary, analysis, plan, charts, tree, binding)
        nodes._summary(node.summary, node, plan, charts, tree, binding)
        return analysis, node

    def lock():
        analyses._lock(original, plan, charts, tree, binding, source)

    with transaction.atomic():
        lock()
        analysis, node = retained(locked=True)
        snapshots = (_snapshot(analysis), _snapshot(node))
        body = protocol.request(
            {
                **{
                    key: value
                    for key, value in analyses._body(analysis, plan, charts, tree, binding).items()
                    if key in analyses.protocol.BASE
                },
                "analysisDigest": analysis_digest,
                "nodePlatformId": str(node.guid),
                "nodePlatformDigest": node_platform_digest,
            },
            job_id=str(original.guid),
            chart_artifact_id=str(charts.guid),
            workload_id=source["workload"],
        )

    def checkpoint():
        current = retained()
        if tuple(_snapshot(row) for row in current) != snapshots:
            raise NvkeError("SOURCE_CHANGED", "The retained analysis or node intent changed during review")

    checkpoint()
    with analyses.authority.open_agent(selected, checkpoint=checkpoint) as client:
        data = client.install_plan_pull_runtime_correspondence(
            str(original.guid), body=deepcopy(body), **analyses._sdk_source(original, charts, source)
        )
    with transaction.atomic():
        lock()
        retained(locked=True)
        checkpoint()
        try:
            result = protocol.summary(
                data,
                body=body,
                source={
                    "analysis": analysis.summary,
                    "pullAuthority": analyses.NvkeInstallPlanPullAuthority.all_objects.get(
                        guid=source["pull_authority"]
                    ).summary,
                    "binding": binding.summary,
                    "nodePlatforms": node.summary,
                },
            )
        except (ValueError, KeyError, TypeError, OverflowError):
            raise NvkeError(
                "OPERATOR_INVALID_RESPONSE", "The agent did not confirm the retained pull correspondence"
            ) from None
        checkpoint()
        return {
            "analysis": analyses._public(analysis, plan, charts, tree, binding, original, selected),
            "node_platforms": nodes._public(node, plan, charts, tree, binding, original, selected),
            "metadata": result,
        }
