import { routeTitle } from "@/lib/i18n/route-title";

import { RunContent } from "../components/run-content";

export const generateMetadata = routeTitle("runs", "agent");

/** The Runs tab: this agent's executions, polled (spec 44 §5.2); `/run` redirects here. */
export default function AgentRunsPage() {
  return <RunContent />;
}
