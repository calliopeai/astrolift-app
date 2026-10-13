import { routeTitle } from "@/lib/i18n/route-title";

import { StartDeploymentClient } from "./start-deployment-client";

export const generateMetadata = routeTitle("startDeployment");

/** The start flow: a page in steps, since it asks for more than three fields (spec 44 §5.4). */
export default function StartDeploymentRoute() {
  return <StartDeploymentClient />;
}
