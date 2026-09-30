"use client";

import {
  KIND_TO_SOURCE_KIND,
  usableSourceConnections,
} from "@/components/wizard/source-connection";

import { useQuery } from "@apollo/client/react";
import * as React from "react";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { useScmConnect } from "@/components/use-scm-connect";
import { CLUSTER_COUNT } from "@/graphql/clusters/clusters.queries";
import { LIST_APPS } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp, SourceKind } from "@/graphql/registry/registry.types";
import { LIST_AVAILABLE_REPOS, LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import type {
  AstroliftRemoteRepoList,
  AstroliftSourceConnection,
  ScmConnectionKind,
} from "@/graphql/scm/scm.types";

import type { RepoPickerStepViewProps } from "./RepoPickerStep";

interface ConnectionsResp {
  astroliftSourceConnections: AstroliftSourceConnection[];
}

interface ReposResp {
  astroliftAvailableRepos: AstroliftRemoteRepoList;
}

interface ClusterCountResp {
  astroliftClusterCount: number;
}

interface AppsResp {
  astroliftApps: AstroliftRegisteredApp[];
}

/** The wizard-state fields step 1 reads and writes. */
export interface RepoPickerFields {
  connectionId: string;
  connectionKind: ScmConnectionKind | "";
  connectionIsAppInstall: boolean;
  sourceKind: SourceKind;
  sourceRepo: string;
  sourceUrl: string;
  defaultBranch: string;
  deployBranch: string;
  name: string;
  slug: string;
  slugTouched: boolean;
  manifestFromRepo: boolean;
  manifestErrors: string[];
  manifestValid: boolean;
  pushCiWorkflow: boolean;
}

export interface UseRepoPickerArgs<S extends RepoPickerFields> {
  state: S;
  setState: React.Dispatch<React.SetStateAction<S>>;
  setValid: (valid: boolean) => void;
  /**
   * Whether a connection kind can push the starter CI workflow. Passed in by
   * the wizard so the pushable set stays single-sourced (#908).
   */
  isCiPushableKind: (kind: ScmConnectionKind) => boolean;
}

/** Wizard step 1 (repo picker): every query and every wizard-state write. */
export function useRepoPicker<S extends RepoPickerFields>({
  state,
  setState,
  setValid,
  isCiPushableKind,
}: UseRepoPickerArgs<S>): RepoPickerStepViewProps {
  const scm = useScmConnect();
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  // Cluster preflight (#315/#316): registerApp refuses orgs with zero
  // managed clusters. We mirror the gate in the wizard so the operator
  // doesn't walk through five steps to fail at submit. Querying ahead
  // of the connections list keeps the empty-state UX coherent — no
  // partial UI rendered behind the gate. The backend's
  // astroliftClusterCount tightened in #316 to require lifecycle =
  // "managed" — registered-only rows no longer count.
  const clusterCount = useQuery<ClusterCountResp>(CLUSTER_COUNT, {
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });

  // Existing apps — used to surface a "monorepo" badge on repo rows
  // that already host a registered app at a different manifest path.
  // Backend currently has no per-repo flag (#409 follow-on); we derive
  // it client-side off the LIST_APPS payload.
  const existingApps = useQuery<AppsResp>(LIST_APPS, {
    skip: !orgId,
    fetchPolicy: "cache-first",
  });
  const repoToAppCount = React.useMemo(() => {
    const map = new Map<string, number>();
    for (const a of existingApps.data?.astroliftApps ?? []) {
      if (a.deletedAt) continue;
      if (!a.sourceRepo) continue;
      map.set(a.sourceRepo, (map.get(a.sourceRepo) ?? 0) + 1);
    }
    return map;
  }, [existingApps.data]);
  const hasCluster = (clusterCount.data?.astroliftClusterCount ?? 0) > 0;
  const clusterCountReady = clusterCount.data !== undefined;

  const connections = useQuery<ConnectionsResp>(LIST_SOURCE_CONNECTIONS, {
    fetchPolicy: "cache-and-network",
    skip: !orgId || !hasCluster,
  });

  const usable = React.useMemo(
    () => usableSourceConnections(connections.data?.astroliftSourceConnections ?? []),
    [connections.data]
  );
  const selectedConnection = usable.find((connection) => connection.id === state.connectionId);
  const connectionValid = Boolean(
    orgId && selectedConnection && !connections.loading && !connections.error
  );

  // Auto-pick the first connection when there's exactly one.
  React.useEffect(() => {
    if (!state.connectionId && usable.length === 1) {
      const c = usable[0];
      setState((s) => ({
        ...s,
        connectionId: c.id,
        connectionKind: c.kind as ScmConnectionKind,
        connectionIsAppInstall: c.kind === "github_app_install",
        sourceKind: KIND_TO_SOURCE_KIND[c.kind as ScmConnectionKind] ?? "git_url",
        // Default the CI-push toggle on for write-capable connections;
        // operators can flip it off in step 5 if their repo already
        // has a workflow they want to keep untouched.
        pushCiWorkflow: isCiPushableKind(c.kind),
      }));
    }
  }, [usable, state.connectionId, setState, isCiPushableKind]);

  const [search, setSearch] = React.useState("");
  const repos = useQuery<ReposResp>(LIST_AVAILABLE_REPOS, {
    variables: {
      connectionId: state.connectionId,
      search: search || null,
      limit: 100,
    },
    skip: !connectionValid,
    fetchPolicy: "cache-and-network",
  });
  const repoList = repos.data?.astroliftAvailableRepos;

  // A repo is picked when sourceRepo + connectionId are both set
  // AND the org has at least one managed cluster (#315/#316). The
  // backend would refuse on submit anyway; gating Next here keeps the
  // operator from going further until the precondition is met.
  React.useEffect(() => {
    setValid(Boolean(hasCluster && connectionValid && state.sourceRepo));
  }, [hasCluster, connectionValid, state.sourceRepo, setValid]);

  function onPickRepo(fullName: string) {
    const repo = repoList?.repos.find((r) => r.fullName === fullName);
    if (!repo || !connectionValid) return;
    const conn = usable.find((c) => c.id === state.connectionId);
    const inferredKind = conn
      ? (KIND_TO_SOURCE_KIND[conn.kind as ScmConnectionKind] ?? "git_url")
      : state.sourceKind;
    const branch = repo.defaultBranch || "main";
    setState((s) => ({
      ...s,
      sourceKind: inferredKind,
      sourceRepo: repo.fullName,
      sourceUrl: repo.cloneUrlHttps || repo.cloneUrlSsh || "",
      defaultBranch: branch,
      deployBranch: branch,
      // Seed name/slug only if the operator hasn't typed anything yet.
      // Title-case the repo name for the human-readable label so it
      // doesn't ship to the UI as `api-gateway` or `api_gateway`.
      name: s.name || titleCased(repo.name),
      slug: s.slugTouched ? s.slug : kebab(repo.name),
      manifestFromRepo: false, // step 2 will refetch
      manifestErrors: [],
      manifestValid: false,
    }));
  }

  function onConnectionChange(v: string) {
    const conn = usable.find((c) => c.id === v);
    setState((s) => ({
      ...s,
      connectionId: v,
      connectionKind: conn ? (conn.kind as ScmConnectionKind) : "",
      connectionIsAppInstall: conn?.kind === "github_app_install",
      sourceKind: conn
        ? (KIND_TO_SOURCE_KIND[conn.kind as ScmConnectionKind] ?? "git_url")
        : s.sourceKind,
      sourceRepo: "",
      sourceUrl: "",
      pushCiWorkflow: conn ? isCiPushableKind(conn.kind) : s.pushCiWorkflow,
    }));
    setSearch("");
  }

  return {
    clusterLoading: clusterCount.loading && !clusterCountReady,
    noCluster: clusterCountReady && !hasCluster,
    connectionsLoading: connections.loading && !connections.data,
    connections: usable,
    connectionId: state.connectionId,
    sourceRepo: state.sourceRepo,
    defaultBranch: state.defaultBranch,
    onConnectionChange,
    reposLoading: repos.loading && !repos.data,
    repoList,
    search,
    onSearchChange: setSearch,
    onPickRepo,
    repoToAppCount,
    scm,
  };
}

function kebab(s: string): string {
  return s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

function titleCased(s: string): string {
  return s
    .replace(/[-_]+/g, " ")
    .trim()
    .split(/\s+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}
