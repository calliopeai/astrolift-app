import { routeTitle } from "@/lib/i18n/route-title";

import { CommandRunsClient } from "./command-runs-client";

export const generateMetadata = routeTitle("commandRuns");

/** One-off command runs across apps, cursor paged. */
export default function CommandRunsPage() {
  return <CommandRunsClient />;
}
