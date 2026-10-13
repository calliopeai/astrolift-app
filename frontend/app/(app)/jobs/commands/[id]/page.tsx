import { PreloadQuery } from "@/lib/apollo";
import { GET_COMMAND_RUN } from "@/graphql/lifecycle/lifecycle.queries";
import { routeTitle } from "@/lib/i18n/route-title";

import { CommandRunDetailClient } from "./command-run-detail-client";

export const generateMetadata = routeTitle("commandRun");

/**
 * Command run detail (#1106) — drill-in target for a /jobs Commands row.
 * Preloads the run itself, the query the page reads first; the 100-run list
 * window is only an instant-paint fallback when the cache already has it.
 */
export default async function CommandRunDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <PreloadQuery query={GET_COMMAND_RUN} variables={{ id }}>
      <CommandRunDetailClient id={id} />
    </PreloadQuery>
  );
}
