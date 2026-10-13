import { ClusterPrerequisitesScreen } from "@/components/screens/documentation/ClusterPrerequisitesScreen";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("clusterPrerequisites", "documentation");

export default function ClusterPrerequisitesPage() {
  return <ClusterPrerequisitesScreen />;
}
