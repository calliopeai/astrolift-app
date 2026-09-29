"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import type { ListNavTreeQuery } from "@/graphql/__generated__/operations";
import { LIST_NAV_TREE } from "@/graphql/identity/identity.queries";

import type { ScopeNode } from "./access-model";

type NavTree = NonNullable<ListNavTreeQuery["astroliftNavTree"]>;
type AppSummary = NavTree["unassignedApps"][number];

function appNodes(apps: AppSummary[]): ScopeNode[] {
  const seen = new Set<string>();
  return apps
    .filter((a) => !seen.has(a.id) && seen.add(a.id))
    .map((a) => ({ kind: "APP", id: a.id, name: a.name, slug: a.slug }));
}

/**
 * The nav tree as scopes: org › teams › projects › apps, where an agent is an
 * app (agents are registered apps; the resolver scopes them as APP) and a
 * workflow's agents join their project. Apps outside a project sit under
 * their team, and apps outside a team under the org. Pure.
 */
export function navTreeToScopes(tree: NavTree | null | undefined): ScopeNode[] {
  if (!tree) return [];
  const teams: ScopeNode[] = tree.teams.map(({ team, projects, unassignedApps }) => ({
    kind: "TEAM",
    id: team.id,
    name: team.name,
    slug: team.slug,
    children: [
      ...projects.map(({ project, apps, workflows, standaloneAgents }) => ({
        kind: "PROJECT" as const,
        id: project.id,
        name: project.name,
        slug: project.slug,
        children: appNodes([...apps, ...standaloneAgents, ...workflows.flatMap((w) => w.agents)]),
      })),
      ...appNodes(unassignedApps),
    ],
  }));
  return [
    {
      kind: "ORG",
      id: tree.organization.id,
      name: tree.organization.name,
      slug: tree.organization.slug,
      children: [...teams, ...appNodes(tree.unassignedApps)],
    },
  ];
}

/**
 * The scope tree for `ScopePicker`. One query the shell's projects rail
 * already runs (`astroliftNavTree`), read cache-first, so opening the picker
 * does not fetch it again. It arrives whole, so no node needs `loadChildren`.
 */
export function useScopeTree() {
  const { data, loading, error, refetch } = useQuery<ListNavTreeQuery>(LIST_NAV_TREE, {
    fetchPolicy: "cache-first",
  });
  const tree = data?.astroliftNavTree;
  const roots = React.useMemo(() => navTreeToScopes(tree), [tree]);
  return {
    roots,
    loading: loading && !data,
    error: error ? { message: error.message } : null,
    onRetry: () => void refetch(),
  };
}
