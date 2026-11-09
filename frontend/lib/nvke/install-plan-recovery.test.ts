import { expect, it, vi } from "vitest";
import { INSTALL_PLAN_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanPanel.stories";
import {
  readSelectedInstallPlan,
  selectInstallPlan,
  readInstallPlanJournal,
  saveInstallPlanJournal,
  clearSelectedInstallPlan,
  withInstallPlanLock,
  type NvkeInstallPlanJournalSource,
} from "./install-plan-recovery";
const plan = INSTALL_PLAN_PROPS.plan!;
const source: NvkeInstallPlanJournalSource = {
  server: "https://astro.example.test/app/gql/config/",
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
it("retains only source metadata and a digest, with exclusive per-plan intent and readback", () => {
  const s = storage();
  const record = saveInstallPlanJournal(s, source, plan.id, "a".repeat(64));
  expect(readInstallPlanJournal(s, source, plan.id)).toEqual(record);
  expect(readSelectedInstallPlan(s, source, source.operationId)).toBe(plan.id);
  expect(Object.keys(record).sort()).toEqual(
    [
      "format",
      "kind",
      "server",
      "organizationId",
      "actorId",
      "siteId",
      "siteVersion",
      "operationId",
      "operationVersion",
      "authorityId",
      "authorityVersion",
      "instanceId",
      "bundleManifestSha256",
      "nativeRevision",
      "planId",
      "inputSha256",
    ].sort()
  );
  expect(saveInstallPlanJournal(s, source, plan.id, "a".repeat(64))).toEqual(record);
  const before = new Map(s.items);
  expect(() => saveInstallPlanJournal(s, source, plan.id, "b".repeat(64))).toThrow(
    "RECOVERY_MISMATCH"
  );
  expect(s.items).toEqual(before);
});
it("keeps earlier intent journals when choosing another plan", () => {
  const s = storage();
  saveInstallPlanJournal(s, source, plan.id, "a".repeat(64));
  clearSelectedInstallPlan(s, source, source.operationId, plan.id);
  expect(readSelectedInstallPlan(s, source, source.operationId)).toBeNull();
  expect(readInstallPlanJournal(s, source, plan.id)).not.toBeNull();
  selectInstallPlan(s, source, source.operationId, plan.plan.source.nativeRunId);
  expect(readInstallPlanJournal(s, source, plan.plan.source.nativeRunId)).toBeNull();
  expect(readInstallPlanJournal(s, source, plan.id)).not.toBeNull();
});
it("isolates actors, organizations and servers and refuses changed source versions", () => {
  const s = storage();
  saveInstallPlanJournal(s, source, plan.id, "a".repeat(64));
  for (const different of [
    { ...source, actorId: 42 },
    { ...source, organizationId: plan.id },
    { ...source, server: "https://other.test/" },
  ]) {
    expect(readSelectedInstallPlan(s, different, source.operationId)).toBeNull();
    expect(readInstallPlanJournal(s, different, plan.id)).toBeNull();
  }
  for (const field of ["siteVersion", "operationVersion", "authorityVersion"] as const)
    expect(() =>
      readInstallPlanJournal(s, { ...source, [field]: source[field] + 1 }, plan.id)
    ).toThrow("RECOVERY_MISMATCH");
  expect(() => readInstallPlanJournal(s, { ...source, instanceId: plan.id }, plan.id)).toThrow(
    "RECOVERY_MISMATCH"
  );
  expect(() =>
    clearSelectedInstallPlan(s, source, source.operationId, plan.plan.source.nativeRunId)
  ).toThrow("RECOVERY_MISMATCH");
});
it("fails before admission if storage throws, silently drops or corrupts data", () => {
  const s = storage();
  expect(() =>
    saveInstallPlanJournal({ ...s, setItem: () => {} }, source, plan.id, "a".repeat(64))
  ).toThrow("STORAGE_UNAVAILABLE");
  expect(() =>
    saveInstallPlanJournal(
      {
        ...s,
        setItem: () => {
          throw new Error("private storage failure");
        },
      },
      source,
      plan.id,
      "a".repeat(64)
    )
  ).toThrow("STORAGE_UNAVAILABLE");
  saveInstallPlanJournal(s, source, plan.id, "a".repeat(64));
  const intent = [...s.items.keys()].find((key) => key.includes(":intent:"))!;
  s.items.set(
    intent,
    JSON.stringify({ ...readInstallPlanJournal(s, source, plan.id), requestText: "private" })
  );
  expect(() => readInstallPlanJournal(s, source, plan.id)).toThrow("INVALID_RECOVERY");
});
it("requires an operation-scoped exclusive browser lock", async () => {
  vi.stubGlobal("navigator", {});
  await expect(withInstallPlanLock(source, source.operationId, async () => true)).rejects.toThrow(
    "STORAGE_UNAVAILABLE"
  );
  const request = vi.fn(async (_key: string, _options: unknown, run: () => Promise<boolean>) =>
    run()
  );
  vi.stubGlobal("navigator", { locks: { request } });
  await expect(withInstallPlanLock(source, source.operationId, async () => true)).resolves.toBe(
    true
  );
  expect(request.mock.calls[0][1]).toEqual({ mode: "exclusive" });
  vi.unstubAllGlobals();
});
