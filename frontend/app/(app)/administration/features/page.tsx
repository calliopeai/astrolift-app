import { ADMIN_FEATURE_INVENTORY } from "@/graphql/server/server.queries";
import { PreloadQuery } from "@/lib/apollo";
import { routeTitle } from "@/lib/i18n/route-title";

import { FeaturesClient } from "./features-client";

export const generateMetadata = routeTitle("features");

export default function AdministrationFeaturesPage() {
  return (
    <PreloadQuery query={ADMIN_FEATURE_INVENTORY}>
      <FeaturesClient />
    </PreloadQuery>
  );
}
