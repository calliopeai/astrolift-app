import { expect, it } from "vitest";
import { INSTALL_CHARTS_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanChartsPanel.stories";
import {
  readSelectedInstallPlanTree,
  selectInstallPlanTree,
  readInstallPlanTreeJournal,
  saveInstallPlanTreeJournal,
  clearSelectedInstallPlanTree,
  type NvkeInstallPlanTreeJournalSource,
} from "./install-plan-tree-recovery";
const plan = INSTALL_CHARTS_PROPS.plan!;
const charts = INSTALL_CHARTS_PROPS.artifacts!;
const artifactId = "87a76c80-3468-43aa-b2be-e511d587d312";
const source: NvkeInstallPlanTreeJournalSource = {
  server: "https://astro.test/app/gql/config/",
  organizationId: plan.siteId,
  actorId: 41,
  siteId: plan.siteId,
  siteVersion: plan.siteVersion,
  operationId: plan.operationId,
  operationVersion: plan.operationVersion,
  authorityId: plan.authorityId,
  authorityVersion: plan.authorityVersion,
  instanceId: plan.plan.source.instanceId,
  bundleManifestSha256: plan.plan.source.bundleManifestSha256,
  nativeRevision: plan.plan.source.nativeRevision,
  planId: plan.id,
  planVersion: plan.version,
  planDigest: plan.plan.planDigest,
  chartArtifactId: charts.id,
  chartArtifactVersion: charts.version,
  chartArtifactDigest: charts.artifactDigest,
};
function storage() {
  const items = new Map<string, string>();
  return {
    items,
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => {
      items.set(key, value);
    },
    removeItem: (key: string) => {
      items.delete(key);
    },
  };
}
it("retains an immutable hash and complete parent/source binding without request options", () => {
  const s = storage();
  const row = saveInstallPlanTreeJournal(s, source, artifactId, "a".repeat(64));
  expect(row).toEqual({
    format: 1,
    kind: "nvke-install-plan-tree",
    ...source,
    artifactId,
    inputSha256: "a".repeat(64),
  });
  expect(readSelectedInstallPlanTree(s, source, source.operationId, plan.id, charts.id)).toBe(
    artifactId
  );
  expect(saveInstallPlanTreeJournal(s, source, artifactId, "a".repeat(64))).toEqual(row);
  const before = new Map(s.items);
  expect(() => saveInstallPlanTreeJournal(s, source, artifactId, "b".repeat(64))).toThrow(
    "RECOVERY_MISMATCH"
  );
  expect(s.items).toEqual(before);
});
it("preserves earlier intent when choosing another artifact and isolates parent selections", () => {
  const s = storage();
  saveInstallPlanTreeJournal(s, source, artifactId, "a".repeat(64));
  clearSelectedInstallPlanTree(s, source, source.operationId, plan.id, charts.id, artifactId);
  expect(readSelectedInstallPlanTree(s, source, source.operationId, plan.id, charts.id)).toBeNull();
  expect(readInstallPlanTreeJournal(s, source, artifactId)).not.toBeNull();
  expect(
    readSelectedInstallPlanTree(s, source, source.operationId, plan.id, artifactId)
  ).toBeNull();
  selectInstallPlanTree(
    s,
    source,
    source.operationId,
    plan.id,
    charts.id,
    "223e4567-e89b-12d3-a456-426614174099"
  );
  expect(readInstallPlanTreeJournal(s, source, artifactId)).not.toBeNull();
  expect(
    readSelectedInstallPlanTree(s, source, source.operationId, artifactId, charts.id)
  ).toBeNull();
});
it("isolates actors/tenants/servers and rejects every changed source or parent field", () => {
  const s = storage();
  saveInstallPlanTreeJournal(s, source, artifactId, "a".repeat(64));
  for (const different of [
    { ...source, actorId: 42 },
    { ...source, organizationId: artifactId },
    { ...source, server: "https://other.test/" },
  ])
    expect(readInstallPlanTreeJournal(s, different, artifactId)).toBeNull();
  for (const field of [
    "siteVersion",
    "operationVersion",
    "authorityVersion",
    "planVersion",
    "chartArtifactVersion",
  ] as const)
    expect(() =>
      readInstallPlanTreeJournal(s, { ...source, [field]: source[field] + 1 }, artifactId)
    ).toThrow("RECOVERY_MISMATCH");
  for (const different of [
    { ...source, instanceId: artifactId },
    { ...source, planDigest: "d".repeat(64) },
    { ...source, chartArtifactDigest: "d".repeat(64) },
  ])
    expect(() => readInstallPlanTreeJournal(s, different, artifactId)).toThrow("RECOVERY_MISMATCH");
  for (const id of [source.operationId, plan.id, charts.id])
    expect(() => saveInstallPlanTreeJournal(s, source, id, "a".repeat(64))).toThrow(
      "INVALID_RECOVERY"
    );
});
it("refuses dropped writes, corrupted/private fields and conflicting selection deletion", () => {
  const s = storage();
  expect(() =>
    saveInstallPlanTreeJournal({ ...s, setItem: () => {} }, source, artifactId, "a".repeat(64))
  ).toThrow("STORAGE_UNAVAILABLE");
  saveInstallPlanTreeJournal(s, source, artifactId, "a".repeat(64));
  expect(() =>
    clearSelectedInstallPlanTree(
      s,
      source,
      source.operationId,
      plan.id,
      charts.id,
      "223e4567-e89b-12d3-a456-426614174099"
    )
  ).toThrow("RECOVERY_MISMATCH");
  const key = [...s.items.keys()].find((key) => key.includes(":intent:"))!;
  s.items.set(
    key,
    JSON.stringify({
      ...readInstallPlanTreeJournal(s, source, artifactId),
      requestText: "private",
    })
  );
  expect(() => readInstallPlanTreeJournal(s, source, artifactId)).toThrow("INVALID_RECOVERY");
});
