"use client";

import { useQuery } from "@apollo/client/react";
import {
  BotIcon,
  ChevronDownIcon,
  CogIcon,
  GitBranchIcon,
  LinkIcon,
  LockIcon,
  PlugIcon,
  UnlockIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { ScmEmptyConnectAction, ScmReauthAction } from "@/components/ScmConnectPrompt";
import { useScmConnect } from "@/components/use-scm-connect";
import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
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
import { LIST_AGENT_FLEET } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";
import { LIST_AVAILABLE_REPOS, LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import { cn } from "@/lib/utils";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
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

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

export function RepoPickerStep({ state, setState, setValid }: Props) {
  const scm = useScmConnect();
  // Reactive org id (#1022): the synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" on cold load.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";

  // Existing fleet — surfaces a "N agents already registered" badge on repo
  // rows that already host registered agents (a re-scan only adds NEW agents).
  // The register-app wizard derives its monorepo-count seam here off
  // `LIST_APPS` (RepoPickerStep.tsx:126); for agents the analogous signal is
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
      ref: branch,
      // Picking a different repo invalidates a prior scan.
      scanned: false,
      discoveredAgents: [],
      scanError: null,
    }));
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
          can list your repositories and scan them for agent manifests.
        </p>
        <ScmEmptyConnectAction scm={scm} />
      </div>
    );
  }

  const pickedConnection = usable.find((c) => c.id === state.connectionId);

  return (
    <div className="flex flex-col gap-5">
      <IntroCard />

      <div className="space-y-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="flex items-center gap-2">
            <Label htmlFor="connection">Source connection</Label>
            {pickedConnection && (
              <Badge variant="outline" className="text-2xs font-normal">
                {connectionKindLabel(pickedConnection.kind as ScmConnectionKind)}
              </Badge>
            )}
          </div>
          <Link
            href="/settings/source-providers"
            className="text-primary text-xs underline-offset-4 hover:underline"
          >
            Missing a host? Connect another source →
          </Link>
        </div>
        <Select
          value={state.connectionId}
          onValueChange={(v) => {
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
      </div>

      {state.connectionId && (
        <div className="space-y-2">
          <Label htmlFor="repo-picker">Repository</Label>
          {repos.loading && !repos.data ? (
            <Skeleton className="h-10 w-full" />
          ) : repoList?.errorCode ? (
            <div className="text-destructive flex flex-col items-start gap-1 text-xs">
              <span>{repoList.errorMessage ?? repoList.errorCode}</span>
              {repoList.recoverable && (
                <ScmReauthAction connectionKind={pickedConnection?.kind ?? ""} scm={scm} />
              )}
            </div>
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
                      const existingCount = repoToAgentCount.get(r.fullName) ?? 0;
                      return (
                        <ComboboxItem key={r.fullName} value={r}>
                          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                            <div className="flex min-w-0 items-center gap-2">
                              <span className="truncate font-mono text-xs">{r.fullName}</span>
                              {existingCount > 0 && (
                                <Badge
                                  variant="secondary"
                                  className="text-2xs shrink-0 gap-1 px-1 py-0"
                                  title={`This repo already hosts ${existingCount} registered agent${existingCount === 1 ? "" : "s"}. Re-scanning adds only newly-added agents.`}
                                >
                                  {existingCount === 1
                                    ? "1 agent already registered"
                                    : `${existingCount} agents already registered`}
                                </Badge>
                              )}
                            </div>
                            <div className="text-muted-foreground text-2xs flex items-center gap-2">
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
                <div className="space-y-2">
                  <p className="text-muted-foreground text-xs">
                    Picked{" "}
                    <code className="bg-muted rounded px-1 py-0.5 font-mono">
                      {state.sourceRepo}
                    </code>
                    .
                  </p>
                  <div className="space-y-1">
                    <Label htmlFor="agent-ref">Branch / ref to scan</Label>
                    <input
                      id="agent-ref"
                      value={state.ref}
                      onChange={(e) => {
                        const v = e.target.value;
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
                      }}
                      className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-9 w-full rounded-md border px-3 py-1 font-mono text-xs shadow-xs transition-colors focus-visible:ring-1 focus-visible:outline-none"
                    />
                    <p className="text-muted-foreground text-xs">
                      The branch the next step scans for{" "}
                      <code className="font-mono">agents/*/astrolift.toml</code> and a root{" "}
                      <code className="font-mono">astrolift.toml</code>. Defaults to the
                      repository&apos;s default branch.
                    </p>
                  </div>
                </div>
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
      <Badge variant="secondary" className="text-2xs gap-1 px-1 py-0">
        <LockIcon className="size-2.5" /> {visibility}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-2xs gap-1 px-1 py-0">
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

/**
 * Collapsible orientation card shown above the first step body. Mirrors the
 * register-app wizard's intro, retargeted to agent discovery.
 */
function IntroCard() {
  const [open, setOpen] = React.useState(true);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="bg-muted/30 rounded-md border">
      <CollapsibleTrigger className="hover:bg-muted/50 flex w-full items-center justify-between gap-3 rounded-md px-4 py-3 text-left transition-colors">
        <div className="flex flex-col">
          <span className="text-sm font-medium">How agent registration works</span>
          <span className="text-muted-foreground text-xs">
            Three quick steps: connect a Git source, scan the repo for agent manifests, then
            register the agents you discover.
          </span>
        </div>
        <ChevronDownIcon
          className={cn(
            "text-muted-foreground size-4 shrink-0 transition-transform",
            open && "rotate-180"
          )}
        />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="grid gap-3 px-4 pb-4 md:grid-cols-3">
          <IntroTile
            icon={PlugIcon}
            title="Connect"
            description="Pick a source connection and the repo that holds your agent manifests."
          />
          <IntroTile
            icon={BotIcon}
            title="Discover"
            description="We scan agents/*/astrolift.toml and the root manifest, then list every agent we find."
          />
          <IntroTile
            icon={CogIcon}
            title="Register"
            description="Pick a project and register the repo's agents — each becomes an agent workload on the Agents list."
          />
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function IntroTile({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof PlugIcon;
  title: string;
  description: string;
}) {
  return (
    <div className="bg-background flex flex-col gap-2 rounded-md border p-3">
      <div className="bg-primary/10 text-primary flex size-7 items-center justify-center rounded-md">
        <Icon className="size-4" />
      </div>
      <div>
        <p className="text-sm font-medium">{title}</p>
        <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">{description}</p>
      </div>
    </div>
  );
}
