import {
  LIST_PROJECTS,
  LIST_TEAMS,
} from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { DashboardClient } from "./dashboard-client";

export const metadata = {
  title: "Overview · Astrolift",
};

export default function DashboardPage() {
  return (
    <PreloadQuery query={LIST_TEAMS}>
      <PreloadQuery query={LIST_PROJECTS}>
        <DashboardClient />
      </PreloadQuery>
    </PreloadQuery>
  );
}
