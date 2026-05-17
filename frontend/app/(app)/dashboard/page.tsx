import { GET_COST_FORECAST } from "@/graphql/billing/billing.queries";
import { LIST_PROJECTS, LIST_TEAMS } from "@/graphql/identity/identity.queries";
import { GET_RECENT_ACTIVITY } from "@/graphql/operations/operations.queries";
import { PreloadQuery } from "@/lib/apollo";

import { DashboardClient } from "./dashboard-client";

export const metadata = {
  title: "Overview · Astrolift",
};

// Preload everything the client renders above the fold:
//  - identity counts (teams, projects)
//  - activity feed first page (#435 scope A)
//  - cost MTD forecast for the new KPI tile (#435 scope B)
// The lifecycle + metrics queries on the dashboard fetch
// client-side; they were never preloaded on the prior layout and we
// don't want to grow the SSR fan-out without measuring.
export default function DashboardPage() {
  return (
    <PreloadQuery query={LIST_TEAMS}>
      <PreloadQuery query={LIST_PROJECTS}>
        <PreloadQuery query={GET_RECENT_ACTIVITY} variables={{ limit: 10 }}>
          <PreloadQuery query={GET_COST_FORECAST}>
            <DashboardClient />
          </PreloadQuery>
        </PreloadQuery>
      </PreloadQuery>
    </PreloadQuery>
  );
}
