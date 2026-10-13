import { routeTitle } from "@/lib/i18n/route-title";

import { WorkloadsClient } from "./workloads-client";

export const generateMetadata = routeTitle("workloads");

/**
 * Agents › Workloads (spec 44 §4.1, §10.2): the runtime units behind
 * agents, workflows and functions, in one list.
 */
export default function WorkloadsPage() {
  return <WorkloadsClient />;
}
