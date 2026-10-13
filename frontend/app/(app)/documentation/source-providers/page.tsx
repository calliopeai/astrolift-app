import { SourceProvidersDocScreen } from "@/components/screens/documentation/SourceProvidersDocScreen";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("sourceProviders", "documentation");

export default function SourceProvidersDocPage() {
  return <SourceProvidersDocScreen />;
}
