"use client";

import { ShieldPlusIcon, UsersRoundIcon } from "lucide-react";
import Link from "next/link";
import type * as React from "react";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { accessCrumbs, grantHref, PEOPLE_HREF } from "./access-nav";
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
 * external id, which is all a grant or mapping carries, so the page needs no
 * lookup of its own: its Access tab lists the grants the group holds and its
 * role mappings, and Members says where its members come from. The resolver
 * applies both to everyone the identity provider puts in the group (#2157),
 * so Grant access grants to the group itself. Pure.
 */
export function GroupDetail({ externalId, tab, children }: GroupDetailProps) {
  const self = `${PEOPLE_HREF}/${encodeURIComponent(`group:${externalId}`)}`;
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
      primaryAction={
        <Can permission="org.manage_members">
          <Button size="sm" asChild>
            <Link
              href={grantHref({
                principal: { kind: "group", id: externalId, name: externalId },
                returnTo: self,
              })}
            >
              <ShieldPlusIcon className="size-4" />
              Grant access
            </Link>
          </Button>
        </Can>
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
 * A group's Members tab. Its members live in the identity provider: each
 * person's groups are recorded when they sign in, and there is no query
 * that lists a group's members yet, so this says so and points at the
 * provider rather than showing an empty table that reads as "nobody".
 */
export function GroupMembersPanel({ externalId }: { externalId: string }) {
  return (
    <EmptyState
      icon={<UsersRoundIcon className="size-5" />}
      title="Members come from your identity provider"
      description={`Astrolift records who is in ${externalId} when each person signs in, and does not list them here yet. The provider's own console has the members; what the group gives them is on the Access tab.`}
      actionHref="/providers#identity"
      actionLabel="Identity provider"
    />
  );
}
