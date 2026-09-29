/**
 * Agent boxes (spec 44 §5.1): the list declaration and its client-side step.
 * `agentBoxes` returns the org's boxes at once (capped at 200, newest
 * first) with no filter, search, sort or page arguments, so search, the
 * status filter, sort and numbered pages run in the client (needsBackend: a
 * Page field). Every box is an agent box, so the list has no Agents,
 * Workflows or Functions views. A box's row carries its owner's email but
 * not whether that is the viewer, so Mine is empty until it does. Pure.
 */
import type { SelectRowsSpec } from "@/components/list/select-rows";
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import type { AstroliftAgentBox } from "@/graphql/agents/agents.types";

export const AGENT_BOXES_LIST: ListDefinition = {
  id: "agents.boxes",
  fields: [
    {
      key: "status",
      label: "Status",
      options: ["pending", "provisioning", "running", "stopped", "expired", "failed"].map((s) => ({
        value: s,
        label: s,
      })),
    },
  ],
  searchPlaceholder: "Search boxes, slugs, agents…",
  defaultSort: [{ key: "created", dir: "desc" }],
  views: standardViews({ owner: "me" }, [], {
    mineNote:
      "Agent boxes do not say yet whether you started them, so Mine is empty. Every box is in All.",
  }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

const time = (iso: string | null | undefined) => (iso ? Date.parse(iso) : 0);

export const AGENT_BOXES_SELECT: SelectRowsSpec<AstroliftAgentBox> = {
  filter: {
    owner: () => false,
    status: (b, v) => b.status === v,
  },
  text: (b) => [b.name, b.slug, b.agentSlug, b.environmentSpecSlug, b.ownerEmail],
  sort: {
    name: (b) => b.name.toLowerCase(),
    status: (b) => b.status,
    // Never (0) holds the node longest, so it sorts after every timeout.
    idle: (b) => b.idleTimeoutSeconds || Number.POSITIVE_INFINITY,
    started: (b) => time(b.startedAt),
    created: (b) => time(b.createdAt),
  },
  id: (b) => b.id,
};
