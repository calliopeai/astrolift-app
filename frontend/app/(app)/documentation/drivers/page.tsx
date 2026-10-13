import { LIST_PROVIDER_PLUGINS } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { DriversClient } from "./drivers-client";

export const generateMetadata = routeTitle("driverReference", "documentation");

export default function DriversReferencePage() {
  return (
    <PreloadQuery query={LIST_PROVIDER_PLUGINS}>
      <DriversClient />
    </PreloadQuery>
  );
}
