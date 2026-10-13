import { routeTitle } from "@/lib/i18n/route-title";

import { OverviewContent } from "./components/overview-content";

export const generateMetadata = routeTitle("overview", "agent");

/** The agent's Overview tab, at the agent's root (spec 44 §5.2); `/overview` redirects here. */
export default function AgentOverviewPage() {
  return <OverviewContent />;
}
