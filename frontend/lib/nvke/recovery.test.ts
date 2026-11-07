import { afterEach, describe, expect, it, vi } from "vitest";
import {
  archiveRecovery,
  archiveRejectedRecovery,
  assertOriginalRequest,
  nativeTextDigest,
  readRecovery,
  recoveryKey,
  saveRecovery,
  validateRecovery,
  withRecoveryLock,
  type NvkeRecoveryRecord,
  type RecoveryStorage,
} from "./recovery";

const record: NvkeRecoveryRecord = {
  format: 1,
  server: "https://astro.example.test/app/gql/config/",
  organizationId: "123e4567-e89b-12d3-a456-426614174000",
  actorId: 41,
  siteId: "123e4567-e89b-12d3-a456-426614174001",
  expectedSiteVersion: 7,
  operationKey: "createCluster",
  revision: "92c39b8ad563ebe5ef705f781b3038b901aba10f",
  timeoutSeconds: 3600,
  requestId: "123e4567-e89b-12d3-a456-426614174002",
  parametersDigest: "a".repeat(64),
  operationId: null,
};
const operation = {
  isInventory: false,
  id: "123e4567-e89b-12d3-a456-426614174003",
  siteId: record.siteId,
  requestId: record.requestId,
  operationKey: record.operationKey,
};
function storage() {
  const rows = new Map<string, string>();
  const api: RecoveryStorage = {
    getItem: (key) => rows.get(key) ?? null,
    setItem: (key, value) => {
      rows.set(key, value);
    },
    removeItem: (key) => {
      rows.delete(key);
    },
  };
  return { rows, api };
}
afterEach(() => vi.unstubAllGlobals());
describe("NVKE browser request identity", () => {
  it.each(["INVALID_INPUT", "VERSION_MISMATCH", "SURFACE_UNAVAILABLE"])(
    "archives definitive %s admission rejection",
    (code) => {
      const { api, rows } = storage();
      saveRecovery(api, record);
      expect(archiveRejectedRecovery(api, record, code)).toBe(true);
      expect(readRecovery(api, record, record.siteId)).toBeNull();
      expect([...rows.values()].map((value) => JSON.parse(value))).toEqual([
        { ...record, rejectionCode: code },
      ]);
    }
  );
  it.each(["SOURCE_CHANGED", "CONFLICT", "PERMISSION_DENIED", "INTERNAL", ""])(
    "preserves unresolved identity for %s",
    (code) => {
      const { api } = storage();
      saveRecovery(api, record);
      expect(archiveRejectedRecovery(api, record, code)).toBe(false);
      expect(readRecovery(api, record, record.siteId)).toEqual(record);
    }
  );
  it("persists only metadata and refuses to replace an unresolved request", () => {
    const { rows, api } = storage();
    saveRecovery(api, record);
    saveRecovery(api, record);
    expect(readRecovery(api, record, record.siteId)).toEqual(record);
    expect(() => saveRecovery(api, { ...record, requestId: operation.id })).toThrow(
      "RECOVERY_MISMATCH"
    );
    expect([...rows.values()]).toEqual([JSON.stringify(record)]);
    expect(() =>
      validateRecovery({ ...record, parametersText: "credential" }, record, record.siteId)
    ).toThrow("INVALID_RECOVERY");
  });
  it.each(["server", "organizationId", "actorId", "siteId"] as const)("isolates %s", (field) => {
    const { api } = storage();
    saveRecovery(api, record);
    const other = { ...record, [field]: field === "actorId" ? 42 : "different" };
    expect(readRecovery(api, other, other.siteId)).toBeNull();
    expect(() => validateRecovery(record, other, other.siteId)).toThrow("INVALID_RECOVERY");
  });
  it("requires the exact original text digest, action, and deadline on retry", async () => {
    const text = '{"body":{"n":9007199254740993,"token":"private"}}';
    const digest = await nativeTextDigest(text);
    const original = { ...record, parametersDigest: digest };
    expect(() => assertOriginalRequest(original, record, digest)).not.toThrow();
    expect(await nativeTextDigest(text + " ")).not.toBe(digest);
    expect(() => assertOriginalRequest(original, record, "b".repeat(64))).toThrow(
      "RECOVERY_MISMATCH"
    );
    expect(() =>
      assertOriginalRequest(original, { ...record, operationKey: "deleteCluster" }, digest)
    ).toThrow("RECOVERY_MISMATCH");
    expect(() =>
      assertOriginalRequest(original, { ...record, timeoutSeconds: 30 }, digest)
    ).toThrow("RECOVERY_MISMATCH");
  });
  it("archives a correlated receipt before clearing the pending identity", () => {
    const { rows, api } = storage();
    saveRecovery(api, record);
    expect(() => archiveRecovery(api, record, { ...operation, requestId: operation.id })).toThrow(
      "RECOVERY_MISMATCH"
    );
    expect(readRecovery(api, record, record.siteId)).toEqual(record);
    archiveRecovery(api, record, operation);
    expect(readRecovery(api, record, record.siteId)).toBeNull();
    expect([...rows.values()].map((value) => JSON.parse(value))).toEqual([
      { ...record, operationId: operation.id },
    ]);
  });
  it("does not recover a resource-bound receipt as an ordinary native request", () => {
    const { api } = storage();
    saveRecovery(api, record);
    expect(() =>
      archiveRecovery(api, record, { ...operation, resourceSource: { operationId: operation.id } })
    ).toThrow("RECOVERY_MISMATCH");
    expect(readRecovery(api, record, record.siteId)).toEqual(record);
  });
  it("preserves the pending identity when receipt storage fails", () => {
    const { api } = storage();
    saveRecovery(api, record);
    const failed = {
      ...api,
      setItem: () => {
        throw new Error("quota private");
      },
    };
    expect(() => archiveRecovery(failed, record, operation)).toThrow("STORAGE_UNAVAILABLE");
    expect(readRecovery(api, record, record.siteId)).toEqual(record);
  });
  it("refuses admission when storage does not retain the write", () => {
    const { api } = storage();
    expect(() => saveRecovery({ ...api, setItem: () => {} }, record)).toThrow(
      "STORAGE_UNAVAILABLE"
    );
    expect(() =>
      readRecovery(
        {
          ...api,
          getItem: () => {
            throw new Error("private");
          },
        },
        record,
        record.siteId
      )
    ).toThrow("STORAGE_UNAVAILABLE");
  });
  it("rejects malformed, oversized, and incompatible journal records without erasing them", () => {
    const { api } = storage();
    const key = recoveryKey(record, record.siteId);
    for (const text of ["{", "x".repeat(16385), JSON.stringify({ ...record, format: 2 })]) {
      api.setItem(key, text);
      expect(() => readRecovery(api, record, record.siteId)).toThrow();
      expect(api.getItem(key)).toBe(text);
    }
  });
  it("requires an exclusive cross-tab request lock", async () => {
    vi.stubGlobal("navigator", {});
    const work = vi.fn(async () => "result");
    await expect(withRecoveryLock(record, record.siteId, work)).rejects.toThrow(
      "STORAGE_UNAVAILABLE"
    );
    expect(work).not.toHaveBeenCalled();
    const request = vi.fn(async (_key, _options, callback) => callback());
    vi.stubGlobal("navigator", { locks: { request } });
    await expect(withRecoveryLock(record, record.siteId, work)).resolves.toBe("result");
    expect(request).toHaveBeenCalledWith(
      recoveryKey(record, record.siteId),
      { mode: "exclusive" },
      work
    );
  });
});
