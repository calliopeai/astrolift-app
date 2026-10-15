/**
 * Connect a client to a brain (#2320): the MCP client config for one issued
 * read token. A brain serves MCP at `/mcp` on each of its endpoints; the
 * cluster edge admits the token in its own header. Pure.
 */
import type { IssueBrainReadTokenMutation } from "@/graphql/__generated__/operations";

export type BrainConnection = NonNullable<
  IssueBrainReadTokenMutation["issueBrainReadToken"]["data"]
>;

export function brainMcpUrl(endpoint: string): string {
  return `${endpoint.replace(/\/+$/, "")}/mcp`;
}

/** An `mcpServers` block, one server per endpoint, the token in its header. */
export function brainMcpConfig(connection: BrainConnection): string {
  const servers = Object.fromEntries(
    connection.endpoints.map((endpoint, i) => [
      i === 0 ? connection.slug : `${connection.slug}-${i + 1}`,
      {
        type: "http",
        url: brainMcpUrl(endpoint),
        headers: { [connection.header]: connection.token },
      },
    ])
  );
  return JSON.stringify({ mcpServers: servers }, null, 2);
}
