import { defaultListState } from "@/components/list/list-state";
import {
  RUN_AUDIT_LIST,
  runAuditVariables,
} from "@/components/screens/administration/insights/combined-runs";
import { RUN_AUDIT } from "@/graphql/operations/run-audit.queries";
import { PreloadQuery } from "@/lib/apollo";

import { RunsClient } from "./runs-client";

export const metadata = { title: "Runs · Astrolift" };

/**
 * The variables `useCombinedRuns` sends for the default list state. The
 * default has no `since`, so the clock does not enter them; a saved view,
 * chip or cursor falls through to the client fetch.
 */
const FIRST_PAGE = runAuditVariables(
  { ...defaultListState(RUN_AUDIT_LIST), filters: {}, after: null },
  0
);

/**
 * Admin › Usage & governance › Runs: the combined run audit (spec 44 §4.4,
 * decision 14), one `astroliftRunAudit` page, preloaded so it paints with rows.
 */
export default function RunsPage() {
  return (
    <PreloadQuery query={RUN_AUDIT} variables={FIRST_PAGE}>
      <RunsClient />
    </PreloadQuery>
  );
}
