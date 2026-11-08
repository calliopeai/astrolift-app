import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { RequestNvkeCredentialObservationInput } from "@/graphql/__generated__/operations";
import { admitNvkeCredentials } from "./credential-admission";
import { recoverNvkeNative } from "./native-admission";
import {
  readRecovery,
  validateRecovery,
  type RecoveryStorage,
  type NvkeRecoveryOperation,
} from "./recovery";
const scope = {
  server: "https://astro.example.test/app/gql/config/",
  organizationId: "123e4567-e89b-12d3-a456-426614174000",
  actorId: 41,
};
const siteId = "123e4567-e89b-12d3-a456-426614174001",
  opId = "123e4567-e89b-12d3-a456-426614174002",
  sourceId = "123e4567-e89b-12d3-a456-426614174003";
function setup() {
  const rows = new Map<string, string>();
  const storage: RecoveryStorage = {
    getItem: (key) => rows.get(key) ?? null,
    setItem: (key, value) => {
      rows.set(key, value);
    },
    removeItem: (key) => {
      rows.delete(key);
    },
  };
  const options = {
    scope: { ...scope },
    siteId,
    expectedSiteVersion: 7,
    authorityVersion: 3,
    source: { operationId: sourceId, version: 9, resourceKey: null },
    revision: "92c39b8ad563ebe5ef705f781b3038b901aba10f",
    timeoutSeconds: 600,
    storage,
    current: () => true,
    sourceAvailable: () => true,
    onRecord: vi.fn(),
    receipt: vi.fn(async (_id: string): Promise<NvkeRecoveryOperation | null> => null),
    admit: vi.fn(async (input: RequestNvkeCredentialObservationInput) => ({
      ok: true,
      errors: [] as { code: string }[],
      data: {
        id: opId,
        siteId,
        requestId: input.requestId,
        operationKey: "metrics.credentials",
        isInventory: false,
      } as NvkeRecoveryOperation | null,
    })),
  };
  return { rows, options };
}
beforeEach(() =>
  vi.stubGlobal("navigator", {
    locks: {
      request: async (_key: string, _options: object, run: () => Promise<unknown>) => run(),
    },
  })
);
afterEach(() => vi.unstubAllGlobals());
it("persists the source-bound identity before admission and stores no selector or credentials", async () => {
  const { rows, options } = setup(),
    original = options.admit.getMockImplementation()!;
  options.admit.mockImplementation(async (input) => {
    const saved = readRecovery(options.storage, scope, siteId);
    expect(saved).toMatchObject({
      format: 5,
      mode: "credentials",
      source: { operationId: sourceId, version: 9, resourceKey: null },
      authorityVersion: 3,
      requestId: input.requestId,
    });
    expect(input).toEqual({
      siteId,
      expectedSiteVersion: 7,
      expectedActorId: 41,
      expectedAuthorityVersion: 3,
      sourceOperationId: sourceId,
      expectedSourceVersion: 9,
      timeoutSeconds: 600,
      requestId: saved!.requestId,
    });
    return original(input);
  });
  expect((await admitNvkeCredentials(options))?.id).toBe(opId);
  expect(readRecovery(options.storage, scope, siteId)).toBeNull();
  expect(rows.size).toBe(1);
  expect([...rows.values()].join()).not.toMatch(/endpoint|clusterName|token|credentialsFile/);
});
it("recovers a lost reply before changed authority, source, revision or timeout checks", async () => {
  const { rows, options } = setup();
  let accepted: NvkeRecoveryOperation | null = null;
  options.admit.mockImplementation(async (input) => {
    accepted = {
      id: opId,
      siteId,
      requestId: input.requestId,
      operationKey: "metrics.credentials",
      isInventory: false,
    };
    throw Error("PRIVATE_LOST_REPLY");
  });
  await expect(admitNvkeCredentials(options)).rejects.toThrow("PRIVATE_LOST_REPLY");
  const saved = readRecovery(options.storage, scope, siteId)!;
  options.receipt.mockResolvedValue(accepted);
  const changed = {
    ...options,
    authorityVersion: 99,
    expectedSiteVersion: 88,
    timeoutSeconds: 0,
    revision: "changed",
    source: { ...options.source, version: 99 },
    sourceAvailable: () => false,
  };
  expect((await admitNvkeCredentials(changed))?.requestId).toBe(saved.requestId);
  expect(options.admit).toHaveBeenCalledOnce();
  expect(rows.size).toBe(1);
});
it.each([
  "expectedSiteVersion",
  "authorityVersion",
  "revision",
  "timeoutSeconds",
  "source",
] as const)("refuses changed %s when the receipt is not visible", async (field) => {
  const { options } = setup();
  options.admit.mockRejectedValue(Error("LOST"));
  await expect(admitNvkeCredentials(options)).rejects.toThrow("LOST");
  const saved = readRecovery(options.storage, scope, siteId);
  const changed = { ...options };
  if (field === "source") changed.source = { ...changed.source, version: 99 };
  else if (field === "revision") changed.revision = "0".repeat(40);
  else changed[field] = 99;
  await expect(admitNvkeCredentials(changed)).rejects.toThrow("RECOVERY_MISMATCH");
  expect(readRecovery(options.storage, scope, siteId)).toEqual(saved);
  expect(options.admit).toHaveBeenCalledOnce();
});
it("reuses the original UUID for a missing-receipt retry and recover never admits", async () => {
  const { options } = setup(),
    original = options.admit.getMockImplementation()!;
  options.admit.mockRejectedValueOnce(Error("LOST"));
  await expect(admitNvkeCredentials(options)).rejects.toThrow("LOST");
  const saved = readRecovery(options.storage, scope, siteId)!;
  await expect(recoverNvkeNative(options)).rejects.toThrow("REQUEST_FAILED");
  expect(options.admit).toHaveBeenCalledOnce();
  options.admit.mockImplementation(original);
  expect((await admitNvkeCredentials(options))?.requestId).toBe(saved.requestId);
  expect(options.admit.mock.calls[1][0].requestId).toBe(saved.requestId);
});
it.each([
  "SOURCE_CHANGED",
  "AUTHORITY_REVOKED",
  "PRECONDITION",
  "CONFLICT",
  "UNSUPPORTED",
  "NOT_FOUND",
])("retains uncertain intent for %s", async (code) => {
  const { options } = setup();
  options.admit.mockResolvedValue({ ok: false, data: null, errors: [{ code }] });
  await expect(admitNvkeCredentials(options)).rejects.toThrow("REQUEST_FAILED");
  expect(readRecovery(options.storage, scope, siteId)?.operationKey).toBe("metrics.credentials");
});
it.each(["VERSION_MISMATCH", "SURFACE_UNAVAILABLE", "INVALID_INPUT"])(
  "archives a definite first refusal %s",
  async (code) => {
    const { options } = setup();
    options.admit.mockResolvedValue({ ok: false, data: null, errors: [{ code }] });
    await expect(admitNvkeCredentials(options)).rejects.toThrow("REQUEST_REJECTED");
    expect(readRecovery(options.storage, scope, siteId)).toBeNull();
  }
);
it("keeps an older unknown intent when INVALID_INPUT precedes receipt lookup", async () => {
  const { options } = setup();
  options.admit.mockRejectedValueOnce(Error("LOST"));
  await expect(admitNvkeCredentials(options)).rejects.toThrow();
  const saved = readRecovery(options.storage, scope, siteId);
  options.admit.mockResolvedValue({ ok: false, data: null, errors: [{ code: "INVALID_INPUT" }] });
  await expect(admitNvkeCredentials(options)).rejects.toThrow("REQUEST_FAILED");
  expect(readRecovery(options.storage, scope, siteId)).toEqual(saved);
});
it("refuses receipts that do not identify the exact original intent", async () => {
  const { options } = setup();
  options.admit.mockResolvedValue({
    ok: true,
    errors: [],
    data: {
      id: opId,
      siteId,
      requestId: opId,
      operationKey: "metrics.credentials",
      isInventory: false,
    },
  });
  await expect(admitNvkeCredentials(options)).rejects.toThrow("RECOVERY_MISMATCH");
  expect(readRecovery(options.storage, scope, siteId)).not.toBeNull();
});
it("blocks admission without storage, locks, source authority or a current visit", async () => {
  for (const mode of ["storage", "locks", "source", "visit"]) {
    const { options } = setup();
    if (mode === "storage")
      options.storage.setItem = () => {
        throw Error("unavailable");
      };
    if (mode === "locks") vi.stubGlobal("navigator", {});
    if (mode === "source") options.sourceAvailable = () => false;
    if (mode === "visit") options.current = () => false;
    try {
      await admitNvkeCredentials(options);
    } catch {}
    expect(options.admit).not.toHaveBeenCalled();
    vi.stubGlobal("navigator", {
      locks: {
        request: async (_key: string, _options: object, run: () => Promise<unknown>) => run(),
      },
    });
  }
});
it("strictly validates the new recovery format without changing native workflow formats", async () => {
  const { options } = setup();
  options.admit.mockRejectedValue(Error("LOST"));
  await expect(admitNvkeCredentials(options)).rejects.toThrow();
  const saved = readRecovery(options.storage, scope, siteId)!;
  expect(() => validateRecovery(saved, scope, siteId)).not.toThrow();
  for (const change of [
    { format: 4 },
    { mode: "workflow" },
    { authorityVersion: 0 },
    { operationKey: "temporal.RenewK8sCertsV2" },
    { source: { ...options.source, resourceKey: "opaque" } },
    { extra: "PRIVATE" },
  ])
    expect(() => validateRecovery({ ...saved, ...change }, scope, siteId)).toThrow(
      "INVALID_RECOVERY"
    );
});
