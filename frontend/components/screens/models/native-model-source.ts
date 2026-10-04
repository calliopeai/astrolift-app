import type {
  ClusterModelFieldsFragment,
  NativeModelSourceFieldsFragment,
  RegisterBedrockModelConnectionInput,
  RegisterBedrockModelMutation,
} from "@/graphql/__generated__/operations";
import { modelWriteFailure } from "./shared-model-write-results";

export const validNativeSource = (
  value: NativeModelSourceFieldsFragment | null | undefined
): value is NativeModelSourceFieldsFragment =>
  !!value &&
  [
    value.accountId,
    value.partition,
    value.region,
    value.sourceId,
    value.sourceArn,
    value.sourceFingerprint,
  ].every((field) => typeof field === "string" && !/[\u0000-\u001f\u007f]/.test(field)) &&
  value.protocol === "BEDROCK" &&
  ["FOUNDATION_MODEL", "INFERENCE_PROFILE"].includes(value.sourceKind) &&
  /^\d{12}$/.test(value.accountId) &&
  ["aws", "aws-us-gov", "aws-cn"].includes(value.partition) &&
  /^[a-z]{2}(?:-[a-z]+)+-\d+$/.test(value.region) &&
  !!value.sourceId &&
  value.sourceId.length <= 2048 &&
  (value.sourceKind === "FOUNDATION_MODEL"
    ? value.sourceArn ===
      `arn:${value.partition}:bedrock:${value.region}::foundation-model/${value.sourceId}`
    : ["inference-profile", "application-inference-profile"].some(
        (kind) =>
          value.sourceArn ===
          `arn:${value.partition}:bedrock:${value.region}:${value.accountId}:${kind}/${value.sourceId}`
      )) &&
  /^[a-f0-9]{64}$/.test(value.sourceFingerprint) &&
  ["configured", "unavailable"].includes(value.configurationState) &&
  value.invokeAccess === "unknown" &&
  Array.isArray(value.destinationModelArns) &&
  value.destinationModelArns.every(
    (arn) => typeof arn === "string" && arn.startsWith(`arn:${value.partition}:bedrock:`)
  );

export const modelSourceMode = (
  model: Pick<ClusterModelFieldsFragment, "sourceKind" | "nativeSource">
) =>
  model.nativeSource || model.sourceKind === "bedrock"
    ? validNativeSource(model.nativeSource)
      ? "native"
      : "unsupported"
    : ["huggingface", "local_artifact"].includes(model.sourceKind)
      ? "hosted"
      : "unsupported";

export const sameNativeSource = (
  left: NativeModelSourceFieldsFragment | null | undefined,
  right: NativeModelSourceFieldsFragment | null | undefined
) =>
  validNativeSource(left) &&
  validNativeSource(right) &&
  [
    "protocol",
    "sourceKind",
    "accountId",
    "region",
    "partition",
    "sourceId",
    "sourceArn",
    "sourceFingerprint",
  ].every(
    (key) =>
      left[key as keyof NativeModelSourceFieldsFragment] ===
      right[key as keyof NativeModelSourceFieldsFragment]
  ) &&
  JSON.stringify(left.destinationModelArns) === JSON.stringify(right.destinationModelArns);

export const nativeRegistrationResult = (
  envelope: RegisterBedrockModelMutation["registerBedrockModelConnection"] | null | undefined,
  request: RegisterBedrockModelConnectionInput,
  source: NativeModelSourceFieldsFragment,
  fallback: string
): { accepted: true; id: string } | { accepted: false; message: string } => {
  const failure = modelWriteFailure(envelope, fallback),
    data = envelope?.data;
  if (failure) return { accepted: false, message: failure };
  if (
    !data?.id ||
    data.organizationId !== request.organizationId ||
    data.clusterId !== request.clusterId ||
    data.providerId !== request.expectedProviderId ||
    !Number.isSafeInteger(data.version) ||
    data.version < 1 ||
    data.name !== request.name ||
    data.subscriptionsEnabled !== request.allowSubscriptions ||
    data.sharingMode !== request.sharingMode ||
    data.dedicatedAppId !== request.dedicatedAppId ||
    (request.sharingMode === "DEDICATED" &&
      data.dedicatedAppVersion !== request.ifMatchDedicatedAppVersion) ||
    data.sourceKind !== "bedrock" ||
    !sameNativeSource(data.nativeSource, source) ||
    data.nativeSource?.sourceFingerprint !== request.sourceFingerprint ||
    data.nativeSource.configurationState !== "configured" ||
    data.status !== "active" ||
    data.ready != null ||
    data.runtimeSupported != null ||
    data.computeMode != null ||
    data.operationId != null ||
    data.operationStartedAt != null ||
    data.operationCompletedAt != null
  )
    return { accepted: false, message: fallback };
  return { accepted: true, id: data.id };
};
