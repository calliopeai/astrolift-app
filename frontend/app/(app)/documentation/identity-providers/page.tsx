import { IdentityProvidersDoc } from "@/components/screens/documentation/IdentityProvidersDoc";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("identityProviders", "documentation");

export default function IdentityProvidersDocPage() {
  return <IdentityProvidersDoc />;
}
