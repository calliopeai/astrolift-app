import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { RequestNvkeOperatorJobInput } from "@/graphql/__generated__/operations";
import { admitNvkeOperatorJob, operatorEnvelopeMatches } from "./operator-admission";
import { recoverNvkeNative } from "./native-admission";
import {
  readRecovery,
  validateRecovery,
  type NvkeRecoveryOperation,
  type RecoveryStorage,
} from "./recovery";

const scope = {
  server: "https://astro.example.test/app/gql/config/",
  organizationId: "123e4567-e89b-12d3-a456-426614174000",
  actorId: 41,
};
const siteId = "123e4567-e89b-12d3-a456-426614174001",
  operationId = "123e4567-e89b-12d3-a456-426614174002",
  revision = "92c39b8ad563ebe5ef705f781b3038b901aba10f";
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
  const requestText = JSON.stringify(
    {
      format: 1,
      revision,
      siteId,
      command: "generate.sitespec",
      positionals: ["private-site"],
      options: { nodes: "input:private.csv" },
      files: { "private.csv": "private-node,18446744073709551615\n" },
    },
    null,
    2
  );
  const options = {
    scope: { ...scope },
    siteId,
    expectedSiteVersion: 7,
    expectedAuthorityVersion: 9,
    revision,
    values: {
      operationKey: "tooling.generate.sitespec",
      requestText,
      timeoutSeconds: 600,
      acknowledgeInfrastructureChange: false,
    },
    storage,
    current: () => true,
    sourceAvailable: () => true,
    onRecord: vi.fn(),
    receipt: vi.fn(async (_requestId: string): Promise<NvkeRecoveryOperation | null> => null),
    admit: vi.fn(async (input: RequestNvkeOperatorJobInput) => ({
      ok: true,
      errors: [] as { code: string }[],
      data: {
        id: operationId,
        siteId,
        requestId: input.requestId,
        operationKey: "tooling.generate.sitespec",
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

it("flushes only reviewed metadata before sending the exact original private UTF-8 text", async () => {
  const { rows, options } = setup(),
    original = options.admit.getMockImplementation()!;
  options.admit.mockImplementation(async (input) => {
    expect(readRecovery(options.storage, scope, siteId)).toMatchObject({
      format: 7,
      mode: "tooling",
      authorityVersion: 9,
      acknowledgeInfrastructureChange: false,
      requestId: input.requestId,
    });
    expect(input).toEqual({
      siteId,
      expectedActorId: 41,
      expectedSiteVersion: 7,
      expectedAuthorityVersion: 9,
      requestId: input.requestId,
      requestText: options.values.requestText,
      timeoutSeconds: 600,
      acknowledgeInfrastructureChange: false,
    });
    expect([...rows.values()].join()).not.toMatch(
      /private-site|private-node|private.csv|18446744073709551615/
    );
    return original(input);
  });
  expect((await admitNvkeOperatorJob(options))?.id).toBe(operationId);
  expect(readRecovery(options.storage, scope, siteId)).toBeNull();
  expect(rows.size).toBe(1);
});

it("recovers the original receipt before evaluating lost input or changed site/authority", async () => {
  const { options } = setup(),
    original = options.admit.getMockImplementation()!;
  let accepted: NvkeRecoveryOperation | null = null;
  options.admit.mockImplementation(async (input) => {
    accepted = (await original(input)).data;
    throw new Error("Private diagnostics must not enter the recovery record");
  });
  await expect(admitNvkeOperatorJob(options)).rejects.toThrow();
  options.receipt.mockImplementation(async () => accepted);
  options.values.requestText = "";
  options.expectedSiteVersion = 99;
  options.expectedAuthorityVersion = 99;
  options.sourceAvailable = () => false;
  expect((await admitNvkeOperatorJob(options))?.id).toBe(operationId);
  expect(options.admit).toHaveBeenCalledTimes(1);
});

it.each(["input", "site", "authority", "revision", "timeout", "acknowledgement", "command"])(
  "refuses changed %s after a lost reply with no original receipt",
  async (field) => {
    const { options } = setup();
    options.admit.mockRejectedValue(new Error("lost"));
    await expect(admitNvkeOperatorJob(options)).rejects.toThrow();
    const saved = readRecovery(options.storage, scope, siteId);
    if (field === "input") options.values.requestText += " ";
    if (field === "site") options.expectedSiteVersion++;
    if (field === "authority") options.expectedAuthorityVersion++;
    if (field === "revision") options.revision = "a".repeat(40);
    if (field === "timeout") options.values.timeoutSeconds++;
    if (field === "acknowledgement") options.values.acknowledgeInfrastructureChange = true;
    if (field === "command") options.values.operationKey = "tooling.version";
    await expect(admitNvkeOperatorJob(options)).rejects.toThrow();
    expect(options.receipt).toHaveBeenCalledWith(saved!.requestId);
    expect(options.admit).toHaveBeenCalledTimes(1);
    expect(readRecovery(options.storage, scope, siteId)).toEqual(saved);
  }
);

it("explicit identical resubmission retains its UUID, original text and versions", async () => {
  const { options } = setup(),
    original = options.admit.getMockImplementation()!;
  options.admit.mockRejectedValueOnce(new Error("lost"));
  await expect(admitNvkeOperatorJob(options)).rejects.toThrow();
  const saved = readRecovery(options.storage, scope, siteId);
  options.admit.mockImplementation(original);
  await admitNvkeOperatorJob(options);
  expect(options.admit.mock.calls[1][0]).toEqual(options.admit.mock.calls[0][0]);
  expect(options.admit.mock.calls[1][0].requestId).toBe(saved!.requestId);
});

it("read-only recovery never admits even if an original receipt is unavailable", async () => {
  const { options } = setup();
  options.admit.mockRejectedValue(new Error("lost"));
  await expect(admitNvkeOperatorJob(options)).rejects.toThrow();
  await expect(recoverNvkeNative(options)).rejects.toThrow("REQUEST_FAILED");
  expect(options.admit).toHaveBeenCalledTimes(1);
  expect(readRecovery(options.storage, scope, siteId)?.format).toBe(7);
});

it.each(["CONFLICT", "SOURCE_CHANGED", "VERSION_MISMATCH", "INVALID_INPUT", "PRECONDITION"])(
  "retains original tooling metadata after %s refusal",
  async (code) => {
    const { options } = setup();
    options.admit.mockRejectedValueOnce(new Error("lost"));
    await expect(admitNvkeOperatorJob(options)).rejects.toThrow("lost");
    options.admit.mockResolvedValue({ ok: false, data: null, errors: [{ code }] });
    await expect(admitNvkeOperatorJob(options)).rejects.toThrow("REQUEST_FAILED");
    expect(readRecovery(options.storage, scope, siteId)).toMatchObject({ format: 7 });
  }
);

it.each(["INVALID_INPUT", "VERSION_MISMATCH", "SURFACE_UNAVAILABLE"])(
  "archives a definitive first %s refusal without private input",
  async (code) => {
    const { options, rows } = setup();
    options.admit.mockResolvedValue({ ok: false, data: null, errors: [{ code }] });
    await expect(admitNvkeOperatorJob(options)).rejects.toThrow("REQUEST_REJECTED");
    expect(readRecovery(options.storage, scope, siteId)).toBeNull();
    expect([...rows.values()].join()).not.toMatch(/private-site|private-node|private.csv/);
  }
);

it("cannot send when journal write verification fails", async () => {
  const { options } = setup();
  options.storage.setItem = () => {};
  await expect(admitNvkeOperatorJob(options)).rejects.toThrow("STORAGE_UNAVAILABLE");
  expect(options.admit).not.toHaveBeenCalled();
});
it("cannot send when the caller changes while metadata is published", async () => {
  const { options } = setup();
  let active = true;
  options.current = () => active;
  options.onRecord.mockImplementation(() => {
    active = false;
  });
  expect(await admitNvkeOperatorJob(options)).toBeNull();
  expect(options.admit).not.toHaveBeenCalled();
});
it("freezes values before asynchronous receipt/digest work", async () => {
  const { options } = setup(),
    text = options.values.requestText;
  const pending = admitNvkeOperatorJob(options);
  options.values.requestText = "changed";
  await pending;
  expect(options.admit.mock.calls[0][0].requestText).toBe(text);
});
it("does not archive a receipt belonging to another command or carrying a resource source", async () => {
  const { options } = setup(),
    original = options.admit.getMockImplementation()!;
  options.admit.mockImplementation(async (input) => {
    const reply = await original(input);
    return { ...reply, data: { ...reply.data!, operationKey: "tooling.version" } };
  });
  await expect(admitNvkeOperatorJob(options)).rejects.toThrow("RECOVERY_MISMATCH");
  expect(readRecovery(options.storage, scope, siteId)).not.toBeNull();
});
it.each([
  "tooling.generate.oidc-kubeconfig",
  "tooling.inspect.stack-config",
  "tooling.render.management-controller",
])("round-trips hyphenated tooling keys such as %s", async (operationKey) => {
  const { options } = setup();
  options.admit.mockRejectedValue(new Error("lost"));
  options.values.operationKey = operationKey;
  options.values.requestText = options.values.requestText.replace(
    "generate.sitespec",
    operationKey.slice(8)
  );
  await expect(admitNvkeOperatorJob(options)).rejects.toThrow("lost");
  const saved = readRecovery(options.storage, scope, siteId)!;
  expect(validateRecovery(saved, scope, siteId).operationKey).toBe(operationKey);
  for (const patch of [
    { authorityVersion: 0 },
    { acknowledgeInfrastructureChange: "true" },
    { mode: "native" },
    { privateInput: "secret" },
  ])
    expect(() => validateRecovery({ ...saved, ...patch }, scope, siteId)).toThrow(
      "INVALID_RECOVERY"
    );
});
it("validates the UTF-8 envelope without reserializing large native JSON integers", () => {
  const { options } = setup();
  const text = options.values.requestText.replace('"input:private.csv"', "18446744073709551615");
  expect(operatorEnvelopeMatches(text, options.values.operationKey, siteId, revision)).toBe(true);
  for (const bad of [
    "{}",
    "null",
    "[]",
    "{",
    text + "\ud800",
    " ".repeat(1048577),
    text.replace('"files": {', '"files": ['),
  ])
    expect(operatorEnvelopeMatches(bad, options.values.operationKey, siteId, revision)).toBe(false);
});
