import { AccessLandingClient } from "@/components/screens/administration/access/team-memberships/AccessLandingClient";

export const metadata = { title: "Access · Astrolift" };

/** Current credential decides People versus Teams; broad module visibility does not. */
export default function AccessPage() {
  return <AccessLandingClient />;
}
