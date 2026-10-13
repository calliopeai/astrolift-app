import { routeTitle } from "@/lib/i18n/route-title";

import { GrantClient } from "./grant-client";

export const generateMetadata = routeTitle("grantAccess");

/** The one Grant access flow (access UX design 3.4), preselected from its query. */
export default function GrantPage() {
  return <GrantClient />;
}
