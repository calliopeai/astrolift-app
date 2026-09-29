import { describe, expect, it } from "vitest";

import type { AstroliftAgentSecretStatus } from "@/graphql/agents/agents.types";

import { effectiveFilters, parseListState } from "@/components/list/list-state";

import { AGENT_SECRETS_LIST, agentSecretsPageVariables, secretState } from "./agent-secrets-list";

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

function forQuery(qs: string) {
  const state = parseListState(AGENT_SECRETS_LIST, qs);
  return agentSecretsPageVariables({
    q: state.q,
    filters: effectiveFilters(AGENT_SECRETS_LIST, state),
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });
}

describe("agentSecretsPageVariables", () => {
  it("asks for page 1 by variable name with no filter on a cold load", () => {
    expect(forQuery("")).toEqual({
      search: null,
      filter: null,
      sort: "envVar",
      page: 1,
      pageSize: 25,
    });
  });

  it("sends each status as the server's exists and failing flags", () => {
    expect(forQuery("status=set").filter).toEqual({ exists: true, failing: false });
    expect(forQuery("status=missing").filter).toEqual({ exists: false, failing: false });
    expect(forQuery("status=error").filter).toEqual({ failing: true });
  });

  it("sends the provider chip, search, sort and page through", () => {
    expect(forQuery("provider=vault&q=%20openai%20&sort=-exists&page=2")).toEqual({
      search: "openai",
      filter: { provider: ["vault"] },
      sort: "-exists",
      page: 2,
      pageSize: 25,
    });
  });

  it("matches the set, missing and error rows the status chips name", () => {
    // Each chip's flags pick out exactly the rows secretState gives that state.
    const pick = (f: { exists?: boolean; failing?: boolean }) =>
      ROWS.filter(
        (r) =>
          (f.exists === undefined || r.exists === f.exists) &&
          (f.failing === undefined || Boolean(r.error) === f.failing)
      ).map(secretState);
    expect(pick(forQuery("status=set").filter!)).toEqual(["set"]);
    expect(pick(forQuery("status=missing").filter!)).toEqual(["missing"]);
    expect(pick(forQuery("status=error").filter!)).toEqual(["error"]);
  });
});
