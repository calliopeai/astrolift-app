import { routeTitle } from "@/lib/i18n/route-title";

import { SupportClient } from "./support-client";

export const generateMetadata = routeTitle("support");

export default function SupportPage() {
  return <SupportClient />;
}
