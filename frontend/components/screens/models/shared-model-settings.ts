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
    sharingMode: access.mode,
    dedicatedAppId: access.mode === "DEDICATED" ? access.app!.id : null,
    ifMatchDedicatedAppVersion: access.mode === "DEDICATED" ? access.app!.version : null,
  };
}
