import { LIST_PROVIDER_PLUGINS } from "@/graphql/clusters/clusters.queries";
import { PreloadQuery } from "@/lib/apollo";

import { DriversClient } from "./drivers-client";

export const metadata = {
  title: "Driver reference · Resources · Astrolift",
};

export default function DriversReferencePage() {
  return (
    <PreloadQuery query={LIST_PROVIDER_PLUGINS}>
      <DriversClient />
    </PreloadQuery>
  );
}
