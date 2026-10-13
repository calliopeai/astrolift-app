import { routeTitle } from "@/lib/i18n/route-title";

import { DownloadsClient } from "./downloads-client";

export const generateMetadata = routeTitle("downloads");

export default function DownloadsPage() {
  return <DownloadsClient />;
}
