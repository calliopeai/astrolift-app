import { expect, it } from "vitest";
import { INSTALL_CHARTS_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanChartsPanel.stories";
import {
  readSelectedInstallPlanCharts,
  selectInstallPlanCharts,
  readInstallPlanChartsJournal,
  saveInstallPlanChartsJournal,
  clearSelectedInstallPlanCharts,
  type NvkeInstallPlanChartsJournalSource,
} from "./install-plan-charts-recovery";
const plan = INSTALL_CHARTS_PROPS.plan!;
const artifactId = INSTALL_CHARTS_PROPS.artifacts!.id;
const source: NvkeInstallPlanChartsJournalSource = {
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
  const row = saveInstallPlanChartsJournal(s, source, artifactId, "a".repeat(64));
  expect(row).toEqual({
    format: 1,
    kind: "nvke-install-plan-charts",
    ...source,
    artifactId,
    inputSha256: "a".repeat(64),
  });
  expect(readSelectedInstallPlanCharts(s, source, source.operationId, plan.id)).toBe(artifactId);
  expect(saveInstallPlanChartsJournal(s, source, artifactId, "a".repeat(64))).toEqual(row);
  const before = new Map(s.items);
  expect(() => saveInstallPlanChartsJournal(s, source, artifactId, "b".repeat(64))).toThrow(
    "RECOVERY_MISMATCH"
  );
  expect(s.items).toEqual(before);
});
it("preserves earlier intent when choosing another artifact and isolates parent selections", () => {
  const s = storage();
  saveInstallPlanChartsJournal(s, source, artifactId, "a".repeat(64));
  clearSelectedInstallPlanCharts(s, source, source.operationId, plan.id, artifactId);
  expect(readSelectedInstallPlanCharts(s, source, source.operationId, plan.id)).toBeNull();
  expect(readInstallPlanChartsJournal(s, source, artifactId)).not.toBeNull();
  selectInstallPlanCharts(
    s,
    source,
    source.operationId,
    plan.id,
    "223e4567-e89b-12d3-a456-426614174099"
  );
  expect(readInstallPlanChartsJournal(s, source, artifactId)).not.toBeNull();
  expect(readSelectedInstallPlanCharts(s, source, source.operationId, artifactId)).toBeNull();
});
it("isolates actors/tenants/servers and rejects every changed source or parent field", () => {
  const s = storage();
  saveInstallPlanChartsJournal(s, source, artifactId, "a".repeat(64));
  for (const different of [
    { ...source, actorId: 42 },
    { ...source, organizationId: artifactId },
    { ...source, server: "https://other.test/" },
  ])
    expect(readInstallPlanChartsJournal(s, different, artifactId)).toBeNull();
  for (const field of [
    "siteVersion",
    "operationVersion",
    "authorityVersion",
    "planVersion",
  ] as const)
    expect(() =>
      readInstallPlanChartsJournal(s, { ...source, [field]: source[field] + 1 }, artifactId)
    ).toThrow("RECOVERY_MISMATCH");
  for (const different of [
    { ...source, instanceId: artifactId },
    { ...source, planDigest: "d".repeat(64) },
  ])
    expect(() => readInstallPlanChartsJournal(s, different, artifactId)).toThrow(
      "RECOVERY_MISMATCH"
    );
  for (const id of [source.operationId, plan.id])
    expect(() => saveInstallPlanChartsJournal(s, source, id, "a".repeat(64))).toThrow(
      "INVALID_RECOVERY"
    );
});
it("refuses dropped writes, corrupted/private fields and conflicting selection deletion", () => {
  const s = storage();
  expect(() =>
    saveInstallPlanChartsJournal({ ...s, setItem: () => {} }, source, artifactId, "a".repeat(64))
  ).toThrow("STORAGE_UNAVAILABLE");
  saveInstallPlanChartsJournal(s, source, artifactId, "a".repeat(64));
  expect(() =>
    clearSelectedInstallPlanCharts(
      s,
      source,
      source.operationId,
      plan.id,
      "223e4567-e89b-12d3-a456-426614174099"
    )
  ).toThrow("RECOVERY_MISMATCH");
  const key = [...s.items.keys()].find((key) => key.includes(":intent:"))!;
  s.items.set(
    key,
    JSON.stringify({
      ...readInstallPlanChartsJournal(s, source, artifactId),
      requestText: "private",
    })
  );
  expect(() => readInstallPlanChartsJournal(s, source, artifactId)).toThrow("INVALID_RECOVERY");
});
