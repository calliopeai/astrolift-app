import { CustomDomainsDoc } from "@/components/screens/documentation/CustomDomainsDoc";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("customDomains", "documentation");

export default function CustomDomainsPage() {
  return <CustomDomainsDoc />;
}
