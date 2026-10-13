import { routeTitle } from "@/lib/i18n/route-title";

import { InstancesClient } from "../workflows/instances/instances-client";

export const generateMetadata = routeTitle("platformActivity");

export default function PlatformActivityPage() {
  return <InstancesClient platformActivity />;
}
