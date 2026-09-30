import { z } from "zod";

export const sharedModelDraftSchema = z
  .object({
    name: z.string().trim().min(1).max(128),
    computeMode: z.enum(["cpu", "gpu"]),
    cpuRequest: z.string().trim().min(1),
    memoryRequest: z.string().trim().min(1),
    gpuCount: z.string().regex(/^\d+$/),
    cpuKvCacheGiB: z.string().regex(/^$|^[1-9]\d*$/),
    allowSubscriptions: z.boolean(),
  })
  .refine((draft) =>
    draft.computeMode === "cpu" ? Number(draft.gpuCount) === 0 : Number(draft.gpuCount) > 0
  );
export type SharedModelDraft = {
  name: string;
  computeMode: "" | "cpu" | "gpu";
  cpuRequest: string;
  memoryRequest: string;
  gpuCount: string;
  cpuKvCacheGiB: string;
  allowSubscriptions: boolean;
};
export type SharedModelRequest = {
  organizationId: string;
  clusterId: string;
  expectedProviderId: string;
  name: string;
  modelRepo: string;
  revisionSha: string;
  computeMode: "cpu" | "gpu";
  cpuRequest: string;
  memoryRequest: string;
  gpuCount: number;
  cpuKvCacheGiB: number | null;
  allowSubscriptions: boolean;
};
export function sharedModelRequest(
  organizationId: string,
  cluster: { id: string; providerId: string } | null,
  model: { repoId: string; revisionSha: string } | null,
  draft: SharedModelDraft
): SharedModelRequest | null {
  const parsed = sharedModelDraftSchema.safeParse(draft);
  if (
    !organizationId ||
    !cluster?.id ||
    !cluster.providerId ||
    !model ||
    !/^[a-f0-9]{40}$/i.test(model.revisionSha) ||
    !parsed.success
  )
    return null;
  const value = parsed.data;
  const gpuCount = Number(value.gpuCount),
    cpuKvCacheGiB = value.cpuKvCacheGiB ? Number(value.cpuKvCacheGiB) : null;
  if (
    !Number.isSafeInteger(gpuCount) ||
    (cpuKvCacheGiB !== null && !Number.isSafeInteger(cpuKvCacheGiB))
  )
    return null;
  return {
    organizationId,
    clusterId: cluster.id,
    expectedProviderId: cluster.providerId,
    name: value.name,
    modelRepo: model.repoId,
    revisionSha: model.revisionSha,
    computeMode: value.computeMode,
    cpuRequest: value.cpuRequest,
    memoryRequest: value.memoryRequest,
    gpuCount,
    cpuKvCacheGiB: value.computeMode === "cpu" ? cpuKvCacheGiB : null,
    allowSubscriptions: value.allowSubscriptions,
  };
}
