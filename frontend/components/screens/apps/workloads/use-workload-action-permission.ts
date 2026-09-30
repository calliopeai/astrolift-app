"use client";

import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import type { AstroliftActionPermission } from "@/graphql/__generated__/schema";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";

export const workloadActionRefetch = {
  refetchQueries: "active" as const,
  onQueryUpdated: (query: { queryName?: string; refetch: () => Promise<unknown> }) =>
    ["ListWorkloads", "ListWorkloadsPage", "GetWorkload", "GetWorkloadScalingStatus"].includes(
      query.queryName ?? ""
    )
      ? refetchAfterMutation(query)
      : false,
  awaitRefetchQueries: true,
};

export function useWorkloadActionPermission(
  decision: AstroliftActionPermission | undefined,
  version: number | undefined
) {
  const t = useTranslations("apps.workloadActions");
  const [denial, setDenial] = React.useState<{
    decision: typeof decision;
    version: typeof version;
    code: string;
    reason: string;
  } | null>(null);
  const permission: AstroliftActionPermission =
    denial && denial.decision === decision && denial.version === version
      ? { allowed: false, code: denial.code, reason: denial.reason }
      : decision && Number.isSafeInteger(version) && (version ?? -1) >= 0
        ? decision
        : { allowed: false, code: "", reason: t("unavailable") };

  function blocked() {
    if (permission.allowed) return false;
    toast.error(permission.reason || t("unavailable"));
    return true;
  }

  function reject(payload: MutationResult<unknown> | undefined, fallback: string) {
    const error = payload?.errors?.[0];
    const reason = error?.message || fallback;
    if (
      ["PERMISSION_DENIED", "PRECONDITION", "NOT_FOUND", "CONFLICT"].includes(error?.code ?? "")
    ) {
      setDenial({ decision, version, code: error?.code ?? "", reason });
    }
    if (error?.code === "PERMISSION_DENIED")
      toast.error(t("permissionDenied"), { description: reason });
    else toast.error(reason);
    return false;
  }

  return { permission, blocked, reject };
}
