import { TracesExplorer } from "@/components/screens/traces/TracesScreen";
import { routeTitle } from "@/lib/i18n/route-title";
export const generateMetadata = routeTitle("traces");
export default function TracesPage() {
  return <TracesExplorer />;
}
