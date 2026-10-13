import { routeTitle } from "@/lib/i18n/route-title";

import { TeamsClient } from "./teams-client";

export const generateMetadata = routeTitle("teams", "access");

/** Admin › Access › Teams (access UX design 3.1). */
export default function TeamsPage() {
  return <TeamsClient />;
}
