import { InstancesClient } from "./instances-client";

export const metadata = { title: "Platform instances · Workflows · Astrolift" };

/** Agents › Workflows › Platform instances: the Temporal instances, their own page (rule 3). */
export default function WorkflowInstancesPage() {
  return <InstancesClient />;
}
