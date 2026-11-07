import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { admitNvkeRequest, type NvkeAdmissionInput } from "./admission";
import {
  nativeTextDigest,
  readRecovery,
  recoveryKey,
  saveRecovery,
  type NvkeRecoveryOperation,
  type NvkeRecoveryRecord,
  type RecoveryStorage,
} from "./recovery";

const scope = {
  server: "https://astro.example.test/app/gql/config/",
  organizationId: "123e4567-e89b-12d3-a456-426614174000",
  actorId: 41,
};
const siteId = "123e4567-e89b-12d3-a456-426614174001";
const operationId = "123e4567-e89b-12d3-a456-426614174003";
const revision = "92c39b8ad563ebe5ef705f781b3038b901aba10f";
const values = { operationKey: "listClusters", timeoutSeconds: 3600, parametersText: "{}" };

function setup(mode: "native" | "inventory" = "inventory") {
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
  const receipt = vi.fn(async (_requestId: string): Promise<NvkeRecoveryOperation | null> => null);
  const admit = vi.fn(async (input: { requestId: string }) => ({
    ok: true,
    errors: [],
    data: {
      id: operationId,
      siteId,
      requestId: input.requestId,
      operationKey: values.operationKey,
      isInventory: mode === "inventory",
    },
  }));
  return {
    rows,
    options: {
      scope,
      siteId,
      revision,
      expectedSiteVersion: 7,
      values,
      mode,
      storage,
      current: () => true,
      sourceAvailable: () => true,
      onRecord: vi.fn(),
      receipt,
      admit,
    },
  };
}

async function original(format: 1 | 2, mode: "native" | "inventory" = "native") {
  return {
    ...scope,
    ...(format === 1 ? { format: 1 as const } : { format: 2 as const, mode }),
    siteId,
    expectedSiteVersion: 5,
    operationKey: values.operationKey,
    revision,
    timeoutSeconds: values.timeoutSeconds,
    requestId: "123e4567-e89b-12d3-a456-426614174002",
    parametersDigest: await nativeTextDigest(values.parametersText),
    operationId: null,
  } satisfies NvkeRecoveryRecord;
}

beforeEach(() => {
  vi.stubGlobal("navigator", {
    locks: {
      request: async (_key: string, _options: LockOptions, run: () => Promise<unknown>) => run(),
    },
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("shared NVKE durable admission", () => {
  it("sends the reviewed parameter text even if caller state changes after journaling", async () => {
    const { options } = setup("native");
    options.values = { ...values };
    options.onRecord.mockImplementation(() => {
      options.values.parametersText = '{"body":{"changed":true}}';
    });
    await admitNvkeRequest(options);
    expect(options.admit).toHaveBeenCalledWith(expect.objectContaining({ parametersText: "{}" }));
  });
  it("persists and replays the exact resource source after a lost reply", async () => {
    const { options } = setup("native");
    const source = { operationId, version: 7, resourceKey: "signed-key" };
    const resourceOptions = {
      ...options,
      mode: "resource" as const,
      source: { ...source, __typename: "NvkeResourceSourceType" },
      admit: vi.fn(async (input: NvkeAdmissionInput) => ({
        ok: true,
        errors: [],
        data: {
          id: operationId,
          siteId,
          requestId: input.requestId,
          operationKey: values.operationKey,
          isInventory: false,
          resourceSource: { ...source, __typename: "NvkeResourceSourceType" },
        },
      })),
    };
    resourceOptions.admit.mockRejectedValueOnce(new Error("lost reply"));
    await expect(admitNvkeRequest(resourceOptions)).rejects.toThrow("lost reply");
    const pending = readRecovery(options.storage, scope, siteId)!;
    expect(pending).toMatchObject({ format: 3, mode: "resource", source });
    await expect(
      admitNvkeRequest({ ...resourceOptions, source: { ...source, version: 8 } })
    ).rejects.toThrow("RECOVERY_MISMATCH");
    expect(resourceOptions.admit).toHaveBeenCalledTimes(1);
    await admitNvkeRequest(resourceOptions);
    expect(resourceOptions.admit.mock.calls[1][0]).toMatchObject({
      source,
      requestId: pending.requestId,
    });
    expect(readRecovery(options.storage, scope, siteId)).toBeNull();
  });

  it.each(["missing", "version", "resourceKey", "inventory"])(
    "retains resource recovery when a receipt has a mismatched %s binding",
    async (change) => {
      const { options } = setup("native");
      const source = { operationId, version: 7, resourceKey: "signed-key" };
      const resourceOptions = { ...options, mode: "resource" as const, source };
      options.admit.mockRejectedValueOnce(new Error("lost reply"));
      await expect(admitNvkeRequest(resourceOptions)).rejects.toThrow("lost reply");
      const pending = readRecovery(options.storage, scope, siteId)!;
      options.receipt.mockResolvedValueOnce({
        id: operationId,
        siteId,
        requestId: pending.requestId,
        operationKey: values.operationKey,
        isInventory: change === "inventory",
        resourceSource:
          change === "missing"
            ? null
            : {
                ...source,
                ...(change === "version" ? { version: 8 } : {}),
                ...(change === "resourceKey" ? { resourceKey: "another-resource" } : {}),
              },
      });
      await expect(admitNvkeRequest(resourceOptions)).rejects.toThrow("RECOVERY_MISMATCH");
      expect(readRecovery(options.storage, scope, siteId)).toEqual(pending);
      expect(options.admit).toHaveBeenCalledTimes(1);
    }
  );

  it("refuses resource admission without valid source metadata before transmission", async () => {
    const { options } = setup("native");
    await expect(admitNvkeRequest({ ...options, mode: "resource" })).rejects.toThrow(
      "INVALID_RECOVERY"
    );
    expect(options.admit).not.toHaveBeenCalled();
  });
  it.each(["native", "inventory"] as const)(
    "persists %s mode before transmitting and archives its receipt",
    async (mode) => {
      const { options, rows } = setup(mode);
      options.admit.mockImplementationOnce(async (input) => {
        const stored = readRecovery(options.storage, scope, siteId);
        expect(stored).toMatchObject({ format: 2, mode, requestId: input.requestId });
        expect([...rows.values()].join()).not.toContain("parametersText");
        return {
          ok: true,
          errors: [],
          data: {
            id: operationId,
            siteId,
            requestId: input.requestId,
            operationKey: values.operationKey,
            isInventory: mode === "inventory",
          },
        };
      });
      const result = await admitNvkeRequest(options);
      expect(result?.id).toBe(operationId);
      expect(readRecovery(options.storage, scope, siteId)).toBeNull();
      expect([...rows.keys()]).toEqual([expect.stringContaining(":receipt:")]);
    }
  );

  it.each([1, 2] as const)(
    "recovers legacy/current native format %s without replacing its UUID or version",
    async (format) => {
      const { options } = setup("native");
      const record = await original(format);
      saveRecovery(options.storage, record);
      await admitNvkeRequest(options);
      expect(options.receipt).toHaveBeenCalledWith(record.requestId);
      expect(options.admit).toHaveBeenCalledWith(
        expect.objectContaining({
          requestId: record.requestId,
          expectedSiteVersion: 5,
          expectedActorId: 41,
        })
      );
    }
  );

  it.each([1, 2] as const)(
    "retains unresolved native format %s when switching to inventory",
    async (format) => {
      const { options } = setup();
      const record = await original(format);
      saveRecovery(options.storage, record);
      await expect(admitNvkeRequest(options)).rejects.toThrow("RECOVERY_MISMATCH");
      expect(options.admit).not.toHaveBeenCalled();
      expect(readRecovery(options.storage, scope, siteId)).toEqual(record);
    }
  );

  it("retains an unresolved inventory identity when switching to native", async () => {
    const { options } = setup("native");
    const record = await original(2, "inventory");
    saveRecovery(options.storage, record);
    await expect(admitNvkeRequest(options)).rejects.toThrow("RECOVERY_MISMATCH");
    expect(options.admit).not.toHaveBeenCalled();
    expect(readRecovery(options.storage, scope, siteId)).toEqual(record);
  });

  it("opens a confirmed original-mode receipt from either view without another submission", async () => {
    const { options } = setup();
    const record = await original(1);
    saveRecovery(options.storage, record);
    const operation = {
      id: operationId,
      siteId,
      requestId: record.requestId,
      operationKey: record.operationKey,
      isInventory: false,
    };
    options.receipt.mockResolvedValueOnce(operation);
    expect(await admitNvkeRequest(options)).toEqual(operation);
    expect(options.admit).not.toHaveBeenCalled();
    expect(readRecovery(options.storage, scope, siteId)).toBeNull();
  });

  it("rejects a receipt with a different mode and preserves the original", async () => {
    const { options } = setup();
    const record = await original(2, "inventory");
    saveRecovery(options.storage, record);
    options.receipt.mockResolvedValueOnce({
      id: operationId,
      siteId,
      requestId: record.requestId,
      operationKey: record.operationKey,
      isInventory: false,
    });
    await expect(admitNvkeRequest(options)).rejects.toThrow("RECOVERY_MISMATCH");
    expect(readRecovery(options.storage, scope, siteId)).toEqual(record);
    expect(options.admit).not.toHaveBeenCalled();
  });

  it("reuses the original inventory identity after a lost response", async () => {
    const { options } = setup();
    options.admit.mockRejectedValueOnce(new Error("network lost"));
    await expect(admitNvkeRequest(options)).rejects.toThrow("network lost");
    const record = readRecovery(options.storage, scope, siteId)!;
    expect(record).toMatchObject({ format: 2, mode: "inventory" });
    await admitNvkeRequest(options);
    expect(options.admit.mock.calls[0][0].requestId).toBe(record.requestId);
    expect(options.admit.mock.calls[1][0].requestId).toBe(record.requestId);
  });

  it("retains a late accepted request when the current caller visit has changed", async () => {
    const { options } = setup();
    let current = true;
    options.current = () => current;
    options.admit.mockImplementationOnce(async (input) => {
      current = false;
      return {
        ok: true,
        errors: [],
        data: {
          id: operationId,
          siteId,
          requestId: input.requestId,
          operationKey: values.operationKey,
          isInventory: true,
        },
      };
    });
    expect(await admitNvkeRequest(options)).toBeNull();
    expect(readRecovery(options.storage, scope, siteId)?.operationId).toBeNull();
    expect(options.onRecord).toHaveBeenCalledTimes(1);
  });

  it("does not send or replace a request after a caller switch during receipt lookup", async () => {
    const { options } = setup();
    const record = await original(2, "inventory");
    saveRecovery(options.storage, record);
    let current = true;
    options.current = () => current;
    options.receipt.mockImplementationOnce(async () => {
      current = false;
      return null;
    });
    expect(await admitNvkeRequest(options)).toBeNull();
    expect(options.admit).not.toHaveBeenCalled();
    expect(readRecovery(options.storage, scope, siteId)).toEqual(record);
  });

  it("refuses new admission without a current source and leaves storage empty", async () => {
    const { options, rows } = setup();
    options.sourceAvailable = () => false;
    await expect(admitNvkeRequest(options)).rejects.toThrow("SOURCE_CHANGED");
    expect(options.admit).not.toHaveBeenCalled();
    expect(rows.size).toBe(0);
  });

  it("does not transmit when metadata persistence fails", async () => {
    const { options, rows } = setup();
    options.storage.setItem = () => {};
    await expect(admitNvkeRequest(options)).rejects.toThrow("STORAGE_UNAVAILABLE");
    expect(options.admit).not.toHaveBeenCalled();
    expect(rows.has(recoveryKey(scope, siteId))).toBe(false);
  });
});
