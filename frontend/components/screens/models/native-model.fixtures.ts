import type {
  BedrockModelSourceFieldsFragment,
  ClusterModelFieldsFragment,
  CommonNativeConnectionFieldsFragment,
} from "@/graphql/__generated__/operations";
import type { SharedModelListRow } from "./SharedModelsScreen";
import projections from "./native-model-projection.fixture.json";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";

export const nativeSource: BedrockModelSourceFieldsFragment = {
  identity: projections.rows.find((row) => row.case === "foundation")!.serializedQueryData
    .nativeConnection.source as BedrockModelSourceFieldsFragment["identity"],
  name: "Claude 3 Haiku",
  provider: "Anthropic",
  inputModalities: ["TEXT"],
  outputModalities: ["TEXT"],
  streaming: true,
  lifecycle: "ACTIVE",
  profileType: null,
  registerable: true,
  reason: null,
};
export const nativeModel: ClusterModelFieldsFragment = {
  ...sharedModelDetailProps.model!,
  id: "019e1abc-0000-7000-8000-000000000003",
  organizationId: "019e1abc-0000-7000-8000-000000000001",
  clusterId: "019e1abc-0000-7000-8000-000000000002",
  providerId: "019e1abc-0000-7000-8000-000000000004",
  version: 1,
  name: "Native text",
  modelRepo: nativeSource.identity.sourceId,
  sourceKind: projections.rows.find((row) => row.case === "foundation")!.serializedQueryData
    .sourceKind,
  nativeSource: nativeSource.identity,
  nativeConnection: projections.rows.find((row) => row.case === "foundation")!.serializedQueryData
    .nativeConnection as CommonNativeConnectionFieldsFragment,
  revisionSha: null,
  computeMode: null,
  runtimeSupported: null,
  runtimeReason: null,
  ready: null,
  readinessObservedAt: null,
  readinessGeneration: null,
  desiredSubscriptionRevision: 0,
  appliedSubscriptionRevision: 0,
  operationId: null,
  operationStartedAt: null,
  operationCompletedAt: null,
  desiredResources: {
    cpuRequest: null,
    memoryRequest: null,
    gpuCount: null,
    replicas: null,
    cpuKvCacheGiB: null,
    dtype: null,
    maxModelLen: null,
    maxNumSeqs: null,
  },
  appliedResources: null,
};

export const nativeModelRow: SharedModelListRow = {
  ...nativeModel,
  revisionSha: nativeModel.revisionSha ?? null,
  computeMode: nativeModel.computeMode ?? null,
  reason: nativeModel.reason ?? null,
  ready: nativeModel.ready ?? null,
  readinessObservedAt: nativeModel.readinessObservedAt ?? null,
  desiredResources: {
    cpuRequest: nativeModel.desiredResources.cpuRequest ?? null,
    memoryRequest: nativeModel.desiredResources.memoryRequest ?? null,
    gpuCount: nativeModel.desiredResources.gpuCount ?? null,
  },
};

/** Only the four Bedrock rows have persisted HTTP/PG provenance. Other rows
 * are explicitly unadopted transient Strawberry projections, not registrations. */
export const projectedNativeModel = (variant: string): ClusterModelFieldsFragment => {
  const wire = projections.rows.find((row) => row.case === variant)!.serializedQueryData;
  const connection = wire.nativeConnection as CommonNativeConnectionFieldsFragment;
  return {
    ...nativeModel,
    sourceKind: wire.sourceKind,
    nativeConnection: connection,
    nativeSource:
      connection.source?.__typename === "NativeModelConnectionSource" ? connection.source : null,
  };
};
