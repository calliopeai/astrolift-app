import { PreloadQuery } from "@/lib/apollo";
import { LIST_COMMAND_RUNS } from "@/graphql/lifecycle/lifecycle.queries";

import { CommandRunDetailClient } from "./command-run-detail-client";

export const metadata = { title: "Command run · Astrolift" };

/**
 * Command run detail (#1106) — drill-in target for a /jobs Commands row.
 * Reuses the global LIST_COMMAND_RUNS window (no singular query exists).
 */
export default async function CommandRunDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <PreloadQuery query={LIST_COMMAND_RUNS} variables={{ limit: 100 }}>
      <CommandRunDetailClient id={id} />
    </PreloadQuery>
  );
}
