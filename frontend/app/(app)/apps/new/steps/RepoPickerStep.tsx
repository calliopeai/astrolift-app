"use client";

import { useQuery } from "@apollo/client/react";
import { GitBranchIcon, LinkIcon, LockIcon, ServerIcon, UnlockIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { CLUSTER_COUNT } from "@/graphql/clusters/clusters.queries";
import { LIST_AVAILABLE_REPOS, LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import type {
  AstroliftRemoteRepoList,
  AstroliftSourceConnection,
  ScmConnectionKind,
} from "@/graphql/scm/scm.types";
import type { SourceKind } from "@/graphql/registry/registry.types";

import type { WizardState } from "../wizard-client";

interface ConnectionsResp {
  astroliftSourceConnections: AstroliftSourceConnection[];
}

interface ReposResp {
  astroliftAvailableRepos: AstroliftRemoteRepoList;
}

interface ClusterCountResp {
  astroliftClusterCount: number;
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

// Connection kinds that hold a usable write-token so pushCiWorkflow
// can commit on the operator's behalf. OAuth-app config rows carry the
// app's client secret, not a user token, so they're excluded. Mirrors
// the constant on ReviewSubmitStep; duplicated here so step 1 can flip
// the default toggle when the operator picks a connection.
const CI_PUSHABLE_KINDS = new Set<string>([
  "github_oauth_user",
  "github_app_install",
  "github_pat",
  "gitlab_oauth_user",
  "gitlab_pat",
]);

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

export function RepoPickerStep({ state, setState, setValid }: Props) {
  // Cluster preflight (#315): registerApp refuses orgs with zero
  // active clusters. We mirror the gate in the wizard so the operator
  // doesn't walk through five steps to fail at submit. Querying ahead
  // of the connections list keeps the empty-state UX coherent — no
  // partial UI rendered behind the gate.
  const clusterCount = useQuery<ClusterCountResp>(CLUSTER_COUNT, {
    fetchPolicy: "cache-and-network",
  });
  const hasCluster = (clusterCount.data?.astroliftClusterCount ?? 0) > 0;
  const clusterCountReady = clusterCount.data !== undefined;

  const connections = useQuery<ConnectionsResp>(LIST_SOURCE_CONNECTIONS, {
    fetchPolicy: "cache-and-network",
    skip: !hasCluster,
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
        connectionIsAppInstall: c.kind === "github_app_install",
        sourceKind: KIND_TO_SOURCE_KIND[c.kind as ScmConnectionKind] ?? "git_url",
        // Default the CI-push toggle on for write-capable connections;
        // operators can flip it off in step 5 if their repo already
        // has a workflow they want to keep untouched.
        pushCiWorkflow: CI_PUSHABLE_KINDS.has(c.kind),
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

  // A repo is picked when sourceRepo + connectionId are both set
  // AND the org has at least one connected cluster (#315). The
  // backend would refuse on submit anyway; gating Next here keeps the
  // operator from going further until the precondition is met.
  React.useEffect(() => {
    setValid(Boolean(hasCluster && state.connectionId && state.sourceRepo));
  }, [hasCluster, state.connectionId, state.sourceRepo, setValid]);

  function pickRepo(fullName: string) {
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
      // Seed name/slug only if the operator hasn't typed anything yet.
      name: s.name || repo.name,
      slug: s.slugTouched ? s.slug : kebab(repo.name),
      manifestFromRepo: false, // step 2 will refetch
      manifestErrors: [],
      manifestValid: false,
    }));
  }

  if (clusterCount.loading && !clusterCountReady) {
    return (
      <div className="flex flex-col gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (clusterCountReady && !hasCluster) {
    return (
      <EmptyState
        icon={<ServerIcon className="size-5" />}
        title="Connect a cluster first"
        description="Astrolift deploys apps to Kubernetes clusters you've registered. Connect at least one before adding apps."
        actionHref="/clusters"
        actionLabel="Manage clusters"
      />
    );
  }

  if (connections.loading && !connections.data) {
    return (
      <div className="flex flex-col gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (usable.length === 0) {
    return (
      <div className="border-muted-foreground/30 bg-muted/30 flex flex-col items-start gap-3 rounded-md border border-dashed p-6">
        <div className="flex items-center gap-2">
          <LinkIcon className="text-muted-foreground size-5" />
          <p className="font-medium">No source connections yet</p>
        </div>
        <p className="text-muted-foreground text-sm">
          Connect a Git host (GitHub App, GitLab OAuth, or a personal-access token) so the platform
          can list your repositories and watch them for pushes.
        </p>
        <Link
          href="/settings/source-providers"
          className="text-primary text-sm underline-offset-4 hover:underline"
        >
          Set up a source provider →
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="space-y-2">
        <Label htmlFor="connection">Source connection</Label>
        <Select
          value={state.connectionId}
          onValueChange={(v) => {
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
              pushCiWorkflow: conn ? CI_PUSHABLE_KINDS.has(conn.kind) : s.pushCiWorkflow,
            }));
            setSearch("");
          }}
        >
          <SelectTrigger id="connection">
            <SelectValue placeholder="Choose a connection" />
          </SelectTrigger>
          <SelectContent>
            {usable.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                <span className="font-medium">{c.displayName || c.name}</span>{" "}
                <span className="text-muted-foreground text-xs">
                  ({connectionKindLabel(c.kind as ScmConnectionKind)})
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-muted-foreground text-xs">
          Missing a host?{" "}
          <Link
            href="/settings/source-providers"
            className="text-primary underline-offset-4 hover:underline"
          >
            Connect another source
          </Link>
          .
        </p>
      </div>

      {state.connectionId && (
        <div className="space-y-2">
          <Label htmlFor="repo-picker">Repository</Label>
          {repos.loading && !repos.data ? (
            <Skeleton className="h-10 w-full" />
          ) : repoList?.errorCode ? (
            <p className="text-destructive text-xs">
              {repoList.errorMessage ?? repoList.errorCode}
              {repoList.recoverable && (
                <>
                  {" — "}
                  <Link href="/settings/source-providers" className="underline">
                    reconnect
                  </Link>
                </>
              )}
            </p>
          ) : repoList && repoList.repos.length > 0 ? (
            <>
              <Combobox
                items={repoList.repos}
                itemToStringLabel={(r) => (r as { fullName: string }).fullName}
                value={
                  state.sourceRepo
                    ? (repoList.repos.find((r) => r.fullName === state.sourceRepo) ?? null)
                    : null
                }
                onValueChange={(v) => {
                  if (v && typeof v === "object" && "fullName" in v) {
                    pickRepo((v as { fullName: string }).fullName);
                  }
                }}
                inputValue={search}
                onInputValueChange={(v) => setSearch(v ?? "")}
              >
                <ComboboxInput
                  placeholder={`Search ${repoList.repos.length} repo${
                    repoList.repos.length === 1 ? "" : "s"
                  }…`}
                />
                <ComboboxContent>
                  <ComboboxEmpty>No matching repos.</ComboboxEmpty>
                  <ComboboxList>
                    {(item) => {
                      const r = item as {
                        fullName: string;
                        name: string;
                        visibility: string;
                        defaultBranch: string | null;
                        isFork: boolean;
                        isArchived: boolean;
                        pushedAt: string | null;
                      };
                      return (
                        <ComboboxItem key={r.fullName} value={r}>
                          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                            <span className="truncate font-mono text-xs">{r.fullName}</span>
                            <div className="text-muted-foreground flex items-center gap-2 text-[10px]">
                              <VisibilityBadge visibility={r.visibility} />
                              {r.defaultBranch && (
                                <span className="inline-flex items-center gap-1">
                                  <GitBranchIcon className="size-3" />
                                  {r.defaultBranch}
                                </span>
                              )}
                              {r.isFork && <span>fork</span>}
                              {r.isArchived && <span>archived</span>}
                              {r.pushedAt && <span>updated {formatRelative(r.pushedAt)}</span>}
                            </div>
                          </div>
                        </ComboboxItem>
                      );
                    }}
                  </ComboboxList>
                </ComboboxContent>
              </Combobox>
              {state.sourceRepo && (
                <p className="text-muted-foreground text-xs">
                  Picked{" "}
                  <code className="bg-muted rounded px-1 py-0.5 font-mono">{state.sourceRepo}</code>{" "}
                  on{" "}
                  <code className="bg-muted rounded px-1 py-0.5 font-mono">
                    {state.defaultBranch}
                  </code>
                  .
                </p>
              )}
            </>
          ) : (
            <p className="text-muted-foreground text-xs">
              No repos visible to this connection. Adjust visibility scopes on{" "}
              <Link href="/settings/source-providers" className="underline">
                /settings/source-providers
              </Link>
              .
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function connectionKindLabel(kind: ScmConnectionKind): string {
  switch (kind) {
    case "github_app_install":
      return "GitHub App";
    case "github_oauth_user":
      return "GitHub OAuth";
    case "github_pat":
      return "GitHub PAT";
    case "gitlab_oauth_user":
      return "GitLab OAuth";
    case "gitlab_pat":
      return "GitLab PAT";
    case "bitbucket_oauth_user":
      return "Bitbucket OAuth";
    case "bitbucket_pat":
      return "Bitbucket PAT";
    case "gitea_oauth_user":
      return "Gitea OAuth";
    case "gitea_pat":
      return "Gitea PAT";
    default:
      return kind;
  }
}

function VisibilityBadge({ visibility }: { visibility: string }) {
  if (visibility === "private" || visibility === "internal") {
    return (
      <Badge variant="secondary" className="gap-1 px-1 py-0 text-[10px]">
        <LockIcon className="size-2.5" /> {visibility}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="gap-1 px-1 py-0 text-[10px]">
      <UnlockIcon className="size-2.5" /> {visibility}
    </Badge>
  );
}

function formatRelative(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

function kebab(s: string): string {
  return s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}
