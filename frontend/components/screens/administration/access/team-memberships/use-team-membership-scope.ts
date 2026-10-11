"use client";

import { useState } from "react";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";

/** New identity epoch even on A→B→A; keyed children cannot accept A's old replies. */
export function useTeamMembershipScope() {
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const { user, loading: actorLoading, error: actorError } = useMe();
  const ready = Boolean(
    org?.id && user?.id && !orgLoading && !actorLoading && !orgError && !actorError
  );
  const identity = JSON.stringify([org?.id ?? null, user?.id ?? null, ready]);
  const [epoch, setEpoch] = useState({ identity, revision: 0 });
  const current = epoch.identity === identity ? epoch : { identity, revision: epoch.revision + 1 };
  if (epoch.identity !== identity) setEpoch(current);
  return {
    ready,
    key: `${current.identity}:${current.revision}`,
    error: orgError?.message ?? actorError?.message ?? null,
  };
}
