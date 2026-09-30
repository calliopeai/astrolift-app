"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { useGrantAccess } from "@/components/access/use-grant-access";

import { accessCrumbs, parseGrantParams } from "./access-nav";

/**
 * The Grant access page (access UX design 3.4): `useGrantAccess` plus the
 * route's own half, the preselection from the query (`grantHref`) and where
 * Cancel and Done go (`?return=`). The screen is `GrantAccessFlow`.
 */
export function useGrantPage() {
  const t = useTranslations("shared.access.grant");
  const router = useRouter();
  const params = useSearchParams();
  const { principal, scope, returnTo } = parseGrantParams(
    new URLSearchParams(params?.toString() ?? "")
  );
  const flow = useGrantAccess();
  const labels: Record<string, string> = {
    "/administration/organization": t("links.organization"),
    "/administration/projects": t("links.projects"),
    "/administration/access": t("access"),
    "/administration/access/people": t("people"),
    "/administration/access/teams": t("links.teams"),
    "/administration/permissions": t("links.roles"),
    "/administration/policies": t("links.policies"),
    "/administration/permissions/diagnostics": t("links.check"),
  };
  return {
    ...flow,
    crumbs: accessCrumbs("people", t("title")).map((crumb, index) => ({
      ...crumb,
      switcher: crumb.switcher?.map((option) => ({
        ...option,
        label: labels[option.href] ?? option.label,
      })),
      label:
        index === 0
          ? t("admin")
          : index === 1
            ? t("access")
            : index === 2
              ? t("people")
              : crumb.label,
    })),
    initialDraft: {
      principals: principal ? [principal] : [],
      scope,
    },
    onCancel: () => router.push(returnTo),
    onDone: (outcomes: { ok: boolean }[]) => {
      const n = outcomes.filter((o) => o.ok).length;
      toast.success(t("success", { count: n }));
      router.push(returnTo);
    },
  };
}
