import type { SortState } from "@/components/data-table";
import { ARN200, LONG_URL, SHA64 } from "@/components/screens/agents/skills/agent-skills.fixtures";
import { lower, selectPage, time } from "@/components/screens/agents/skills/catalog";

import type { ToolDetailScreenProps } from "./ToolDetailScreen";
import type { ToolDetailTool } from "./use-tool-detail";
import type { ToolRegistryTool } from "./use-tool-registry";

/** Hand-typed fixtures for the Agents › Tools screens (group agent-tools). */

const LONG =
  "an-extremely-long-tool-identifier-that-keeps-going-well-past-any-reasonable-width-to-test-truncation";

export const TOOLS: ToolRegistryTool[] = [
  {
    id: "tool-1",
    name: "Lookup customer",
    slug: "lookup-customer",
    description: "Fetch a customer record by email or account id.",
    adapter: "python_fn",
    handlerRef: "crm.tools.lookup_customer",
    createdAt: "2026-09-01T12:00:00Z",
    isBuiltin: false,
    skillId: "sk-crm",
    skillSlug: "crm",
    skillName: "CRM",
    skillIsGlobal: false,
    createdByEmail: "leo@example.com",
    createdByMe: true,
  },
  {
    id: "tool-2",
    name: "Summarize ticket",
    slug: "summarize-ticket",
    description: "",
    adapter: "python_fn",
    handlerRef: "support.tools.summarize",
    createdAt: "2026-09-02T12:00:00Z",
    isBuiltin: false,
    skillId: "sk-support",
    skillSlug: "support",
    skillName: "Support",
    skillIsGlobal: false,
    createdByEmail: "ops@example.com",
    createdByMe: false,
  },
  {
    id: "tool-3",
    name: "Create invoice",
    slug: "create-invoice",
    description: "POST an invoice draft to the billing service.",
    adapter: "http_endpoint",
    handlerRef: "https://billing.example.com/tools/invoice",
    createdAt: "2026-09-03T12:00:00Z",
    isBuiltin: false,
    skillId: "sk-billing",
    skillSlug: "billing",
    skillName: "Billing",
    skillIsGlobal: false,
    createdByEmail: "leo@example.com",
    createdByMe: true,
  },
  {
    id: "tool-4",
    name: "Search docs",
    slug: "search-docs",
    description: "Search the internal knowledge base.",
    adapter: "mcp_server",
    handlerRef: "mcp://docs.internal:8080",
    createdAt: "2026-09-04T12:00:00Z",
    isBuiltin: true,
    skillId: "sk-platform-tools",
    skillSlug: "platform-tools",
    skillName: "Platform tools",
    skillIsGlobal: true,
    createdByEmail: "",
    createdByMe: false,
  },
  {
    id: "tool-5",
    name: "Legacy tool",
    slug: "legacy-tool",
    description: "No adapter recorded.",
    adapter: "",
    handlerRef: "",
    createdAt: "2026-09-05T12:00:00Z",
    isBuiltin: false,
    skillId: "sk-legacy",
    skillSlug: "legacy",
    skillName: "Legacy",
    skillIsGlobal: false,
    createdByEmail: "",
    createdByMe: false,
  },
];

/** Sixty tools across the adapters: numbered pages. */
export const MANY_TOOLS: ToolRegistryTool[] = Array.from({ length: 60 }, (_, i) => ({
  ...TOOLS[i % 4],
  id: `tool-many-${i}`,
  name: `Tool ${String(i + 1).padStart(2, "0")}`,
  slug: `tool-${i + 1}`,
  createdAt: new Date(Date.UTC(2026, 8, 28, 12) - i * 3_600_000).toISOString(),
}));

export const LONG_TOOLS: ToolRegistryTool[] = [
  {
    id: "tool-long",
    name: `Tool ${LONG}`,
    slug: LONG,
    description: `A description that goes on and on ${LONG} ${LONG}`,
    adapter: "http_endpoint",
    handlerRef: `https://${LONG}.example.com/${LONG}`,
    createdAt: "2026-09-01T12:00:00Z",
    isBuiltin: false,
    skillId: "sk-a-skill-with-a-long-slug-for-the-skill-column-that-keeps-going",
    skillSlug: "a-skill-with-a-long-slug-for-the-skill-column-that-keeps-going",
    skillName: "A skill with a long name for the skill column that keeps going",
    skillIsGlobal: false,
    createdByEmail: "a.person.with.a.very.long.email.address@example.com",
    createdByMe: false,
  },
  {
    id: "tool-sha",
    name: SHA64,
    slug: SHA64,
    description: ARN200,
    adapter: "mcp_server",
    handlerRef: LONG_URL,
    createdAt: "2026-09-02T12:00:00Z",
    isBuiltin: true,
    skillId: "sk-platform-tools",
    skillSlug: "platform-tools",
    skillName: "Platform tools",
    skillIsGlobal: true,
    createdByEmail: "",
    createdByMe: false,
  },
];

/**
 * A stand-in for `orgToolDefsPage` in stories: the fixture tools filtered,
 * searched, sorted and sliced the way the server answers the list state.
 */
export function serveTools(
  tools: ToolRegistryTool[],
  q: {
    filters: Record<string, string>;
    q: string;
    sort: SortState[];
    page: number;
    pageSize: number;
  }
) {
  return selectPage(
    tools,
    {
      matches: (t, f) =>
        (!f.createdBy || t.createdByMe) &&
        (!f.builtin || t.isBuiltin === (f.builtin === "1")) &&
        (!f.adapter || t.adapter === f.adapter) &&
        (!f.skill || lower(t.skillSlug) === lower(f.skill)),
      text: (t) => [t.name, t.slug, t.description, t.handlerRef, t.skillSlug],
      sortValue: {
        name: (t) => lower(t.name),
        skill: (t) => lower(t.skillSlug),
        adapter: (t) => t.adapter,
        created: (t) => time(t.createdAt),
      },
      id: (t) => t.id,
    },
    q
  );
}

const noopAsync = async () => {};

export const TOOL: ToolDetailTool = {
  id: "tool-1",
  name: "Lookup customer",
  slug: "lookup-customer",
  description: "Fetch a customer record by email or account id.",
  adapter: "python_fn",
  handlerRef: "crm.tools.lookup_customer",
  inputSchema: {
    type: "object",
    properties: { email: { type: "string" } },
    required: ["email"],
  },
  outputSchema: { type: "object", properties: { id: { type: "string" } } },
  createdAt: "2026-09-01T12:00:00Z",
};

export const LONG_TOOL: ToolDetailTool = {
  ...TOOL,
  id: "tool-long",
  name: `Tool ${LONG}`,
  slug: LONG,
  description: `A description that goes on and on ${LONG}`,
  adapter: "http_endpoint",
  handlerRef: `https://${LONG}.example.com/${LONG}`,
  inputSchema: { type: "object", properties: { [LONG]: { type: "string", description: LONG } } },
};

export const DETAIL: ToolDetailScreenProps = {
  tool: TOOL,
  loading: false,
  error: null,
  onRetry: () => {},
  saving: false,
  deleting: false,
  save: async () => ({}),
  remove: noopAsync,
};
