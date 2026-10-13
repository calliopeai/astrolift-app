import { PoliciesDocScreen } from "@/components/screens/documentation/PoliciesDocScreen";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("policies", "documentation");

export default function PoliciesDocPage() {
  return <PoliciesDocScreen />;
}
