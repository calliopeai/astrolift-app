import { PreloadQuery } from "@/lib/apollo";
import { LIST_TEAMS } from "@/graphql/identity/identity.queries";

import { TeamsClient } from "./teams-client";

export const metadata = {
  title: "Teams · Astrolift",
};

export default function TeamsPage() {
  return (
    <PreloadQuery query={LIST_TEAMS}>
      <TeamsClient />
    </PreloadQuery>
  );
}
