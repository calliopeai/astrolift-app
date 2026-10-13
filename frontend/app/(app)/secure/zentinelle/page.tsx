import { ZentinelleScreen } from "@/components/screens/secure/ZentinelleScreen";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("zentinelle", "secure");

export default function ZentinellePage() {
  return <ZentinelleScreen />;
}
