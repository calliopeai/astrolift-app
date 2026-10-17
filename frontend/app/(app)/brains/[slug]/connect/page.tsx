import { routeTitle } from "@/lib/i18n/route-title";

import { ConnectContent } from "../components/connect-content";

export const generateMetadata = routeTitle("connect", "semanticLayer");

/** Connect: MCP endpoints and a read token issued on request. */
export default function BrainConnectPage() {
  return <ConnectContent />;
}
