import { ADMIN_FEATURE_INVENTORY } from "@/graphql/server/server.queries";
import { PreloadQuery } from "@/lib/apollo";

import { FeaturesClient } from "./features-client";

export const metadata = { title: "Features · Astrolift" };

export default function AdministrationFeaturesPage() {
  return (
    <PreloadQuery query={ADMIN_FEATURE_INVENTORY}>
      <FeaturesClient />
    </PreloadQuery>
  );
}
