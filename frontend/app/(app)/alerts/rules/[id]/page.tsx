import { PreloadQuery } from "@/lib/apollo";
import { LIST_ALERT_RULES } from "@/graphql/operations/alerts.queries";

import { AlertRuleDetailClient } from "./alert-rule-detail-client";

export const metadata = { title: "Alert rule · Astrolift" };

/**
 * Alert rule detail (#1106) — drill-in target for an /alerts rule row.
 * Reuses the global LIST_ALERT_RULES window (no singular query exists).
 */
export default async function AlertRuleDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <PreloadQuery query={LIST_ALERT_RULES} variables={{ activeOnly: false }}>
      <AlertRuleDetailClient id={id} />
    </PreloadQuery>
  );
}
