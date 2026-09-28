import { RunsClient } from "./runs-client";

export const metadata = { title: "Runs · Astrolift" };

/**
 * Admin › Usage & governance › Runs: the combined run audit (spec 44 §4.4,
 * decision 14). No `PreloadQuery`: the list merges four source queries
 * whose variables follow the kind chip and the viewer's modules, so a
 * server preload could not name the cache entries the client reads.
 */
export default function RunsPage() {
  return <RunsClient />;
}
