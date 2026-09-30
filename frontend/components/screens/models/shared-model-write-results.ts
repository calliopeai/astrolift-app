import type { ProvisionClusterModelMutation } from "@/graphql/__generated__/operations";
import type { SharedModelRequest } from "./shared-model-form";
import type { SharedModelDeploymentScreenProps } from "./SharedModelDeploymentScreen";
type ErrorEnvelope = { ok: boolean; errors: { code: string; message: string }[] };
export function modelWriteFailure(
  envelope: ErrorEnvelope | null | undefined,
  fallback: string
): string | null {
  if (envelope?.ok === true && Array.isArray(envelope.errors) && envelope.errors.length === 0)
    return null;
  const first = envelope?.errors?.[0];
  return first?.message ? `${first.code}: ${first.message}` : fallback;
}
export function provisionModelResult(
  envelope: ProvisionClusterModelMutation["provisionClusterModel"] | null | undefined,
  request: SharedModelRequest,
  fallback: string
): Awaited<ReturnType<SharedModelDeploymentScreenProps["onDeploy"]>> {
  const failure = modelWriteFailure(envelope, fallback),
    data = envelope?.data;
  if (failure) return { accepted: false, message: failure };
  if (
    !data?.id ||
    data.organizationId !== request.organizationId ||
    data.clusterId !== request.clusterId ||
    data.providerId !== request.expectedProviderId ||
    data.modelRepo !== request.modelRepo ||
    data.revisionSha !== request.revisionSha ||
    data.computeMode !== request.computeMode ||
    data.name !== request.name ||
    data.subscriptionsEnabled !== request.allowSubscriptions ||
    data.desiredResources.cpuRequest !== request.cpuRequest ||
    data.desiredResources.memoryRequest !== request.memoryRequest ||
    data.desiredResources.gpuCount !== request.gpuCount ||
    (data.desiredResources.cpuKvCacheGiB ?? null) !== request.cpuKvCacheGiB ||
    !Number.isSafeInteger(data.version) ||
    data.version < 1 ||
    !data.operationId ||
    data.operationCompletedAt ||
    data.ready === true ||
    data.status !== "updating"
  )
    return { accepted: false, message: fallback };
  return { accepted: true, deploymentId: data.id };
}
