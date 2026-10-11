import { BrainsClient } from "./brains-client";

export const metadata = { title: "Semantic layers · Astrolift" };

/**
 * Semantic layers: the hosted brain apps (`[app] kind = "mimwell"`) the
 * viewer can read (#2320), with the same visibility, team shares and token
 * ceiling as apps. Agents ask the same question through the
 * `astrolift_list_brains` MCP tool.
 */
export default function BrainsPage() {
  return <BrainsClient />;
}
