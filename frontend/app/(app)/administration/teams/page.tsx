import { LIST_TEAMS } from "@/graphql/identity/identity.queries";
import { PreloadQuery } from "@/lib/apollo";

import { TeamsClient } from "@/app/(app)/teams/teams-client";

export const metadata = {
  title: "Teams · Astrolift",
};

export default function AdministrationTeamsPage() {
  return (
    <PreloadQuery query={LIST_TEAMS}>
      <TeamsClient />
    </PreloadQuery>
  );
}
