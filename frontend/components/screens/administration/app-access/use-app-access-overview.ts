"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { useListState } from "@/components/list/use-list-state";
import { useLocalizedList } from "@/components/list/use-localized-list";
import type { EdgeAccessForPersonQuery, EdgeAccessQuery } from "@/graphql/__generated__/operations";
import { EDGE_ACCESS, EDGE_ACCESS_FOR_PERSON } from "@/graphql/registry/registry.queries";

import type { AppAccessOverviewScreenProps } from "./AppAccessOverviewScreen";
import {
  EDGE_ACCESS_LIST,
  edgeAccessVariables,
  fromEdgeAccessRow,
  fromPersonPage,
} from "./edge-access-list";

/**
 * Admin › App access's data half (#2534): one `astroliftEdgeAccess` page for
 * the list state in the URL, or `astroliftEdgeAccessForPerson` when a person
 * is named (Mine names the viewer). Filters, search, order and count are the
 * server's.
 */
export function useAppAccessOverview(): AppAccessOverviewScreenProps {
  const list = useListState(useLocalizedList(EDGE_ACCESS_LIST));
  const variables = edgeAccessVariables({
    filters: list.filters,
    q: list.state.q,
    pageSize: list.state.pageSize,
    after: list.state.after,
  });
  const email = list.filters.person?.trim() || null;

  const all = useQuery<EdgeAccessQuery>(EDGE_ACCESS, {
    variables,
    skip: email !== null,
    fetchPolicy: "cache-and-network",
  });
  const forPerson = useQuery<EdgeAccessForPersonQuery>(EDGE_ACCESS_FOR_PERSON, {
    variables: { ...variables, email: email ?? "" },
    skip: email === null,
    fetchPolicy: "cache-and-network",
  });

  const query = email === null ? all : forPerson;
  const allPage = (all.data ?? all.previousData)?.astroliftEdgeAccess;
  const personPage = (forPerson.data ?? forPerson.previousData)?.astroliftEdgeAccessForPerson;
  const page = email === null ? allPage : personPage;
  const view = React.useMemo(() => {
    if (email !== null) return personPage ? fromPersonPage(personPage) : { rows: [], person: null };
    return { rows: (allPage?.items ?? []).map(fromEdgeAccessRow), person: null };
  }, [email, allPage, personPage]);
  const hasData = Boolean(query.data ?? query.previousData);

  return {
    list,
    rows: view.rows,
    person: view.person,
    loading: query.loading && !hasData,
    stale: query.loading && !query.data && hasData,
    error: query.error && !query.data ? { message: query.error.message } : null,
    onRetry: () => void query.refetch(),
    nextCursor: page?.nextCursor ?? null,
    totalCount: page?.totalCount ?? null,
  };
}
