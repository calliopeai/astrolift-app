import { z } from "zod";

export const sharedModelDraftSchema = z
  .object({
    name: z.string().trim().min(1).max(128),
    computeMode: z.enum(["", "cpu", "gpu"]),
    cpuRequest: z.string().trim().min(1),
    memoryRequest: z.string().trim().min(1),
    gpuCount: z.string().regex(/^\d+$/),
    cpuKvCacheGiB: z.string().regex(/^$|^[1-9]\d*$/),
    allowSubscriptions: z.boolean(),
    dtype: z.enum(["", "AUTO", "FLOAT16", "BFLOAT16", "FLOAT32"]).optional(),
    maxModelLen: z
      .string()
      .regex(/^$|^[1-9]\d*$/)
      .optional(),
    maxNumSeqs: z
      .string()
      .regex(/^$|^[1-9]\d*$/)
      .optional(),
  })
  .refine(
    (draft) =>
      draft.computeMode !== "" &&
      (draft.computeMode === "cpu"
        ? Number(draft.gpuCount) === 0 &&
          /^[1-9]\d*$/.test(draft.cpuKvCacheGiB) &&
          Number(draft.cpuKvCacheGiB) <= 1024
        : Number(draft.gpuCount) > 0)
  );
export type SharedModelDraft = {
  name: string;
  computeMode: "" | "cpu" | "gpu";
  cpuRequest: string;
  memoryRequest: string;
  gpuCount: string;
  cpuKvCacheGiB: string;
  allowSubscriptions: boolean;
  dtype?: "" | "AUTO" | "FLOAT16" | "BFLOAT16" | "FLOAT32";
  maxModelLen?: string;
  maxNumSeqs?: string;
};
export type SharedModelSource =
  | { repoId: string; revisionSha: string }
  | {
      localArtifactId: string;
      expectedArtifactVersion: number;
      name: string;
      manifestSha256: string;
    };
export type SharedModelRequest = {
  organizationId: string;
  clusterId: string;
  expectedProviderId: string;
  name: string;
  modelRepo: string | null;
  revisionSha: string | null;
  localArtifactId: string | null;
  expectedArtifactVersion: number | null;
  computeMode: "cpu" | "gpu";
  cpuRequest: string;
  memoryRequest: string;
  gpuCount: number;
  cpuKvCacheGiB: number | null;
  allowSubscriptions: boolean;
  connectionId: string | null;
  expectedConnectionVersion: number | null;
  dtype: "AUTO" | "FLOAT16" | "BFLOAT16" | "FLOAT32" | null;
  maxModelLen: number | null;
  maxNumSeqs: number | null;
};
export function sharedModelRequest(
  organizationId: string,
  cluster: { id: string; providerId: string } | null,
  model: SharedModelSource | null,
  draft: SharedModelDraft,
  connection: { connectionId: string; expectedConnectionVersion: number } | null = null
): SharedModelRequest | null {
  const parsed = sharedModelDraftSchema.safeParse(draft);
  if (
    !organizationId ||
    !cluster?.id ||
    !cluster.providerId ||
    !model ||
    ("repoId" in model
      ? !model.repoId || !/^[a-f0-9]{40}$/i.test(model.revisionSha)
      : !model.localArtifactId ||
        !Number.isSafeInteger(model.expectedArtifactVersion) ||
        model.expectedArtifactVersion < 1 ||
        !/^[a-f0-9]{64}$/.test(model.manifestSha256) ||
        connection !== null) ||
    !parsed.success
  )
    return null;
  const value = parsed.data;
  if (value.computeMode === "") return null;
  const gpuCount = Number(value.gpuCount),
    cpuKvCacheGiB = value.cpuKvCacheGiB ? Number(value.cpuKvCacheGiB) : null;
  if (
    !Number.isSafeInteger(gpuCount) ||
    (cpuKvCacheGiB !== null && !Number.isSafeInteger(cpuKvCacheGiB))
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
    organizationId,
    clusterId: cluster.id,
    expectedProviderId: cluster.providerId,
    name: value.name,
    modelRepo: "repoId" in model ? model.repoId : null,
    revisionSha: "repoId" in model ? model.revisionSha : null,
    localArtifactId: "localArtifactId" in model ? model.localArtifactId : null,
    expectedArtifactVersion: "localArtifactId" in model ? model.expectedArtifactVersion : null,
    computeMode: value.computeMode,
    cpuRequest: value.cpuRequest,
    memoryRequest: value.memoryRequest,
    gpuCount,
    cpuKvCacheGiB: value.computeMode === "cpu" ? cpuKvCacheGiB : null,
    allowSubscriptions: value.allowSubscriptions,
    dtype: value.dtype || null,
    maxModelLen: value.maxModelLen ? Number(value.maxModelLen) : null,
    maxNumSeqs: value.maxNumSeqs ? Number(value.maxNumSeqs) : null,
    ...(connection ?? { connectionId: null, expectedConnectionVersion: null }),
  };
}
