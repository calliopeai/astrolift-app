import { ConfigurationScreen } from "@/components/screens/documentation/ConfigurationScreen";
import { CONFIGURATION_GROUPS } from "@/components/screens/documentation/configuration-groups";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("configuration", "documentation");

export default function ConfigurationDocPage() {
  return <ConfigurationScreen groups={CONFIGURATION_GROUPS} />;
}
