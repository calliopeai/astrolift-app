"use client";

import { useQuery } from "@apollo/client/react";
import type { GetTeamAccessNavigationQuery } from "@/graphql/__generated__/operations";
import { GET_TEAM_ACCESS_NAVIGATION } from "@/graphql/identity/team-memberships.queries";

export function useTeamAccessNavigation(ready = true) {
  const query = useQuery<GetTeamAccessNavigationQuery>(GET_TEAM_ACCESS_NAVIGATION, {
    skip: !ready,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  return {
    ...query,
    navigation:
      !query.loading && !query.error ? (query.data?.me?.teamAccessNavigation ?? null) : null,
  };
}
