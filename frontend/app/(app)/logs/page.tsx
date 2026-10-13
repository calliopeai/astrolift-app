import { LogsExplorer } from "@/components/screens/logs/LogsScreen";
import { routeTitle } from "@/lib/i18n/route-title";
export const generateMetadata = routeTitle("logs");
export default function LogsPage() {
  return <LogsExplorer />;
}
