"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";

import { useGrantAccess } from "@/components/access/use-grant-access";

import { accessCrumbs, parseGrantParams } from "./access-nav";

/**
 * The Grant access page (access UX design 3.4): `useGrantAccess` plus the
 * route's own half, the preselection from the query (`grantHref`) and where
 * Cancel and Done go (`?return=`). The screen is `GrantAccessFlow`.
 */
export function useGrantPage() {
  const router = useRouter();
  const params = useSearchParams();
  const { principal, scope, returnTo } = parseGrantParams(
    new URLSearchParams(params?.toString() ?? "")
  );
  const flow = useGrantAccess();
  return {
    ...flow,
    crumbs: accessCrumbs("people", "Grant access"),
    initialDraft: {
      principals: principal ? [principal] : [],
      scope,
    },
    onCancel: () => router.push(returnTo),
    onDone: (outcomes: { ok: boolean }[]) => {
      const n = outcomes.filter((o) => o.ok).length;
      toast.success(`Granted to ${n} ${n === 1 ? "person" : "people"}`);
      router.push(returnTo);
    },
  };
}
