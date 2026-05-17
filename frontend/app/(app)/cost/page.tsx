import {
  LIST_BUDGETS,
  LIST_COST_SNAPSHOTS,
} from "@/graphql/billing/billing.queries";
import { PreloadQuery } from "@/lib/apollo";

import { CostClient } from "./cost-client";

export const metadata = { title: "Cost · Astrolift" };

export default function CostPage() {
  return (
    <PreloadQuery query={LIST_BUDGETS}>
      <PreloadQuery
        query={LIST_COST_SNAPSHOTS}
        variables={{ days: 30, limit: 100 }}
      >
        <CostClient />
      </PreloadQuery>
    </PreloadQuery>
  );
}
