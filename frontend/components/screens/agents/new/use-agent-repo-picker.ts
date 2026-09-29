"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { useScmConnect } from "@/components/use-scm-connect";
import { LIST_AGENT_FLEET } from "@/graphql/agents/agents.queries";
import type {
  AstroliftAgentListItem,
  AstroliftDiscoveredAgentManifest,
} from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import type { SourceKind } from "@/graphql/registry/registry.types";
import { LIST_AVAILABLE_REPOS, LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import type {
  AstroliftRemoteRepoList,
  AstroliftSourceConnection,
  ScmConnectionKind,
} from "@/graphql/scm/scm.types";

import type { AgentRepoPickerStepViewProps } from "./AgentRepoPickerStep";

interface ConnectionsResp {
  astroliftSourceConnections: AstroliftSourceConnection[];
}

interface ReposResp {
  astroliftAvailableRepos: AstroliftRemoteRepoList;
}

interface AgentFleetResp {
  agentFleet: AstroliftAgentListItem[];
}

const KIND_TO_SOURCE_KIND: Record<ScmConnectionKind, SourceKind> = {
  github_pat: "github",
  github_app_install: "github",
  github_oauth_app: "github",
  github_oauth_user: "github",
  gitlab_pat: "gitlab",
  gitlab_oauth_app: "gitlab",
  gitlab_oauth_user: "gitlab",
  bitbucket_pat: "bitbucket",
  bitbucket_oauth_app: "bitbucket",
  bitbucket_oauth_user: "bitbucket",
  gitea_pat: "gitea",
  gitea_oauth_app: "gitea",
  gitea_oauth_user: "gitea",
};

/** The agent-wizard-state fields step 1 reads and writes. */
export interface AgentRepoPickerFields {
  connectionId: string;
  connectionKind: ScmConnectionKind | "";
  sourceKind: SourceKind;
  sourceRepo: string;
  sourceUrl: string;
  defaultBranch: string;
  deployBranch: string;
  ref: string;
  scanned: boolean;
  scanError: string | null;
  discoveredAgents: AstroliftDiscoveredAgentManifest[];
}

export interface UseAgentRepoPickerArgs<S extends AgentRepoPickerFields> {
  state: S;
  setState: React.Dispatch<React.SetStateAction<S>>;
  setValid: (valid: boolean) => void;
}

/** Agent wizard step 1 (repo picker): every query and every wizard-state write. */
export function useAgentRepoPicker<S extends AgentRepoPickerFields>({
  state,
  setState,
  setValid,
}: UseAgentRepoPickerArgs<S>): AgentRepoPickerStepViewProps {
  const scm = useScmConnect();
  // Reactive org id (#1022): the synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" on cold load.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";

  // Existing fleet — surfaces a "N agents already registered" badge on repo
  // rows that already host registered agents (a re-scan only adds NEW agents).
  // The register-app wizard derives its monorepo-count seam here off
  // `LIST_APPS` (use-repo-picker.ts); for agents the analogous signal is
  // the fleet list, keyed by sourceRepo.
  const existingAgents = useQuery<AgentFleetResp>(LIST_AGENT_FLEET, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-first",
  });
  const repoToAgentCount = React.useMemo(() => {
    const map = new Map<string, number>();
    for (const a of existingAgents.data?.agentFleet ?? []) {
      if (!a.sourceRepo) continue;
      map.set(a.sourceRepo, (map.get(a.sourceRepo) ?? 0) + 1);
    }
    return map;
  }, [existingAgents.data]);

  // No managed-cluster preflight here. Unlike register_app (a deploy target),
  // register_agent_repo does NOT require a managed cluster — agents are
  // dispatched on demand and the dispatch path enforces that requirement
  // later. Gating registration on a cluster would be wrong.
  const connections = useQuery<ConnectionsResp>(LIST_SOURCE_CONNECTIONS, {
    fetchPolicy: "cache-and-network",
  });

  const usable = (connections.data?.astroliftSourceConnections ?? []).filter(
    (c) => c.isActive && !c.isOauthAppConfig
  );

  // Auto-pick the first connection when there's exactly one.
  React.useEffect(() => {
    if (!state.connectionId && usable.length === 1) {
      const c = usable[0];
      setState((s) => ({
        ...s,
        connectionId: c.id,
        connectionKind: c.kind as ScmConnectionKind,
        sourceKind: KIND_TO_SOURCE_KIND[c.kind as ScmConnectionKind] ?? "git_url",
      }));
    }
  }, [usable, state.connectionId, setState]);

  const [search, setSearch] = React.useState("");
  const repos = useQuery<ReposResp>(LIST_AVAILABLE_REPOS, {
    variables: {
      connectionId: state.connectionId,
      search: search || null,
      limit: 100,
    },
    skip: !state.connectionId,
    fetchPolicy: "cache-and-network",
  });
  const repoList = repos.data?.astroliftAvailableRepos;

  // A repo is picked when sourceRepo + connectionId are both set. (No cluster
  // gate — see above.)
  React.useEffect(() => {
    setValid(Boolean(state.connectionId && state.sourceRepo));
  }, [state.connectionId, state.sourceRepo, setValid]);

  function onPickRepo(fullName: string) {
    const repo = repoList?.repos.find((r) => r.fullName === fullName);
    if (!repo) return;
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
      ref: branch,
      // Picking a different repo invalidates a prior scan.
      scanned: false,
      discoveredAgents: [],
      scanError: null,
    }));
  }

  function onConnectionChange(v: string) {
    const conn = usable.find((c) => c.id === v);
    setState((s) => ({
      ...s,
      connectionId: v,
      connectionKind: conn ? (conn.kind as ScmConnectionKind) : "",
      sourceKind: conn
        ? (KIND_TO_SOURCE_KIND[conn.kind as ScmConnectionKind] ?? "git_url")
        : s.sourceKind,
      sourceRepo: "",
      sourceUrl: "",
      scanned: false,
      discoveredAgents: [],
      scanError: null,
    }));
    setSearch("");
  }

  function onRefChange(v: string) {
    setState((s) => ({
      ...s,
      ref: v,
      defaultBranch: v,
      deployBranch: v,
      // Changing the ref invalidates a prior scan.
      scanned: false,
      discoveredAgents: [],
      scanError: null,
    }));
  }

  return {
    connectionsLoading: connections.loading && !connections.data,
    connections: usable,
    connectionId: state.connectionId,
    sourceRepo: state.sourceRepo,
    refValue: state.ref,
    onConnectionChange,
    reposLoading: repos.loading && !repos.data,
    repoList,
    search,
    onSearchChange: setSearch,
    onPickRepo,
    onRefChange,
    repoToAgentCount,
    scm,
  };
}
