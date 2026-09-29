"use client";

import { UsersRoundIcon } from "lucide-react";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";

import { accessCrumbs, PEOPLE_HREF } from "./access-nav";
import { PrincipalPage } from "./PrincipalPage";
import { type GroupTab, groupTabs } from "./principal-tabs";

export interface GroupDetailProps {
  externalId: string;
  tab: GroupTab;
  /** The active tab's body. */
  children: React.ReactNode;
}

/**
 * An IdP group's page (access UX design 3.2). A group is known by its
 * external id, which is all a role binding carries, so the page needs no
 * lookup of its own: its Access tab lists the grants the group holds, and
 * Members says where its members come from. No Grant access action: the
 * backend grants roles to users only (design 6.9). Pure.
 */
export function GroupDetail({ externalId, tab, children }: GroupDetailProps) {
  return (
    <PrincipalPage
      crumbs={accessCrumbs("people", externalId)}
      principal={{ kind: "group", id: externalId, name: externalId }}
      fallbackTitle={externalId}
      status={
        <Badge variant="secondary" className="text-2xs">
          IdP group
        </Badge>
      }
      tabs={groupTabs(externalId, tab)}
      loading={false}
      error={null}
      onRetry={() => {}}
      notFound={false}
      notFoundCopy={{
        title: "Group not found",
        description: "",
        backHref: PEOPLE_HREF,
        backLabel: "Back to People",
      }}
    >
      {children}
    </PrincipalPage>
  );
}

/**
 * A group's Members tab. Its members live in the identity provider, and
 * Astrolift has no group list or member sync yet (design 6.2, 6.4), so this
 * says so and points at the provider rather than showing an empty table
 * that reads as "nobody".
 */
export function GroupMembersPanel({ externalId }: { externalId: string }) {
  return (
    <EmptyState
      icon={<UsersRoundIcon className="size-5" />}
      title="Members come from your identity provider"
      description={`Astrolift does not sync who is in ${externalId} yet, so it cannot list them here. The provider's own console has the members; the grants this group holds are on the Access tab.`}
      actionHref="/providers#identity"
      actionLabel="Identity provider"
    />
  );
}
