"use client";

import { AuditLogScreen } from "@/components/screens/administration/insights/AuditLogScreen";
import { useAuditLog } from "@/components/screens/administration/insights/use-audit-log";

export function AuditClient() {
  return <AuditLogScreen {...useAuditLog()} />;
}
