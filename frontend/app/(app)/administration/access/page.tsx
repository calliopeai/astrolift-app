import { AccessLandingClient } from "@/components/screens/administration/access/team-memberships/AccessLandingClient";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("access");

/** Current credential decides People versus Teams; broad module visibility does not. */
export default function AccessPage() {
  return <AccessLandingClient />;
}
