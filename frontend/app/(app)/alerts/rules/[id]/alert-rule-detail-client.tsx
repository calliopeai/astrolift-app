"use client";

import { AlertRuleDetail } from "@/components/screens/alerts/AlertRuleDetail";
import { useAlertRuleDetail } from "@/components/screens/alerts/use-alert-rule-detail";

/** Alert rule detail (#1106), wired to one rule id. */
export function AlertRuleDetailClient({ id }: { id: string }) {
  return <AlertRuleDetail {...useAlertRuleDetail(id)} />;
}
