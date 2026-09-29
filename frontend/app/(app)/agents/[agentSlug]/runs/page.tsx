import { RunContent } from "../components/run-content";

export const metadata = { title: "Runs · Agent · Astrolift" };

/** The Runs tab: this agent's executions, polled (spec 44 §5.2); `/run` redirects here. */
export default function AgentRunsPage() {
  return <RunContent />;
}
