"use client";

import { ShieldPlusIcon } from "lucide-react";
import Link from "next/link";
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
  const fallback = `Member ${id.slice(0, 8)}`;
  const tabHref = personTabs(id, tab).find((t) => t.active)?.href ?? PEOPLE_HREF;
  return (
    <PrincipalPage
      crumbs={accessCrumbs("people", m?.user.username ?? fallback)}
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
              Grant access
            </Link>
          </Button>
        ) : undefined
      }
      tabs={personTabs(id, tab)}
      loading={loading}
      error={error}
      onRetry={onRetry}
      notFound={!loading && !error && !m}
      notFoundCopy={{
        title: "Member not found",
        description:
          "This member may not exist, may have left the organization, or you may not have access to it.",
        backHref: PEOPLE_HREF,
        backLabel: "Back to People",
      }}
    >
      {children}
    </PrincipalPage>
  );
}
