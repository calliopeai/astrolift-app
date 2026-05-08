import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { PreloadQuery } from "@/lib/apollo";

import { DeploymentsClient } from "./deployments-client";

export const metadata = { title: "Deployments · Astrolift" };

export default function DeploymentsPage() {
  return (
    <PreloadQuery query={LIST_DEPLOYMENTS} variables={{ limit: 100 }}>
      <DeploymentsClient />
    </PreloadQuery>
  );
}
