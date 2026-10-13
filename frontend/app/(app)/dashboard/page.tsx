import { routeTitle } from "@/lib/i18n/route-title";

import { HomeClient } from "./home-client";

export const generateMetadata = routeTitle("home");

export default function DashboardPage() {
  return <HomeClient />;
}
