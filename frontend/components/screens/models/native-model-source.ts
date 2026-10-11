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

export const isBedrockModelKind = (kind: string | undefined) =>
  ["bedrock_foundation_model", "bedrock_inference_profile"].includes(kind ?? "");

type ModelSource = Partial<
  Pick<ClusterModelFieldsFragment, "sourceKind" | "nativeSource" | "nativeConnection">
>;
const nativeKinds = {
  bedrock_foundation_model: ["BEDROCK", "BEDROCK_FOUNDATION_MODEL"],
  bedrock_inference_profile: ["BEDROCK", "BEDROCK_INFERENCE_PROFILE"],
  bedrock_unknown: ["BEDROCK", "UNKNOWN"],
  vertex_endpoint: ["VERTEX", "VERTEX_ENDPOINT"],
  foundry_deployment: ["FOUNDRY", "FOUNDRY_DEPLOYMENT"],
  unknown: ["UNKNOWN", "UNKNOWN"],
} as const;
const hexFingerprint = (value: unknown): value is string =>
  typeof value === "string" && /^[a-f0-9]{64}$/.test(value);

/** Family is display metadata. It never admits settings, invocation or adoption. */
export const nativeModelFamily = (model: ModelSource) => {
  const raw = nativeKinds[model.sourceKind as keyof typeof nativeKinds];
  const family = model.nativeConnection?.family;
  return raw && raw[0] === family ? family : (raw?.[0] ?? null);
};

/** Current queries advertise the common contract. Only an explicit older-server
 * capability absence permits legacy Bedrock compatibility; absence in a current
 * response is a refusal, never a substitute source or action approval. */
export const modelSourceMode = (
  model: ModelSource,
  commonMetadataSupported = true
): "native" | "native_unavailable" | "hosted" | "unsupported" => {
  const connection = model.nativeConnection;
  if (["huggingface", "local_artifact"].includes(model.sourceKind ?? ""))
    return connection == null && model.nativeSource == null ? "hosted" : "unsupported";
  if (!commonMetadataSupported && connection === undefined)
    return isBedrockModelKind(model.sourceKind) &&
      validNativeSource(model.nativeSource) &&
      model.sourceKind === `bedrock_${model.nativeSource.sourceKind.toLowerCase()}`
      ? "native"
      : "unsupported";
  const raw = nativeKinds[model.sourceKind as keyof typeof nativeKinds];
  if (
    !raw ||
    !connection ||
    raw[0] !== connection.family ||
    raw[1] !== connection.sourceKind ||
    connection.invokeAccess !== "UNKNOWN"
  )
    return "unsupported";
  if (connection.configurationState === "UNAVAILABLE")
    return model.nativeSource == null &&
      connection.source == null &&
      connection.resourceIdentityFingerprint == null &&
      connection.reviewedSourceFingerprint == null &&
      connection.metadataObservedAt == null
      ? "native_unavailable"
      : "unsupported";
  // These shapes declare future metadata only. Current adoption is Bedrock-only.
  if (
    connection.configurationState !== "CONFIGURED" ||
    connection.family !== "BEDROCK" ||
    connection.source?.__typename !== "NativeModelConnectionSource" ||
    !validNativeSource(model.nativeSource) ||
    !validNativeSource(connection.source) ||
    model.nativeSource.configurationState !== "configured" ||
    connection.source.configurationState !== "configured" ||
    model.sourceKind !== `bedrock_${model.nativeSource.sourceKind.toLowerCase()}` ||
    !sameNativeSource(model.nativeSource, connection.source) ||
    !hexFingerprint(connection.resourceIdentityFingerprint) ||
    connection.reviewedSourceFingerprint !== model.nativeSource.sourceFingerprint ||
    connection.metadataObservedAt !== connection.source.metadataObservedAt ||
    (connection.metadataObservedAt != null &&
      !Number.isFinite(Date.parse(connection.metadataObservedAt)))
  )
    return "unsupported";
  return "native";
};

export const sameNativeConnection = (left: ModelSource, right: ModelSource) =>
  modelSourceMode(left) === "native" &&
  modelSourceMode(right) === "native" &&
  sameNativeSource(left.nativeSource, right.nativeSource) &&
  left.nativeConnection?.resourceIdentityFingerprint ===
    right.nativeConnection?.resourceIdentityFingerprint &&
  left.nativeConnection?.reviewedSourceFingerprint ===
    right.nativeConnection?.reviewedSourceFingerprint;

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
    !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(data.id) ||
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
    data.sourceKind !== `bedrock_${request.sourceKind.toLowerCase()}` ||
    modelSourceMode(data) !== "native" ||
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
