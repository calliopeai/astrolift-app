import { routeTitle } from "@/lib/i18n/route-title";

import { ModelsClient } from "./models-client";
export const generateMetadata = routeTitle("models");
/** Shared cluster deployments lead; existing app/project endpoints have a separate view. */
export default function ModelsPage() {
  return <ModelsClient />;
}
