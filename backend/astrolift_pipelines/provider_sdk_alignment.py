"""Provider SDK alignment — PipelineExecutor + BlobStore driver protocols (#97).

Adds two new driver protocol contracts to the provider SDK interface:

1. **PipelineExecutor**: the contract a provider plugin implements to spawn
   pipeline job pods on its infrastructure (EKS, GKE, AKS, on-prem K8s).
   The existing ClusterDriver handles generic workload apply/delete; this
   interface adds pipeline-specific concerns: job isolation namespace,
   resource quotas, and job-level pod lifecycle.

2. **BlobStore**: the contract for pipeline artifact storage.
   The platform already has object_store managed services (S3/GCS/AzBlob).
   This formalizes the BlobStore interface so pipeline artifact operations
   can work against any configured blob backend.

Both protocols are defined as Python Protocol classes so type-checkers can
verify implementations without requiring inheritance.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PipelineExecutorProtocol(Protocol):
    """Provider SDK protocol: spawn and manage pipeline job pods.

    Implemented by cloud-specific drivers (k8s_native, aws, gcp, azure).
    The Controller (astrolift-app) calls these methods when dispatching
    pipeline jobs to a cluster.
    """

    def spawn_pipeline_job(
        self,
        *,
        job_name: str,
        namespace: str,
        container_image: str,
        env: dict[str, str],
        timeout_seconds: int,
        cpu_request: str,
        memory_request: str,
        service_account: str | None,
        image_pull_secret: str | None,
    ) -> str:
        """Spawn a pipeline job pod and return its external ID.

        The external_id is backend-specific (K8s Job name, ECS task ARN, etc.)
        and is stored on JobRun.temporal_activity_id for status polling.
        """
        ...

    def poll_pipeline_job(self, external_id: str, namespace: str) -> dict[str, Any]:
        """Poll the current status of a running pipeline job.

        Returns:
            dict with keys:
              - running (bool)
              - succeeded (bool)
              - failed (bool)
              - exit_code (int | None)
              - started_at (datetime | None)
              - finished_at (datetime | None)
        """
        ...

    def cancel_pipeline_job(self, external_id: str, namespace: str) -> None:
        """Cancel a running pipeline job and clean up its resources."""
        ...

    def stream_job_logs(self, external_id: str, namespace: str):
        """Stream log lines from a pipeline job pod.

        Yields strings (log lines), terminates when the pod exits.
        """
        ...


@runtime_checkable
class BlobStoreProtocol(Protocol):
    """Provider SDK protocol: tenant-scoped blob storage for artifacts.

    Implemented by cloud-specific drivers (S3, GCS, Azure Blob).
    Used by the pipeline artifact store (artifact_store.py) and the
    context variable evaluator for artifact passing between jobs.
    """

    def upload(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        """Upload bytes to the blob store at the given key."""
        ...

    def download(self, key: str) -> bytes:
        """Download bytes from the blob store. Raises FileNotFoundError if missing."""
        ...

    def presigned_url(self, key: str, *, expires_in: int = 3600) -> str:
        """Generate a presigned download URL valid for `expires_in` seconds."""
        ...

    def delete(self, key: str) -> None:
        """Delete an object from the blob store."""
        ...

    def list_keys(self, prefix: str) -> list[str]:
        """List all keys under a prefix. Used for artifact cleanup."""
        ...


def validate_pipeline_executor(driver: Any) -> bool:
    """Return True if `driver` satisfies the PipelineExecutorProtocol.

    Call at driver registration time to verify compatibility.
    """
    return isinstance(driver, PipelineExecutorProtocol)


def validate_blob_store(driver: Any) -> bool:
    """Return True if `driver` satisfies the BlobStoreProtocol."""
    return isinstance(driver, BlobStoreProtocol)
