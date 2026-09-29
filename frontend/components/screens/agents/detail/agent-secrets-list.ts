/**
 * The agent's Secrets tab, Values section, as a list (spec 44 §5.1; Leo's
 * list rule 4): each secret ref the agent's env spec binds, with its value's
 * status. `agentEnvironmentSpecSecretStatus` returns every ref at once, with
 * no filter, sort or paging; an agent binds a handful, so the hook reads the
 * set and this answers the rest exactly, with numbered pages. The view says
 * so. A ref has no owner, so there is no Mine. Pure.
 */
import type { SortState } from "@/components/data-table";
import type { ListDefinition } from "@/components/list/list-state";
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

/** The providers the refs use, in a stable order, for the filter. */
export function secretProviders(rows: AstroliftAgentSecretStatus[]): string[] {
  return [...new Set(rows.map((r) => r.provider).filter(Boolean))].sort();
}

export function agentSecretsList(providers: string[]): ListDefinition {
  return {
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
      {
        key: "provider",
        label: "Provider",
        options: providers.map((p) => ({ value: p, label: p })),
      },
    ],
    searchPlaceholder: "Search variables, URIs…",
    defaultSort: [{ key: "envVar", dir: "asc" }],
    views: [
      {
        key: "all",
        label: "All",
        filters: {},
        note: "Filtered, sorted and paged in the browser: the secret status read returns every ref at once.",
      },
    ],
    paging: "numbered",
    pageSizes: [25, 50, 100],
  };
}

const STATE_ORDER: Record<SecretState, number> = { error: 0, missing: 1, set: 2 };

/** Filter, search, sort and page the refs. Stable: ties break on the variable name. */
export function selectSecrets(
  all: AstroliftAgentSecretStatus[],
  filters: Record<string, string>,
  q: string,
  sort: SortState[],
  page: number,
  pageSize: number
): { rows: AstroliftAgentSecretStatus[]; totalCount: number } {
  const needle = q.trim().toLowerCase();
  const matched = all.filter((r) => {
    if (filters.status && secretState(r) !== filters.status) return false;
    if (filters.provider && r.provider !== filters.provider) return false;
    return (
      !needle || r.envVar.toLowerCase().includes(needle) || r.uri.toLowerCase().includes(needle)
    );
  });
  const sorted = [...matched].sort((a, b) => {
    for (const s of sort) {
      const c =
        s.key === "status"
          ? STATE_ORDER[secretState(a)] - STATE_ORDER[secretState(b)]
          : s.key === "provider"
            ? a.provider.localeCompare(b.provider)
            : a.envVar.localeCompare(b.envVar);
      if (c !== 0) return s.dir === "asc" ? c : -c;
    }
    return a.envVar.localeCompare(b.envVar);
  });
  const start = (Math.max(1, page) - 1) * pageSize;
  return { rows: sorted.slice(start, start + pageSize), totalCount: matched.length };
}
