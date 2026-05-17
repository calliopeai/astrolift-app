import {
  GET_COST_FORECAST,
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
        variables={{ window: "D30", limit: 500 }}
      >
        <PreloadQuery query={GET_COST_FORECAST}>
          <CostClient />
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
