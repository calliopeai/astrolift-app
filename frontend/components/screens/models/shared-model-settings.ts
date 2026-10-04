import type {
  ClusterModelFieldsFragment,
  ModelSharingMode,
  UpdateClusterModelInput,
} from "@/graphql/__generated__/operations";
import { sharedModelDraftSchema, type SharedModelDraft } from "./shared-model-form";
export type DedicatedModelApp = { id: string; version: number; name: string; slug: string };
export type ModelAccessDraft = { mode: ModelSharingMode; app: DedicatedModelApp | null };
export function modelAccessDraft(model: ClusterModelFieldsFragment): ModelAccessDraft {
  return {
    mode: model.sharingMode,
    app:
      model.dedicatedAppId &&
      model.dedicatedAppVersion &&
      model.dedicatedAppName &&
      model.dedicatedAppSlug
        ? {
            id: model.dedicatedAppId,
            version: model.dedicatedAppVersion,
            name: model.dedicatedAppName,
            slug: model.dedicatedAppSlug,
          }
        : null,
  };
}
export function modelSettingsDraft(model: ClusterModelFieldsFragment): SharedModelDraft {
  const resources = model.desiredResources;
  const dtype = resources.dtype?.toUpperCase();
  return {
    name: model.name,
    computeMode:
      model.computeMode === "cpu" || model.computeMode === "gpu" ? model.computeMode : "",
    cpuRequest: resources.cpuRequest ?? "",
    memoryRequest: resources.memoryRequest ?? "",
    gpuCount: resources.gpuCount == null ? "" : String(resources.gpuCount),
    cpuKvCacheGiB: resources.cpuKvCacheGiB == null ? "" : String(resources.cpuKvCacheGiB),
    allowSubscriptions: model.subscriptionsEnabled,
    dtype:
      dtype === "AUTO" || dtype === "FLOAT16" || dtype === "BFLOAT16" || dtype === "FLOAT32"
        ? dtype
        : "",
    maxModelLen: resources.maxModelLen == null ? "" : String(resources.maxModelLen),
    maxNumSeqs: resources.maxNumSeqs == null ? "" : String(resources.maxNumSeqs),
  };
}
/** The server derives the stored immutable source; this request carries no repo, token or artifact URL. */
export function modelSettingsRequest(
  model: ClusterModelFieldsFragment,
  draft: SharedModelDraft,
  access = modelAccessDraft(model)
): UpdateClusterModelInput | null {
  const parsed = sharedModelDraftSchema.safeParse(draft);
  if (
    !parsed.success ||
    !model.id ||
    !model.organizationId ||
    !model.clusterId ||
    !model.providerId ||
    !Number.isSafeInteger(model.version) ||
    model.version < 1 ||
    draft.computeMode !== model.computeMode ||
    !["SHARED", "DEDICATED"].includes(access.mode)
  )
    return null;
  const value = parsed.data;
  const gpuCount = Number(value.gpuCount);
  const cache = value.computeMode === "cpu" ? Number(value.cpuKvCacheGiB) : null;
  if (
    !Number.isSafeInteger(gpuCount) ||
    (cache !== null && (!Number.isSafeInteger(cache) || cache < 1 || cache > 1024)) ||
    (access.mode === "DEDICATED" &&
      (!access.app?.id || !Number.isSafeInteger(access.app.version) || access.app.version < 1))
  )
    return null;
  for (const [raw, low, high] of [
    [value.maxModelLen, 256, 131072],
    [value.maxNumSeqs, 1, 4096],
  ] as const) {
    if (raw && (!Number.isSafeInteger(Number(raw)) || Number(raw) < low || Number(raw) > high))
      return null;
  }
  return {
    organizationId: model.organizationId,
    id: model.id,
    expectedClusterId: model.clusterId,
    expectedProviderId: model.providerId,
    ifMatchVersion: model.version,
    name: value.name,
    cpuRequest: value.cpuRequest,
    memoryRequest: value.memoryRequest,
    gpuCount,
    cpuKvCacheGiB: cache,
    allowSubscriptions: value.allowSubscriptions,
    dtype: value.dtype || null,
    maxModelLen: value.maxModelLen ? Number(value.maxModelLen) : null,
    maxNumSeqs: value.maxNumSeqs ? Number(value.maxNumSeqs) : null,
    sharingMode: access.mode,
    dedicatedAppId: access.mode === "DEDICATED" ? access.app!.id : null,
    ifMatchDedicatedAppVersion: access.mode === "DEDICATED" ? access.app!.version : null,
  };
}
