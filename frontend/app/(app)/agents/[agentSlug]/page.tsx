import { OverviewContent } from "./components/overview-content";

export const metadata = { title: "Overview · Agent · Astrolift" };

/** The agent's Overview tab, at the agent's root (spec 44 §5.2); `/overview` redirects here. */
export default function AgentOverviewPage() {
  return <OverviewContent />;
}
