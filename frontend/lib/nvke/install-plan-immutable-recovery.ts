import { z } from "zod";
import type { NvkeInstallPlanTreeJournalSource } from "./install-plan-tree-recovery";
import { validNvkeGuid, NvkeRecoveryError, type RecoveryStorage } from "./recovery";

const guid = z.string().refine(validNvkeGuid),
  version = z.number().int().positive().safe();
const digest = z
  .string()
  .length(64)
  .regex(/^[0-9a-f]{64}$/);
const sourceShape = {
  server: z.string().min(1).max(4096),
  organizationId: guid,
  actorId: version,
  siteId: guid,
  siteVersion: version,
  operationId: guid,
  operationVersion: version,
  authorityId: guid,
  authorityVersion: version,
  instanceId: guid,
  bundleManifestSha256: digest,
  nativeRevision: z
    .string()
    .length(40)
    .regex(/^[0-9a-f]{40}$/),
  planId: guid,
  planVersion: version,
  planDigest: digest,
  chartArtifactId: guid,
  chartArtifactVersion: version,
  chartArtifactDigest: digest,
  treeId: guid,
  treeVersion: version,
  treeDigest: digest,
  workloadId: guid,
  workloadVersion: version,
  workloadDigest: digest,
  mode: z.enum(["payload", "execution", "platform", "pull-authority"]),
  bindingId: guid,
  bindingVersion: version,
  bindingDigest: digest,
};
const sourceSchema = z
  .object(sourceShape)
  .strict()
  .refine(
    (row) =>
      new Set([
        row.operationId,
        row.planId,
        row.chartArtifactId,
        row.treeId,
        row.workloadId,
        row.bindingId,
      ]).size === 6
  );
const selectionSchema = z.object({ artifactId: guid }).strict();
const selectorSchema = selectionSchema.extend({ format: z.literal(1) }).strict();
const schema = z
  .object({
    format: z.literal(1),
    kind: z.enum([
      "nvke-install-plan-image-payload",
      "nvke-install-plan-immutable-execution-tree",
      "nvke-install-plan-node-platforms",
      "nvke-install-plan-pull-authority",
    ]),
    ...sourceShape,
    ...selectionSchema.shape,
    inputSha256: digest,
  })
  .strict();
export type NvkeInstallPlanImmutableJournalSource = NvkeInstallPlanTreeJournalSource & {
  treeId: string;
  treeVersion: number;
  treeDigest: string;
  workloadId: string;
  workloadVersion: number;
  workloadDigest: string;
  mode: "payload" | "execution" | "platform" | "pull-authority";
  bindingId: string;
  bindingVersion: number;
  bindingDigest: string;
};
export type NvkeInstallPlanImmutableChoice = z.infer<typeof selectionSchema>;
export type NvkeInstallPlanImmutableJournal = z.infer<typeof schema>;
export const installPlanImmutableRecoveryKey = (source: NvkeInstallPlanImmutableJournalSource) =>
  "astrolift.nvke.install-plan-immutable.v1:" +
  JSON.stringify([
    source.server,
    source.organizationId,
    source.actorId,
    source.operationId,
    source.planId,
    source.chartArtifactId,
    source.treeId,
    source.workloadId,
    source.bindingId,
    source.mode,
  ]);
const intentKey = (source: NvkeInstallPlanImmutableJournalSource, artifactId: string) =>
  installPlanImmutableRecoveryKey(source) + ":intent:" + artifactId;

function recordKind(mode: "payload" | "execution" | "platform" | "pull-authority") {
  if (mode === "pull-authority") return "nvke-install-plan-pull-authority" as const;
  if (mode === "platform") return "nvke-install-plan-node-platforms" as const;
  return mode === "payload"
    ? ("nvke-install-plan-image-payload" as const)
    : ("nvke-install-plan-immutable-execution-tree" as const);
}
function parseRecord(raw: string): unknown {
  const parsed: unknown = JSON.parse(raw);
  const tokens =
    raw.match(/"(?:\\.|[^"\\])*"|[{}\[\],:]|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g) ??
    [];
  let offset = 0;
  if (tokens[offset++] !== "{") throw new NvkeRecoveryError("INVALID_RECOVERY");
  const seen = new Set<string>();
  while (tokens[offset] !== "}") {
    const key: unknown = JSON.parse(tokens[offset++]);
    if (typeof key !== "string" || seen.has(key) || tokens[offset++] !== ":")
      throw new NvkeRecoveryError("INVALID_RECOVERY");
    seen.add(key);
    const token = tokens[offset++];
    if (!token || ["{", "[", "}", "]", ",", ":"].includes(token))
      throw new NvkeRecoveryError("INVALID_RECOVERY");
    if (tokens[offset] !== ",") break;
    offset++;
  }
  if (tokens[offset++] !== "}" || offset !== tokens.length)
    throw new NvkeRecoveryError("INVALID_RECOVERY");
  return parsed;
}

function safely<T>(run: () => T): T {
  try {
    return run();
  } catch (error) {
    if (error instanceof NvkeRecoveryError) throw error;
    throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  }
}
function choice(source: NvkeInstallPlanImmutableJournalSource, value: unknown) {
  sourceSchema.parse(source);
  const selected = selectionSchema.parse(value);
  if (
    new Set([
      selected.artifactId,
      source.operationId,
      source.planId,
      source.chartArtifactId,
      source.treeId,
      source.workloadId,
      source.bindingId,
    ]).size !== 7
  )
    throw new NvkeRecoveryError("INVALID_RECOVERY");
  return selected;
}
export function readSelectedInstallPlanImmutable(
  storage: RecoveryStorage,
  source: NvkeInstallPlanImmutableJournalSource
): NvkeInstallPlanImmutableChoice | null {
  return safely(() => {
    sourceSchema.parse(source);
    const raw = storage.getItem(installPlanImmutableRecoveryKey(source));
    if (raw === null) return null;
    if (raw.length > 1024) throw new NvkeRecoveryError("INVALID_RECOVERY");
    const parsed = selectorSchema.parse(parseRecord(raw));
    return choice(source, {
      artifactId: parsed.artifactId,
    });
  });
}
export function selectInstallPlanImmutable(
  storage: RecoveryStorage,
  source: NvkeInstallPlanImmutableJournalSource,
  value: NvkeInstallPlanImmutableChoice
) {
  safely(() => {
    const selected = choice(source, value),
      key = installPlanImmutableRecoveryKey(source);
    const text = JSON.stringify({ format: 1, ...selected });
    storage.setItem(key, text);
    if (storage.getItem(key) !== text) throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  });
}
export function readInstallPlanImmutableJournal(
  storage: RecoveryStorage,
  source: NvkeInstallPlanImmutableJournalSource,
  value: NvkeInstallPlanImmutableChoice
): NvkeInstallPlanImmutableJournal | null {
  return safely(() => {
    const expected = sourceSchema.parse(source),
      selected = choice(source, value);
    const raw = storage.getItem(intentKey(source, selected.artifactId));
    if (raw === null) return null;
    if (raw.length > 16384) throw new NvkeRecoveryError("INVALID_RECOVERY");
    const result = schema.parse(parseRecord(raw));
    if (
      result.kind !== recordKind(source.mode) ||
      Object.entries({ ...expected, ...selected }).some(
        ([key, value]) => result[key as keyof NvkeInstallPlanImmutableJournal] !== value
      )
    )
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    return result;
  });
}
export function saveInstallPlanImmutableJournal(
  storage: RecoveryStorage,
  source: NvkeInstallPlanImmutableJournalSource,
  value: NvkeInstallPlanImmutableChoice,
  inputSha256: string
) {
  return safely(() => {
    const selected = choice(source, value),
      record = schema.parse({
        format: 1,
        kind: recordKind(source.mode),
        ...source,
        ...selected,
        inputSha256,
      });
    const previous = readInstallPlanImmutableJournal(storage, source, selected);
    if (previous && previous.inputSha256 !== inputSha256)
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    if (!previous) storage.setItem(intentKey(source, selected.artifactId), JSON.stringify(record));
    if (
      JSON.stringify(readInstallPlanImmutableJournal(storage, source, selected)) !==
      JSON.stringify(record)
    )
      throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
    selectInstallPlanImmutable(storage, source, selected);
    return record;
  });
}
export function clearSelectedInstallPlanImmutable(
  storage: RecoveryStorage,
  source: NvkeInstallPlanImmutableJournalSource,
  expected: NvkeInstallPlanImmutableChoice
) {
  safely(() => {
    const selected = choice(source, expected);
    if (
      JSON.stringify(readSelectedInstallPlanImmutable(storage, source)) !== JSON.stringify(selected)
    )
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    storage.removeItem(installPlanImmutableRecoveryKey(source));
    if (readSelectedInstallPlanImmutable(storage, source) !== null)
      throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  });
}
