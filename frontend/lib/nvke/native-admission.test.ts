import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  admitNvkeNative,
  nativeMutationInput,
  recoverNvkeNative,
  nativeOperationKey,
  type NvkeNativeValues,
  type NvkeNativeAdmissionInput,
} from "./native-admission";
import { admitNvkeRequest } from "./admission";
import {
  readRecovery,
  saveRecovery,
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
const reset: NvkeNativeValues = {
  mode: "reset",
  timeoutSeconds: 7200,
  review: {
    workflowId: "owner-更新",
    baseRunId: opId,
    workflowTaskEventId: "9007199254740993",
    activityEventId: "9007199254740994",
    activityType: "StartEtcdEncryptionRewrite",
    phase: "rewrite",
    mode: "restart_rewrite",
    rationale: "PRIVATE_REASON",
    quiescenceEvidence: "PRIVATE_NODE_EVIDENCE",
    acknowledgeBoundaryReviewed: true,
    acknowledgeConflictingWorkStopped: true,
    retirementWorkflowId: "",
  },
};
const workflow: NvkeNativeValues = {
  mode: "workflow",
  workflow: "RenewK8sCertsV2",
  parametersText: '{ "clusterID":"PRIVATE_CLUSTER", "thresholdDays":9007199254740993 }',
  acknowledgeLostNodeTeardown: false,
  timeoutSeconds: 7200,
};
function setup(values: NvkeNativeValues = reset) {
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
    values: structuredClone(values),
    storage,
    current: () => true,
    sourceAvailable: () => true,
    onRecord: vi.fn(),
    receipt: vi.fn(async (_id: string): Promise<NvkeRecoveryOperation | null> => null),
    admit: vi.fn(async (input: NvkeNativeAdmissionInput) => ({
      ok: true,
      errors: [] as { code: string }[],
      data: {
        id: opId,
        siteId,
        requestId: input.requestId,
        operationKey: nativeOperationKey(values),
        isInventory: false,
      } as NvkeRecoveryOperation | null,
    })),
  };
  return { rows, options };
}
beforeEach(() =>
  vi.stubGlobal("navigator", {
    locks: {
      request: async (_key: string, _options: LockOptions, run: () => Promise<unknown>) => run(),
    },
  })
);
afterEach(() => vi.unstubAllGlobals());
describe("native browser admission shares the durable site journal", () => {
  it.each([reset, workflow])(
    "keeps exact private input in memory and reuses the frozen native request",
    async (values) => {
      const { rows, options } = setup(values);
      options.admit.mockRejectedValueOnce(new Error("LOST"));
      await expect(admitNvkeNative(options)).rejects.toThrow("LOST");
      const record = readRecovery(options.storage, scope, siteId)!;
      expect(record.format).toBe(4);
      expect([...rows.values()].join()).not.toMatch(/PRIVATE|owner-更新|activityEventId/);
      await admitNvkeNative(options);
      expect(options.admit.mock.calls[0][0]).toEqual(options.admit.mock.calls[1][0]);
      expect(options.admit.mock.calls[0][0]).toMatchObject({
        expectedSiteVersion: 7,
        expectedAuthorityVersion: 3,
        expectedSourceVersion: 9,
        expectedActorId: 41,
        sourceOperationId: sourceId,
      });
      expect(readRecovery(options.storage, scope, siteId)).toBeNull();
      if (values.mode === "reset")
        expect(options.admit.mock.calls[1][0]).toMatchObject({
          review: { workflowTaskEventId: "9007199254740993" },
        });
      else
        expect(options.admit.mock.calls[1][0]).toMatchObject({
          parametersText: values.parametersText,
        });
    }
  );
  it("recovers a receipt before examining changed private values or authority", async () => {
    const { options } = setup();
    options.admit.mockRejectedValueOnce(new Error("LOST"));
    await expect(admitNvkeNative(options)).rejects.toThrow();
    const record = readRecovery(options.storage, scope, siteId)!;
    const row = {
      id: opId,
      siteId,
      requestId: record.requestId,
      operationKey: record.operationKey,
      isInventory: false,
    };
    options.receipt.mockResolvedValue(row);
    options.authorityVersion = 99;
    options.values = structuredClone(workflow);
    expect(await admitNvkeNative(options)).toEqual(row);
    expect(options.admit).toHaveBeenCalledTimes(1);
  });
  it("supports lookup-only recovery without private input", async () => {
    const { options } = setup();
    options.admit.mockRejectedValueOnce(new Error("LOST"));
    await expect(admitNvkeNative(options)).rejects.toThrow();
    const record = readRecovery(options.storage, scope, siteId)!;
    options.receipt.mockResolvedValue({
      id: opId,
      siteId,
      requestId: record.requestId,
      operationKey: record.operationKey,
      isInventory: false,
    });
    await recoverNvkeNative(options);
    expect(options.admit).toHaveBeenCalledTimes(1);
    expect(readRecovery(options.storage, scope, siteId)).toBeNull();
  });
  it.each(["authority", "source", "site", "revision", "mode", "timeout", "private-input"])(
    "refuses a changed pending %s review",
    async (change) => {
      const { options } = setup();
      options.admit.mockRejectedValueOnce(new Error("LOST"));
      await expect(admitNvkeNative(options)).rejects.toThrow();
      switch (change) {
        case "authority":
          options.authorityVersion++;
          break;
        case "source":
          options.source.version++;
          break;
        case "site":
          options.expectedSiteVersion++;
          break;
        case "revision":
          options.revision = "b".repeat(40);
          break;
        case "mode":
          options.values = structuredClone(workflow);
          break;
        case "timeout":
          options.values.timeoutSeconds++;
          break;
        case "private-input":
          if (options.values.mode === "reset") options.values.review.rationale = "PRIVATE_CHANGED";
      }
      await expect(admitNvkeNative(options)).rejects.toThrow("RECOVERY_MISMATCH");
      expect(options.admit).toHaveBeenCalledTimes(1);
    }
  );
  it("blocks HTTP views from replacing an unresolved native reset", async () => {
    const { options } = setup();
    options.admit.mockRejectedValueOnce(new Error("LOST"));
    await expect(admitNvkeNative(options)).rejects.toThrow();
    const admit = vi.fn();
    await expect(
      admitNvkeRequest({
        ...options,
        mode: "native",
        values: { operationKey: "getCluster", timeoutSeconds: 7200, parametersText: "{}" },
        admit,
      })
    ).rejects.toThrow("RECOVERY_MISMATCH");
    expect(admit).not.toHaveBeenCalled();
  });
  it("blocks native resets from replacing an unresolved HTTP intent", async () => {
    const { options } = setup();
    saveRecovery(options.storage, {
      ...scope,
      format: 2,
      mode: "native",
      siteId,
      expectedSiteVersion: 7,
      operationKey: "getCluster",
      revision: options.revision,
      timeoutSeconds: 7200,
      requestId: opId,
      parametersDigest: "a".repeat(64),
      operationId: null,
    });
    await expect(admitNvkeNative(options)).rejects.toThrow("RECOVERY_MISMATCH");
    expect(options.admit).not.toHaveBeenCalled();
  });
  it.each(["storage", "lock"])("refuses admission without %s", async (kind) => {
    const { options } = setup();
    if (kind === "storage")
      options.storage.setItem = () => {
        throw new Error("BLOCKED");
      };
    else vi.stubGlobal("navigator", {});
    await expect(admitNvkeNative(options)).rejects.toThrow("STORAGE_UNAVAILABLE");
    expect(options.admit).not.toHaveBeenCalled();
  });
  it("freezes private review and metadata before caller state changes", async () => {
    const { options } = setup();
    options.onRecord.mockImplementation(() => {
      if (options.values.mode === "reset") options.values.review.rationale = "PRIVATE_CHANGED";
      options.source.version++;
      options.authorityVersion++;
      options.expectedSiteVersion++;
    });
    await admitNvkeNative(options);
    expect(options.admit.mock.calls[0][0]).toMatchObject({
      expectedSiteVersion: 7,
      expectedAuthorityVersion: 3,
      expectedSourceVersion: 9,
      review: { rationale: "PRIVATE_REASON" },
    });
  });
  it("preserves the journal after a stale actor visit finishes", async () => {
    const { options } = setup();
    let current = true;
    options.current = () => current;
    options.admit.mockImplementationOnce(async (input) => {
      current = false;
      return {
        ok: true,
        errors: [],
        data: {
          id: opId,
          siteId,
          requestId: input.requestId,
          operationKey: nativeOperationKey(reset),
          isInventory: false,
        },
      };
    });
    expect(await admitNvkeNative(options)).toBeNull();
    expect(readRecovery(options.storage, scope, siteId)).not.toBeNull();
  });
  it("refuses wrong receipts without archiving the pending intent", async () => {
    const { options } = setup();
    options.admit.mockResolvedValue({
      ok: true,
      errors: [],
      data: {
        id: opId,
        siteId,
        requestId: sourceId,
        operationKey: nativeOperationKey(reset),
        isInventory: false,
      },
    });
    await expect(admitNvkeNative(options)).rejects.toThrow("RECOVERY_MISMATCH");
    expect(readRecovery(options.storage, scope, siteId)).not.toBeNull();
  });
  it.each(["INVALID_INPUT", "VERSION_MISMATCH", "SURFACE_UNAVAILABLE"])(
    "archives a definitive first %s rejection",
    async (code) => {
      const { options } = setup();
      options.admit.mockResolvedValue({ ok: false, errors: [{ code }], data: null });
      await expect(admitNvkeNative(options)).rejects.toThrow("REQUEST_REJECTED");
      expect(readRecovery(options.storage, scope, siteId)).toBeNull();
    }
  );
  it("retains an unknown prior intent if input validation precedes receipt lookup", async () => {
    const { options } = setup();
    options.admit.mockRejectedValueOnce(new Error("LOST"));
    await expect(admitNvkeNative(options)).rejects.toThrow();
    options.admit.mockResolvedValue({ ok: false, errors: [{ code: "INVALID_INPUT" }], data: null });
    await expect(admitNvkeNative(options)).rejects.toThrow("REQUEST_FAILED");
    expect(readRecovery(options.storage, scope, siteId)).not.toBeNull();
  });
  it.each(["authorityVersion", "source", "mode", "operationKey"])(
    "rejects malformed native journal %s",
    async (field) => {
      const { options } = setup();
      options.admit.mockRejectedValueOnce(new Error("LOST"));
      await expect(admitNvkeNative(options)).rejects.toThrow();
      const record = readRecovery(options.storage, scope, siteId)!;
      const changed = {
        ...record,
        [field]:
          field === "authorityVersion"
            ? 0
            : field === "source"
              ? { operationId: sourceId, version: 9, resourceKey: "PRIVATE_TARGET" }
              : "bad",
      };
      expect(() => validateRecovery(changed, scope, siteId)).toThrow("INVALID_RECOVERY");
    }
  );
});

it("rechecks the stored identity immediately before native admission", async () => {
  const { options } = setup();
  options.onRecord.mockImplementation(() =>
    options.storage.removeItem(
      "astrolift.nvke.recovery.v1:" +
        JSON.stringify([scope.server, scope.organizationId, scope.actorId, siteId])
    )
  );
  await expect(admitNvkeNative(options)).rejects.toThrow("RECOVERY_MISMATCH");
  expect(options.admit).not.toHaveBeenCalled();
});
it("does not admit if the actor visit changes during journal publication", async () => {
  const { options } = setup();
  let current = true;
  options.current = () => current;
  options.onRecord.mockImplementation(() => {
    current = false;
  });
  expect(await admitNvkeNative(options)).toBeNull();
  expect(options.admit).not.toHaveBeenCalled();
  expect(readRecovery(options.storage, scope, siteId)).not.toBeNull();
});
it("requires a current source before a new durable intent", async () => {
  const { options } = setup();
  options.sourceAvailable = () => false;
  await expect(admitNvkeNative(options)).rejects.toThrow("SOURCE_CHANGED");
  expect(options.admit).not.toHaveBeenCalled();
  expect(readRecovery(options.storage, scope, siteId)).toBeNull();
});
it("keeps an uncertain record when receipt lookup is unavailable", async () => {
  const { options } = setup();
  options.admit.mockRejectedValueOnce(new Error("LOST"));
  await expect(admitNvkeNative(options)).rejects.toThrow();
  options.receipt.mockRejectedValue(new Error("UNAVAILABLE"));
  await expect(recoverNvkeNative(options)).rejects.toThrow("UNAVAILABLE");
  expect(options.admit).toHaveBeenCalledTimes(1);
  expect(readRecovery(options.storage, scope, siteId)).not.toBeNull();
});

it.each([reset, workflow])(
  "maps the client review into the exact generated mutation input",
  async (values) => {
    const { options } = setup(values);
    await admitNvkeNative(options);
    const mapped = nativeMutationInput(options.admit.mock.calls[0][0]);
    if (mapped.kind === "reset") {
      expect(mapped.input).toMatchObject({
        mode: "restart_rewrite",
        workflowTaskEventId: "9007199254740993",
        workflow: "RotateEtcdEncryptionKey",
      });
      expect(mapped.input).not.toHaveProperty("review");
    } else {
      expect(mapped.input).toMatchObject({
        parametersText: workflow.mode === "workflow" ? workflow.parametersText : "",
      });
      expect(mapped.input).not.toHaveProperty("mode");
    }
  }
);

it("accepts an identical typed review despite object field ordering", async () => {
  const { options } = setup();
  options.admit.mockRejectedValueOnce(new Error("LOST"));
  await expect(admitNvkeNative(options)).rejects.toThrow();
  if (options.values.mode !== "reset") throw Error("fixture");
  options.values.review = Object.fromEntries(
    Object.entries(options.values.review).reverse()
  ) as typeof options.values.review;
  await admitNvkeNative(options);
  expect(options.admit).toHaveBeenCalledTimes(2);
});

it.each(["PRECONDITION", "RESOURCE_BUSY", "UNSUPPORTED"])(
  "releases a definitively rejected new native %s intent",
  async (code) => {
    const { options } = setup();
    options.admit.mockResolvedValue({ ok: false, errors: [{ code }], data: null });
    await expect(admitNvkeNative(options)).rejects.toThrow("REQUEST_REJECTED");
    expect(readRecovery(options.storage, scope, siteId)).toBeNull();
  }
);
it.each(["PRECONDITION", "RESOURCE_BUSY", "UNSUPPORTED"])(
  "preserves prior unknown identity on later %s rejection",
  async (code) => {
    const { options } = setup();
    options.admit.mockRejectedValueOnce(new Error("LOST"));
    await expect(admitNvkeNative(options)).rejects.toThrow();
    const original = readRecovery(options.storage, scope, siteId);
    options.admit.mockResolvedValue({ ok: false, errors: [{ code }], data: null });
    await expect(admitNvkeNative(options)).rejects.toThrow("REQUEST_FAILED");
    expect(readRecovery(options.storage, scope, siteId)).toEqual(original);
  }
);
