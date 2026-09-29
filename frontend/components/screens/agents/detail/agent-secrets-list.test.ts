import { describe, expect, it } from "vitest";

import type { AstroliftAgentSecretStatus } from "@/graphql/agents/agents.types";

import { secretProviders, secretState, selectSecrets } from "./agent-secrets-list";

const ref = (envVar: string, over: Partial<AstroliftAgentSecretStatus> = {}) =>
  ({
    envVar,
    uri: `agents/org-1/${envVar.toLowerCase()}`,
    exists: true,
    error: null,
    provider: "aws-secrets-manager",
    canReveal: true,
    readLimitation: null,
    ...over,
  }) as AstroliftAgentSecretStatus;

const ROWS = [
  ref("OPENAI_KEY"),
  ref("CRM_TOKEN", { exists: false }),
  ref("SLACK_TOKEN", { error: "access denied", provider: "vault" }),
];

describe("secretState", () => {
  it("reads an error first, then whether a value exists", () => {
    expect(ROWS.map(secretState)).toEqual(["set", "missing", "error"]);
  });
});

describe("selectSecrets", () => {
  const asc = [{ key: "envVar", dir: "asc" as const }];

  it("sorts by variable name by default", () => {
    expect(selectSecrets(ROWS, {}, "", asc, 1, 25).rows.map((r) => r.envVar)).toEqual([
      "CRM_TOKEN",
      "OPENAI_KEY",
      "SLACK_TOKEN",
    ]);
  });

  it("puts errors first when sorted by status", () => {
    const sorted = selectSecrets(ROWS, {}, "", [{ key: "status", dir: "asc" }], 1, 25);
    expect(sorted.rows.map((r) => r.envVar)).toEqual(["SLACK_TOKEN", "CRM_TOKEN", "OPENAI_KEY"]);
  });

  it("filters by status and provider, searches name and URI, and counts before paging", () => {
    expect(selectSecrets(ROWS, { status: "missing" }, "", asc, 1, 25).totalCount).toBe(1);
    expect(selectSecrets(ROWS, { provider: "vault" }, "", asc, 1, 25).rows[0]?.envVar).toBe(
      "SLACK_TOKEN"
    );
    expect(selectSecrets(ROWS, {}, "openai_key", asc, 1, 25).totalCount).toBe(1);
    const paged = selectSecrets(ROWS, {}, "", asc, 2, 2);
    expect(paged).toMatchObject({ totalCount: 3, rows: [{ envVar: "SLACK_TOKEN" }] });
  });

  it("lists each provider once, sorted", () => {
    expect(secretProviders(ROWS)).toEqual(["aws-secrets-manager", "vault"]);
  });
});
