import { routeTitle } from "@/lib/i18n/route-title";

import { InstancesClient } from "./instances-client";

export const generateMetadata = routeTitle("platformInstances", "workflows");

/** Agents › Workflows › Platform instances: the Temporal instances, their own page (rule 3). */
export default function WorkflowInstancesPage() {
  return <InstancesClient />;
}
