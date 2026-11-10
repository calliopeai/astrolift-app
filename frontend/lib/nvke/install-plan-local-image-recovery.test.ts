import { expect, it } from "vitest";
import { INSTALL_CONTEXT_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanContextPanel.stories";
import {
  readSelectedInstallPlanLocalImage,
  readInstallPlanLocalImageJournal,
  saveInstallPlanLocalImageJournal,
  selectInstallPlanLocalImage,
  clearSelectedInstallPlanLocalImage,
  installPlanLocalImageRecoveryKey,
  type NvkeInstallPlanLocalImageJournalSource,
} from "./install-plan-local-image-recovery";
const f = INSTALL_CONTEXT_PROPS,
  plan = f.plan!,
  charts = f.charts!,
  tree = f.tree!;
const source: NvkeInstallPlanLocalImageJournalSource = {
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
  const saved = saveInstallPlanLocalImageJournal(s, source, chosen, digest);
  expect(readSelectedInstallPlanLocalImage(s, source)).toEqual(chosen);
  expect(readInstallPlanLocalImageJournal(s, source, chosen)).toEqual(saved);
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
  clearSelectedInstallPlanLocalImage(s, source, chosen);
  expect(readSelectedInstallPlanLocalImage(s, source)).toBeNull();
  expect(readInstallPlanLocalImageJournal(s, source, chosen)).toEqual(saved);
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
  saveInstallPlanLocalImageJournal(s, source, chosen, "e".repeat(64));
  const altered = { ...source, ...change };
  if (installPlanLocalImageRecoveryKey(altered) !== installPlanLocalImageRecoveryKey(source))
    expect(readInstallPlanLocalImageJournal(s, altered, chosen)).toBeNull();
  else expect(() => readInstallPlanLocalImageJournal(s, altered, chosen)).toThrow();
});
it.each([{ item: "controller" }, { ordinal: 0 }])(
  "rejects item/ordinal fields outside the image selection %#",
  (change) => {
    const s = storage();
    saveInstallPlanLocalImageJournal(s, source, chosen, "e".repeat(64));
    expect(() => readInstallPlanLocalImageJournal(s, source, { ...chosen, ...change })).toThrow();
    expect(() =>
      saveInstallPlanLocalImageJournal(s, source, { ...chosen, ...change }, "e".repeat(64))
    ).toThrow();
  }
);
it("does not overwrite changed input or clear another tab's selection", () => {
  const s = storage();
  saveInstallPlanLocalImageJournal(s, source, chosen, "e".repeat(64));
  expect(() => saveInstallPlanLocalImageJournal(s, source, chosen, "f".repeat(64))).toThrow();
  expect(() =>
    clearSelectedInstallPlanLocalImage(s, source, {
      ...chosen,
      artifactId: "c5f2545b-31d6-4e81-9628-261ebee18431",
    })
  ).toThrow();
  expect(readSelectedInstallPlanLocalImage(s, source)).toEqual(chosen);
});
it.each([plan.id, charts.id, tree.id, plan.operationId])(
  "refuses a cross-kind artifact UUID %s",
  (artifactId) => {
    const s = storage();
    expect(() => selectInstallPlanLocalImage(s, source, { ...chosen, artifactId })).toThrow();
    expect(s.items.size).toBe(0);
  }
);
it("detects silent writes, corrupt records and private extra fields", () => {
  const s = storage();
  expect(() =>
    saveInstallPlanLocalImageJournal({ ...s, setItem: () => {} }, source, chosen, "e".repeat(64))
  ).toThrow();
  saveInstallPlanLocalImageJournal(s, source, chosen, "e".repeat(64));
  const key = Array.from(s.items.keys()).find((key) => key.includes(":intent:"))!;
  const record = JSON.parse(s.items.get(key)!);
  s.items.set(key, JSON.stringify({ ...record, capture: "PRIVATE_BODY" }));
  expect(() => readInstallPlanLocalImageJournal(s, source, chosen)).toThrow();
  s.items.set(
    installPlanLocalImageRecoveryKey(source),
    JSON.stringify({ format: 1, ...chosen, credentials: "PRIVATE_BODY" })
  );
  expect(() => readSelectedInstallPlanLocalImage(s, source)).toThrow();
});
