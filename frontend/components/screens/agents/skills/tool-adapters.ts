export type Adapter = "python_fn" | "http_endpoint" | "mcp_server";

export const ADAPTERS: { value: Adapter; label: string }[] = [
  { value: "python_fn", label: "Python function" },
  { value: "http_endpoint", label: "HTTP endpoint" },
  { value: "mcp_server", label: "MCP server" },
];
