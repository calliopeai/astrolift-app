"use client";

import { ShieldPlusIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { DetailStatusBadge, type Dot } from "@/components/detail/EntityDetailShell";
import { Button } from "@/components/ui/button";

import { accessCrumbs, grantHref, PEOPLE_HREF } from "./access-nav";
import { PrincipalPage } from "./PrincipalPage";
import { type PersonTab, personTabs } from "./principal-tabs";
import type { useMemberDetail } from "./use-member-detail";

const LIFECYCLE_TONE: Record<string, Dot> = {
  active: "ok",
  invited: "warn",
  suspended: "warn",
  anonymized: "muted",
};

export type MemberDetailProps = Omit<ReturnType<typeof useMemberDetail>, "memberships"> & {
  tab: PersonTab;
  /** The active tab's body, fed by that tab's own hook. */
  children: React.ReactNode;
};

/**
 * A person's page (access UX design 3.2) on the detail archetype: their
 * name and email, lifecycle, Grant access, and the tabs Access (what they
 * hold, by scope, removable at its source), Teams and Activity. Pure.
 */
export function MemberDetail({
  id,
  member: m,
  canManage,
  loading,
  error,
  onRetry,
  tab,
  children,
}: MemberDetailProps) {
  const t = useTranslations("adminPrincipal");
  const fallback = t("memberFallback", { id: id.slice(0, 8) });
  const tabs = personTabs(id, tab).map((item) => ({ ...item, label: t(`tabs.${item.key}`) }));
  const tabHref = tabs.find((item) => item.active)?.href ?? PEOPLE_HREF;
  return (
    <PrincipalPage
      crumbs={accessCrumbs("people", m?.user.username ?? fallback)}
      presentation={{
        tabsAriaLabel: t("tabsLabel"),
        loadFailed: t("loadFailed"),
        retry: t("retry"),
      }}
      principal={
        m ? { kind: "user", id: m.user.id, name: m.user.username, detail: m.user.email } : null
      }
      fallbackTitle={fallback}
      status={
        m ? (
          <DetailStatusBadge status={m.lifecycle} tone={LIFECYCLE_TONE[m.lifecycle] ?? "muted"} />
        ) : undefined
      }
      context={m ? <span className="font-mono">{m.user.email}</span> : undefined}
      primaryAction={
        m && canManage ? (
          <Button size="sm" asChild>
            <Link
              href={grantHref({
                principal: { kind: "user", id: m.user.id, name: m.user.username },
                returnTo: tabHref,
              })}
            >
              <ShieldPlusIcon className="size-4" />
              {t("grantAccess")}
            </Link>
          </Button>
        ) : undefined
      }
      tabs={tabs}
      loading={loading}
      error={error}
      onRetry={onRetry}
      notFound={!loading && !error && !m}
      notFoundCopy={{
        title: t("memberNotFound"),
        description: t("memberNotFoundDescription"),
        backHref: PEOPLE_HREF,
        backLabel: t("backToPeople"),
      }}
    >
      {children}
    </PrincipalPage>
  );
}
