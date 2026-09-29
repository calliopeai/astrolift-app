export type Adapter = "python_fn" | "http_endpoint" | "mcp_server";

export const ADAPTERS: { value: Adapter; label: string }[] = [
  { value: "python_fn", label: "Python function" },
  { value: "http_endpoint", label: "HTTP endpoint" },
  { value: "mcp_server", label: "MCP server" },
];

/** Adapter label by value; an unknown adapter shows as itself. */
export const ADAPTER_LABEL: Record<string, string> = Object.fromEntries(
  ADAPTERS.map((a) => [a.value, a.label])
);

/** The handler ref's shape for each adapter, as the field's placeholder. */
export const HANDLER_PLACEHOLDER: Record<Adapter, string> = {
  python_fn: "module.function",
  http_endpoint: "https://example.com/tool",
  mcp_server: "mcp://server-address",
};
