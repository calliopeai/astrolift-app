import type {
  ProvisionClusterModelMutation,
  SubscribeClusterModelMutation,
  RevokeModelSubscriptionMutation,
  UpdateClusterModelMutation,
  DeprovisionClusterModelMutation,
  ClusterModelFieldsFragment,
  UpdateClusterModelInput,
  DeprovisionClusterModelInput,
} from "@/graphql/__generated__/operations";
import type {
  SubscriptionRequest,
  RevokeSubscriptionRequest,
  SubscriptionActionResult,
} from "./ModelSubscriptionsPanel";
import type { SharedModelRequest } from "./shared-model-form";
import type { SharedModelDeploymentScreenProps } from "./SharedModelDeploymentScreen";
import type { ManagementResult } from "./SharedModelManagementPanel";
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
    (request.localArtifactId
      ? data.sourceKind !== "local_artifact" ||
        data.localArtifactId !== request.localArtifactId ||
        data.localArtifactVersion !== request.expectedArtifactVersion ||
        !data.localManifestSha256 ||
        !/^[a-f0-9]{64}$/.test(data.localManifestSha256) ||
        data.revisionSha !== null
      : data.modelRepo !== request.modelRepo || data.revisionSha !== request.revisionSha) ||
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
type SubscriptionEnvelope =
  | SubscribeClusterModelMutation["subscribeClusterModel"]
  | RevokeModelSubscriptionMutation["revokeModelSubscription"];
export function subscriptionModelResult(
  envelope: SubscriptionEnvelope | null | undefined,
  request: SubscriptionRequest | RevokeSubscriptionRequest,
  fallback: string
): SubscriptionActionResult {
  const failure = modelWriteFailure(envelope, fallback),
    data = envelope?.data;
  if (failure) return { accepted: false, message: failure };
  if (
    !data ||
    data.restartRequired !== true ||
    data.deployment.id !== request.modelId ||
    data.deployment.organizationId !== request.organizationId ||
    data.deployment.clusterId !== request.expectedClusterId ||
    data.deployment.providerId !== request.expectedProviderId ||
    data.subscription.modelDeploymentId !== request.modelId ||
    !data.subscription.id ||
    !Number.isSafeInteger(data.subscription.version) ||
    data.subscription.version < 1
  )
    return { accepted: false, message: fallback };
  const subscription = data.subscription;
  if ("environmentId" in request) {
    if (
      subscription.environmentId !== request.environmentId ||
      subscription.alias !== request.alias ||
      !subscription.desiredEnabled ||
      subscription.status !== "pending" ||
      data.deployment.version <= request.modelVersion ||
      subscription.desiredRevision !== data.deployment.desiredSubscriptionRevision
    )
      return { accepted: false, message: fallback };
  } else {
    if (
      subscription.id !== request.subscriptionId ||
      subscription.desiredEnabled ||
      !["revoking", "revoked"].includes(subscription.status)
    )
      return { accepted: false, message: fallback };
    if (subscription.status === "revoked") {
      if (subscription.appliedRevision < subscription.desiredRevision || !subscription.reconciledAt)
        return { accepted: false, message: fallback };
      return {
        accepted: true,
        state: "revoked",
        subscriptionId: subscription.id,
        desiredRevision: subscription.desiredRevision,
      };
    }
    if (
      subscription.version <= request.subscriptionVersion ||
      data.deployment.version <= request.modelVersion ||
      subscription.desiredRevision !== data.deployment.desiredSubscriptionRevision
    )
      return { accepted: false, message: fallback };
  }
  if (
    data.deployment.status !== "updating" ||
    !data.deployment.operationId ||
    data.deployment.operationCompletedAt ||
    data.deployment.ready === true
  )
    return { accepted: false, message: fallback };
  return {
    accepted: true,
    state: "pending",
    subscriptionId: subscription.id,
    desiredRevision: subscription.desiredRevision,
  };
}
export function managementModelResult(
  envelope:
    | UpdateClusterModelMutation["updateClusterModel"]
    | DeprovisionClusterModelMutation["deprovisionClusterModel"]
    | null
    | undefined,
  model: ClusterModelFieldsFragment,
  input: UpdateClusterModelInput | DeprovisionClusterModelInput,
  fallback: string
): ManagementResult {
  const failure = modelWriteFailure(envelope, fallback),
    data = envelope?.data,
    update = "cpuRequest" in input;
  if (failure) return { accepted: false, message: failure };
  if (
    !data ||
    data.id !== model.id ||
    data.organizationId !== model.organizationId ||
    data.clusterId !== model.clusterId ||
    data.providerId !== model.providerId ||
    data.modelRepo !== model.modelRepo ||
    data.revisionSha !== model.revisionSha ||
    data.computeMode !== model.computeMode ||
    !Number.isSafeInteger(data.version) ||
    data.version <= input.ifMatchVersion ||
    data.status !== (update ? "updating" : "deprovisioning") ||
    !data.operationId ||
    data.operationCompletedAt ||
    data.ready === true ||
    data.desiredSubscriptionRevision <= model.desiredSubscriptionRevision
  )
    return { accepted: false, message: fallback };
  if (
    update &&
    (data.desiredResources.cpuRequest !== input.cpuRequest ||
      data.desiredResources.memoryRequest !== input.memoryRequest ||
      data.desiredResources.gpuCount !== input.gpuCount ||
      (data.desiredResources.cpuKvCacheGiB ?? null) !== (input.cpuKvCacheGiB ?? null) ||
      data.subscriptionsEnabled !== input.allowSubscriptions)
  )
    return { accepted: false, message: fallback };
  return { accepted: true, operationId: data.operationId };
}
