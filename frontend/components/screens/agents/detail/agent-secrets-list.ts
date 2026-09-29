/**
 * The agent's Secrets tab, Values section, as a list (spec 44 §5.1; Leo's
 * list rule 4): each secret ref the agent's env spec binds, with its value's
 * status. The server probes the secret store and answers the chips, search,
 * sort and page (`agentEnvironmentSpecSecretStatusPage`, #2155). A ref has
 * no owner, so there is no Mine. Pure.
 */
import type { ListDefinition } from "@/components/list/list-state";
import {
  type NumberedListQuery,
  numberedPageVariables,
} from "@/components/screens/agents/skills/catalog";
import type { AstroliftAgentSecretStatus } from "@/graphql/agents/agents.types";

export type SecretState = "set" | "missing" | "error";

export function secretState(r: AstroliftAgentSecretStatus): SecretState {
  return r.error ? "error" : r.exists ? "set" : "missing";
}

export const SECRET_STATE_LABEL: Record<SecretState, string> = {
  set: "Set",
  missing: "Missing",
  error: "Error",
};

export const AGENT_SECRETS_LIST: ListDefinition = {
  id: "agents.detail.secrets",
  fields: [
    {
      key: "status",
      label: "Status",
      options: (Object.keys(SECRET_STATE_LABEL) as SecretState[]).map((s) => ({
        value: s,
        label: SECRET_STATE_LABEL[s],
      })),
    },
    // Free text: the secret store provider (vault, aws-secrets-manager, …).
    { key: "provider", label: "Provider" },
  ],
  searchPlaceholder: "Search variables, URIs…",
  defaultSort: [{ key: "envVar", dir: "asc" }],
  views: [{ key: "all", label: "All", filters: {} }],
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export interface AgentSecretStatusFilter {
  exists?: boolean;
  failing?: boolean;
  provider?: string[];
}

/**
 * The status chip as the server's two flags: an error is `failing`; set and
 * missing are the ones that did not fail, with and without a value.
 */
const STATUS_FILTER: Record<SecretState, AgentSecretStatusFilter> = {
  set: { exists: true, failing: false },
  missing: { exists: false, failing: false },
  error: { failing: true },
};

/** The list state as `agentEnvironmentSpecSecretStatusPage` variables (plus `slug`). */
export function agentSecretsPageVariables(q: NumberedListQuery) {
  const f = q.filters;
  const filter: AgentSecretStatusFilter = {
    ...(STATUS_FILTER[f.status as SecretState] ?? {}),
  };
  if (f.provider) filter.provider = [f.provider];
  return numberedPageVariables(q, filter, AGENT_SECRETS_LIST.defaultSort);
}
