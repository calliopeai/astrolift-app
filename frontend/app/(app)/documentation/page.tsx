import { DocumentationIndexScreen } from "@/components/screens/documentation/DocumentationIndexScreen";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("documentation");

export default function DocumentationIndexPage() {
  return <DocumentationIndexScreen />;
}
