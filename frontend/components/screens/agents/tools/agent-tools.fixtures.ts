import type { ToolDetailScreenProps } from "./ToolDetailScreen";
import type { ToolRegistryScreenProps } from "./ToolRegistryScreen";
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
  },
  {
    id: "tool-2",
    name: "Summarize ticket",
    slug: "summarize-ticket",
    description: "",
    adapter: "python_fn",
    handlerRef: "support.tools.summarize",
    createdAt: "2026-09-02T12:00:00Z",
  },
  {
    id: "tool-3",
    name: "Create invoice",
    slug: "create-invoice",
    description: "POST an invoice draft to the billing service.",
    adapter: "http_endpoint",
    handlerRef: "https://billing.example.com/tools/invoice",
    createdAt: "2026-09-03T12:00:00Z",
  },
  {
    id: "tool-4",
    name: "Search docs",
    slug: "search-docs",
    description: "Search the internal knowledge base.",
    adapter: "mcp_server",
    handlerRef: "mcp://docs.internal:8080",
    createdAt: "2026-09-04T12:00:00Z",
  },
  {
    id: "tool-5",
    name: "Legacy tool",
    slug: "legacy-tool",
    description: "No adapter recorded.",
    adapter: "",
    handlerRef: "",
    createdAt: "2026-09-05T12:00:00Z",
  },
];

export const REGISTRY: ToolRegistryScreenProps = {
  tools: TOOLS,
  loading: false,
  error: null,
};

export const LONG_TOOLS: ToolRegistryTool[] = [
  {
    id: "tool-long",
    name: `Tool ${LONG}`,
    slug: LONG,
    description: `A description that goes on and on ${LONG} ${LONG}`,
    adapter: "http_endpoint",
    handlerRef: `https://${LONG}.example.com/${LONG}`,
    createdAt: "2026-09-01T12:00:00Z",
  },
];

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
  saving: false,
  deleting: false,
  save: async () => ({ ok: true, schemaError: null }),
  remove: noopAsync,
};
