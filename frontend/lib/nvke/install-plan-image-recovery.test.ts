import { expect, it } from "vitest";
import { INSTALL_CONTEXT_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanContextPanel.stories";
import {
  readSelectedInstallPlanImage,
  readInstallPlanImageJournal,
  saveInstallPlanImageJournal,
  selectInstallPlanImage,
  clearSelectedInstallPlanImage,
  installPlanImageRecoveryKey,
  type NvkeInstallPlanImageJournalSource,
} from "./install-plan-image-recovery";
const f = INSTALL_CONTEXT_PROPS,
  plan = f.plan!,
  charts = f.charts!,
  tree = f.tree!;
const source: NvkeInstallPlanImageJournalSource = {
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
  treeId: tree.id,
  treeVersion: tree.version,
  treeDigest: tree.treeDigest,
};
const chosen = { artifactId: "2be02f88-dc9b-47fb-9662-96504efde439" };
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
it("retains only source metadata, artifact selection and exact-input hash across reloads", () => {
  const s = storage(),
    digest = "e".repeat(64);
  const saved = saveInstallPlanImageJournal(s, source, chosen, digest);
  expect(readSelectedInstallPlanImage(s, source)).toEqual(chosen);
  expect(readInstallPlanImageJournal(s, source, chosen)).toEqual(saved);
  expect(saved.inputSha256).toBe(digest);
  const raw = Array.from(s.items.values()).join();
  for (const field of [
    "requestText",
    "maximumBytes",
    "reference",
    "policy",
    "rendered",
    "credentials",
    "lookupContent",
  ])
    expect(raw).not.toContain(field);
  clearSelectedInstallPlanImage(s, source, chosen);
  expect(readSelectedInstallPlanImage(s, source)).toBeNull();
  expect(readInstallPlanImageJournal(s, source, chosen)).toEqual(saved);
});
it.each([
  { actorId: 42 },
  { server: "https://other.test" },
  { organizationId: tree.id },
  { siteVersion: source.siteVersion + 1 },
  { authorityVersion: source.authorityVersion + 1 },
  { planVersion: source.planVersion + 1 },
  { chartArtifactVersion: source.chartArtifactVersion + 1 },
  { treeVersion: source.treeVersion + 1 },
  { treeDigest: "f".repeat(64) },
])("never certifies another scope or reviewed source %#", (change) => {
  const s = storage();
  saveInstallPlanImageJournal(s, source, chosen, "e".repeat(64));
  const altered = { ...source, ...change };
  if (installPlanImageRecoveryKey(altered) !== installPlanImageRecoveryKey(source))
    expect(readInstallPlanImageJournal(s, altered, chosen)).toBeNull();
  else expect(() => readInstallPlanImageJournal(s, altered, chosen)).toThrow();
});
it.each([{ item: "controller" }, { ordinal: 0 }])(
  "rejects item/ordinal fields outside the image selection %#",
  (change) => {
    const s = storage();
    saveInstallPlanImageJournal(s, source, chosen, "e".repeat(64));
    expect(() => readInstallPlanImageJournal(s, source, { ...chosen, ...change })).toThrow();
    expect(() =>
      saveInstallPlanImageJournal(s, source, { ...chosen, ...change }, "e".repeat(64))
    ).toThrow();
  }
);
it("does not overwrite changed input or clear another tab's selection", () => {
  const s = storage();
  saveInstallPlanImageJournal(s, source, chosen, "e".repeat(64));
  expect(() => saveInstallPlanImageJournal(s, source, chosen, "f".repeat(64))).toThrow();
  expect(() =>
    clearSelectedInstallPlanImage(s, source, {
      ...chosen,
      artifactId: "c5f2545b-31d6-4e81-9628-261ebee18431",
    })
  ).toThrow();
  expect(readSelectedInstallPlanImage(s, source)).toEqual(chosen);
});
it.each([plan.id, charts.id, tree.id, plan.operationId])(
  "refuses a cross-kind artifact UUID %s",
  (artifactId) => {
    const s = storage();
    expect(() => selectInstallPlanImage(s, source, { ...chosen, artifactId })).toThrow();
    expect(s.items.size).toBe(0);
  }
);
it("detects silent writes, corrupt records and private extra fields", () => {
  const s = storage();
  expect(() =>
    saveInstallPlanImageJournal({ ...s, setItem: () => {} }, source, chosen, "e".repeat(64))
  ).toThrow();
  saveInstallPlanImageJournal(s, source, chosen, "e".repeat(64));
  const key = Array.from(s.items.keys()).find((key) => key.includes(":intent:"))!;
  const record = JSON.parse(s.items.get(key)!);
  s.items.set(key, JSON.stringify({ ...record, capture: "PRIVATE_BODY" }));
  expect(() => readInstallPlanImageJournal(s, source, chosen)).toThrow();
  s.items.set(
    installPlanImageRecoveryKey(source),
    JSON.stringify({ format: 1, ...chosen, credentials: "PRIVATE_BODY" })
  );
  expect(() => readSelectedInstallPlanImage(s, source)).toThrow();
});

const linkedSource = {
  ...source,
  workloadId: "c098fc8d-3f48-45fd-b9b0-7d670261e9ef",
  workloadVersion: 2,
  workloadDigest: "4".repeat(64),
};
it("keeps graph and workload image recovery selections separate", () => {
  const s = storage();
  saveInstallPlanImageJournal(s, linkedSource, chosen, "e".repeat(64));
  expect(readSelectedInstallPlanImage(s, source)).toBeNull();
  expect(readSelectedInstallPlanImage(s, linkedSource)).toEqual(chosen);
  expect(() =>
    readInstallPlanImageJournal(s, { ...linkedSource, workloadVersion: 3 }, chosen)
  ).toThrow();
  expect(() =>
    readInstallPlanImageJournal(s, { ...linkedSource, workloadDigest: "9".repeat(64) }, chosen)
  ).toThrow();
});
it.each([
  { workloadId: undefined },
  { workloadVersion: undefined },
  { workloadDigest: undefined },
  { workloadId: source.treeId },
])("refuses incomplete or aliasing workload journal source %#", (change) => {
  expect(() =>
    saveInstallPlanImageJournal(storage(), { ...linkedSource, ...change }, chosen, "e".repeat(64))
  ).toThrow();
});
it("rejects workload fields injected into a graph-only journal", () => {
  const s = storage();
  saveInstallPlanImageJournal(s, source, chosen, "e".repeat(64));
  for (const [key, value] of s.items)
    if (key.includes(":intent:"))
      s.items.set(
        key,
        JSON.stringify({
          ...JSON.parse(value),
          workloadId: linkedSource.workloadId,
          workloadVersion: 2,
          workloadDigest: linkedSource.workloadDigest,
        })
      );
  expect(() => readInstallPlanImageJournal(s, source, chosen)).toThrow();
});
