import { routeTitle } from "@/lib/i18n/route-title";

import { BrainsClient } from "./brains-client";

export const generateMetadata = routeTitle("semanticLayers");

/**
 * Semantic layers: the hosted brain apps (`[app] kind = "mimwell"`) the
 * viewer can read (#2320), with the same visibility, team shares and token
 * ceiling as apps. Agents ask the same question through the
 * `astrolift_list_brains` MCP tool.
 */
export default function BrainsPage() {
  return <BrainsClient />;
}
