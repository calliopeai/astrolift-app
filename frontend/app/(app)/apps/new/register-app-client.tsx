"use client";

import { useLazyQuery, useMutation, useQuery } from "@apollo/client/react";
import { CheckIcon, FileTextIcon, GitBranchIcon, RocketIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import type { AstroliftProject } from "@/graphql/identity/identity.types";
import { REGISTER_APP } from "@/graphql/registry/registry.mutations";
import { LIST_APPS } from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredApp,
  SourceKind,
} from "@/graphql/registry/registry.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  GET_SOURCE_FILE,
  LIST_AVAILABLE_REPOS,
  LIST_SOURCE_CONNECTIONS,
} from "@/graphql/scm/scm.queries";
import type {
  AstroliftRemoteRepoList,
  AstroliftSourceConnection,
  AstroliftSourceFile,
} from "@/graphql/scm/scm.types";

interface ProjectsResp {
  astroliftProjects: AstroliftProject[];
}

interface ConnectionsResp {
  astroliftSourceConnections: AstroliftSourceConnection[];
}

interface ReposResp {
  astroliftAvailableRepos: AstroliftRemoteRepoList;
}

interface SourceFileResp {
  astroliftSourceFile: AstroliftSourceFile;
}

const KIND_TO_SOURCE_KIND: Record<string, SourceKind> = {
  github_pat: "github",
  github_app_install: "github",
  github_oauth_app: "github",
  gitlab_pat: "gitlab",
  gitlab_oauth_app: "gitlab",
  bitbucket_pat: "bitbucket",
  bitbucket_oauth_app: "bitbucket",
  gitea_pat: "gitea",
  gitea_oauth_app: "gitea",
};

const slugify = (s: string) =>
  s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);

const SOURCE_KINDS: { value: SourceKind; label: string }[] = [
  { value: "github", label: "GitHub" },
  { value: "gitlab", label: "GitLab" },
  { value: "bitbucket", label: "Bitbucket" },
  { value: "gitea", label: "Gitea" },
  { value: "git_url", label: "Git URL" },
];

export function RegisterAppClient() {
  const router = useRouter();
  const projects = useQuery<ProjectsResp>(LIST_PROJECTS);
  const connections = useQuery<ConnectionsResp>(LIST_SOURCE_CONNECTIONS);

  const [projectId, setProjectId] = React.useState("");
  const [name, setName] = React.useState("");
  const [slug, setSlug] = React.useState("");
  const [slugTouched, setSlugTouched] = React.useState(false);
  const [description, setDescription] = React.useState("");
  const [sourceKind, setSourceKind] = React.useState<SourceKind>("github");
  const [sourceRepo, setSourceRepo] = React.useState("");
  const [sourceUrl, setSourceUrl] = React.useState("");
  const [manifestPath, setManifestPath] = React.useState("astrolift.toml");
  const [defaultBranch, setDefaultBranch] = React.useState("main");
  const [deployBranch, setDeployBranch] = React.useState("main");
  const [manifestRaw, setManifestRaw] = React.useState("");
  const [manifestTouched, setManifestTouched] = React.useState(false);
  const [manifestFetchState, setManifestFetchState] = React.useState<
    "idle" | "fetching" | "loaded" | "missing" | "error"
  >("idle");
  const [manifestFetchMessage, setManifestFetchMessage] = React.useState("");

  // Connected-host picker. The operator can paste a clone URL the
  // old way OR pick a stored connection and choose a repo from it.
  // Picking a connection auto-fills sourceKind/sourceRepo/sourceUrl/
  // defaultBranch from the chosen repo and (when the manifest is
  // present at the default branch) the manifest textarea.
  const [pickerConnectionId, setPickerConnectionId] = React.useState("");
  const [pickerSearch, setPickerSearch] = React.useState("");

  const repoListable = pickerConnectionId !== "";
  const repos = useQuery<ReposResp>(LIST_AVAILABLE_REPOS, {
    variables: {
      connectionId: pickerConnectionId,
      search: pickerSearch || null,
      limit: 100,
    },
    skip: !repoListable,
  });

  const [fetchManifest] = useLazyQuery<SourceFileResp>(GET_SOURCE_FILE, {
    fetchPolicy: "network-only",
  });

  const usableConnections = (
    connections.data?.astroliftSourceConnections ?? []
  ).filter((c) => c.isActive && !c.isOauthAppConfig);
  const repoList = repos.data?.astroliftAvailableRepos;

  async function tryAutoFetchManifest(args: {
    connectionId: string;
    repoFullName: string;
    branch: string;
    path: string;
  }) {
    setManifestFetchState("fetching");
    setManifestFetchMessage("");
    const { data, error } = await fetchManifest({
      variables: {
        connectionId: args.connectionId,
        repoFullName: args.repoFullName,
        path: args.path,
        ref: args.branch,
      },
    });
    if (error) {
      setManifestFetchState("error");
      setManifestFetchMessage(error.message);
      return;
    }
    const f = data?.astroliftSourceFile;
    if (!f) {
      setManifestFetchState("error");
      setManifestFetchMessage("no response from server");
      return;
    }
    if (f.errorCode) {
      setManifestFetchState("error");
      setManifestFetchMessage(f.errorMessage ?? f.errorCode);
      return;
    }
    if (f.content == null) {
      setManifestFetchState("missing");
      setManifestFetchMessage(`${args.path} not found on ${args.branch}`);
      return;
    }
    setManifestFetchState("loaded");
    setManifestFetchMessage(
      `Loaded ${args.path} from ${args.repoFullName}@${args.branch}`,
    );
    // Don't clobber an operator's manual edit.
    if (!manifestTouched) {
      setManifestRaw(f.content);
    }
  }

  function pickRepo(fullName: string) {
    const repo = repoList?.repos.find((r) => r.fullName === fullName);
    if (!repo) return;
    const conn = usableConnections.find((c) => c.id === pickerConnectionId);
    const inferredKind = conn
      ? (KIND_TO_SOURCE_KIND[conn.kind] ?? "git_url")
      : "git_url";
    setSourceKind(inferredKind);
    setSourceRepo(repo.fullName);
    setSourceUrl(repo.cloneUrlHttps || repo.cloneUrlSsh || "");
    const branch = repo.defaultBranch || "main";
    setDefaultBranch(branch);
    setDeployBranch(branch);
    if (!name) {
      setName(repo.name);
    }
    if (!slugTouched && !slug) {
      setSlug(slugify(repo.name));
    }
    // Best-effort manifest auto-fetch from the default branch. The
    // wizard's manifest path defaults to astrolift.toml; operators
    // who keep theirs elsewhere can edit the path field and re-pick.
    void tryAutoFetchManifest({
      connectionId: pickerConnectionId,
      repoFullName: repo.fullName,
      branch,
      path: manifestPath || "astrolift.toml",
    });
  }

  React.useEffect(() => {
    const list = projects.data?.astroliftProjects;
    if (list && list.length > 0 && !projectId) {
      setProjectId(list[0].id);
    }
  }, [projects.data, projectId]);

  const [registerApp, { loading }] = useMutation<{
    registerApp: MutationResult<AstroliftRegisteredApp>;
  }>(REGISTER_APP, {
    refetchQueries: [{ query: LIST_APPS }],
    awaitRefetchQueries: true,
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!projectId) return;
    const finalSlug = slug || slugify(name);
    const { data } = await registerApp({
      variables: {
        input: {
          projectId,
          name: name.trim(),
          slug: finalSlug,
          description: description.trim() || null,
          sourceKind,
          sourceRepo: sourceRepo.trim(),
          sourceUrl: sourceUrl.trim() || null,
          manifestPath: manifestPath.trim() || "astrolift.toml",
          manifestRaw: manifestRaw.trim() ? manifestRaw : null,
          defaultBranch: defaultBranch.trim() || "main",
          deployBranch: deployBranch.trim() || "main",
        },
      },
    });
    if (data?.registerApp.ok && data.registerApp.data) {
      toast.success(`Registered ${data.registerApp.data.slug}`);
      router.push(`/apps/${data.registerApp.data.slug}`);
    } else {
      toast.error(data?.registerApp.errors?.[0]?.message ?? "Register failed");
    }
  }

  const projectsList = projects.data?.astroliftProjects ?? [];

  return (
    <PageShell
      title="Register app"
      description="Connect a Git repository to the platform. Once registered, OnboardAppWorkflow provisions the registry repo, a Kubernetes namespace, workload identity, and the initial managed services."
    >
      <form onSubmit={submit} className="grid gap-4">
        <Card>
          <CardHeader>
            <CardTitle>Identity</CardTitle>
            <CardDescription>
              Where this app lives in the project hierarchy.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 sm:max-w-2xl">
            <div className="space-y-2">
              <Label htmlFor="project">Project</Label>
              <Select value={projectId} onValueChange={setProjectId}>
                <SelectTrigger id="project">
                  <SelectValue
                    placeholder={
                      projectsList.length === 0
                        ? "No projects — create one first"
                        : "Select a project"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {projectsList.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.team.slug}/{p.slug}{" "}
                      <span className="text-muted-foreground">— {p.name}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="name">Display name</Label>
                <Input
                  id="name"
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value);
                    if (!slugTouched) setSlug(slugify(e.target.value));
                  }}
                  placeholder="API Gateway"
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="slug">Slug</Label>
                <Input
                  id="slug"
                  value={slug}
                  onChange={(e) => {
                    setSlug(e.target.value);
                    setSlugTouched(true);
                  }}
                  placeholder="api-gateway"
                  pattern="[a-z0-9-]+"
                  required
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="description">Description (optional)</Label>
              <Textarea
                id="description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="What does this app do?"
                rows={2}
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Source</CardTitle>
            <CardDescription>
              The Git repository the platform watches for pushes. Pick from
              a connected host below, or paste a clone URL manually.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 sm:max-w-2xl">
            {usableConnections.length > 0 ? (
              <div className="rounded-md border bg-muted/30 p-4">
                <Label className="text-xs uppercase tracking-wide text-muted-foreground">
                  Pick from a connected host
                </Label>
                <div className="mt-2 grid gap-3 sm:grid-cols-2">
                  <Select
                    value={pickerConnectionId}
                    onValueChange={(v) => {
                      setPickerConnectionId(v);
                      setPickerSearch("");
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Choose a connection" />
                    </SelectTrigger>
                    <SelectContent>
                      {usableConnections.map((c) => (
                        <SelectItem key={c.id} value={c.id}>
                          {c.name}{" "}
                          <span className="text-muted-foreground text-xs">
                            ({c.kind})
                          </span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {repoListable && (
                  <div className="mt-3">
                    {repos.loading ? (
                      <p className="text-muted-foreground text-xs">
                        Loading repos…
                      </p>
                    ) : repoList?.errorCode ? (
                      <p className="text-destructive text-xs">
                        {repoList.errorMessage ?? repoList.errorCode}
                        {repoList.recoverable && (
                          <>
                            {" "}
                            <Link
                              href="/settings/source-providers"
                              className="underline"
                            >
                              reconnect
                            </Link>
                          </>
                        )}
                      </p>
                    ) : repoList && repoList.repos.length > 0 ? (
                      <Combobox
                        items={repoList.repos}
                        itemToStringLabel={(r) =>
                          (r as { fullName: string }).fullName
                        }
                        value={
                          sourceRepo
                            ? (repoList.repos.find(
                                (r) => r.fullName === sourceRepo,
                              ) ?? null)
                            : null
                        }
                        onValueChange={(v) => {
                          if (v && typeof v === "object" && "fullName" in v) {
                            pickRepo((v as { fullName: string }).fullName);
                          }
                        }}
                        inputValue={pickerSearch}
                        onInputValueChange={(v) => setPickerSearch(v ?? "")}
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
                                visibility: string;
                                isFork: boolean;
                                isArchived: boolean;
                              };
                              return (
                                <ComboboxItem key={r.fullName} value={r}>
                                  <span className="font-mono text-xs">
                                    {r.fullName}
                                  </span>
                                  <span className="text-muted-foreground ml-auto text-[10px]">
                                    {r.visibility}
                                    {r.isFork && ", fork"}
                                    {r.isArchived && ", archived"}
                                  </span>
                                </ComboboxItem>
                              );
                            }}
                          </ComboboxList>
                        </ComboboxContent>
                      </Combobox>
                    ) : (
                      <p className="text-muted-foreground text-xs">
                        No repos visible to this connection. Adjust the
                        visibility scopes on{" "}
                        <Link
                          href="/settings/source-providers"
                          className="underline"
                        >
                          /settings/source-providers
                        </Link>
                        .
                      </p>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <p className="text-muted-foreground text-xs">
                No source connections yet.{" "}
                <Link
                  href="/settings/source-providers"
                  className="underline"
                >
                  Connect a host
                </Link>{" "}
                to pick from a repo list, or fill in the form below
                manually.
              </p>
            )}

            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-2">
                <Label htmlFor="source-kind">Provider</Label>
                <Select
                  value={sourceKind}
                  onValueChange={(v) => setSourceKind(v as SourceKind)}
                >
                  <SelectTrigger id="source-kind">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SOURCE_KINDS.map((k) => (
                      <SelectItem key={k.value} value={k.value}>
                        {k.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2 sm:col-span-2">
                <Label htmlFor="source-repo">
                  {sourceKind === "git_url" ? "Repo URL" : "Repo (owner/name)"}
                </Label>
                <Input
                  id="source-repo"
                  value={sourceRepo}
                  onChange={(e) => setSourceRepo(e.target.value)}
                  placeholder={
                    sourceKind === "git_url"
                      ? "https://git.example/team/repo.git"
                      : "acme/api-gateway"
                  }
                  required
                />
              </div>
            </div>

            {sourceKind !== "git_url" && (
              <div className="space-y-2">
                <Label htmlFor="source-url">Clone URL (optional)</Label>
                <Input
                  id="source-url"
                  value={sourceUrl}
                  onChange={(e) => setSourceUrl(e.target.value)}
                  placeholder="https://github.com/acme/api-gateway.git"
                />
              </div>
            )}

            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-2">
                <Label htmlFor="manifest-path">Manifest path</Label>
                <Input
                  id="manifest-path"
                  value={manifestPath}
                  onChange={(e) => setManifestPath(e.target.value)}
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="default-branch">Default branch</Label>
                <Input
                  id="default-branch"
                  value={defaultBranch}
                  onChange={(e) => setDefaultBranch(e.target.value)}
                  placeholder="main"
                  className="font-mono text-xs"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="deploy-branch">Deploy branch</Label>
                <Input
                  id="deploy-branch"
                  value={deployBranch}
                  onChange={(e) => setDeployBranch(e.target.value)}
                  placeholder="main"
                  className="font-mono text-xs"
                />
              </div>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label htmlFor="manifest-raw">Manifest preview</Label>
                <div className="flex items-center gap-2">
                  <ManifestFetchBadge
                    state={manifestFetchState}
                    message={manifestFetchMessage}
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={
                      !pickerConnectionId ||
                      !sourceRepo ||
                      manifestFetchState === "fetching"
                    }
                    onClick={() =>
                      tryAutoFetchManifest({
                        connectionId: pickerConnectionId,
                        repoFullName: sourceRepo,
                        branch: defaultBranch || "main",
                        path: manifestPath || "astrolift.toml",
                      })
                    }
                  >
                    <FileTextIcon className="size-4" />
                    {manifestFetchState === "fetching" ? "Fetching…" : "Fetch from repo"}
                  </Button>
                </div>
              </div>
              <Textarea
                id="manifest-raw"
                value={manifestRaw}
                onChange={(e) => {
                  setManifestRaw(e.target.value);
                  setManifestTouched(true);
                }}
                placeholder={
                  pickerConnectionId
                    ? "Pick a repo to auto-fetch astrolift.toml from the default branch — or paste your manifest here."
                    : "Paste astrolift.toml here, or pick a connected source above to auto-fetch it."
                }
                rows={10}
                className="font-mono text-xs"
              />
              <p className="text-muted-foreground text-xs">
                Optional. If empty, the onboarding workflow fetches the
                manifest from <code>{manifestPath || "astrolift.toml"}</code>{" "}
                on the default branch after registration.
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>What happens next</CardTitle>
          </CardHeader>
          <CardContent>
            <ol className="text-muted-foreground space-y-2 text-sm">
              <li className="flex gap-3">
                <RocketIcon className="text-primary mt-0.5 size-4 shrink-0" />
                <span>
                  An <code className="font-mono">OnboardAppWorkflow</code> Temporal
                  run starts and provisions the registry repo, namespace,
                  network policy, and ServiceAccount.
                </span>
              </li>
              <li className="flex gap-3">
                <GitBranchIcon className="text-primary mt-0.5 size-4 shrink-0" />
                <span>
                  We fetch the manifest at the default branch and persist the
                  raw + normalized form against the new app.
                </span>
              </li>
              <li className="flex gap-3">
                <RocketIcon className="text-primary mt-0.5 size-4 shrink-0" />
                <span>
                  Managed services declared in the manifest are provisioned in
                  parallel; once all reach <code className="font-mono">active</code>{" "}
                  the app flips to <code className="font-mono">ready</code>.
                </span>
              </li>
            </ol>
          </CardContent>
        </Card>

        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={() => router.push("/apps")}>
            Cancel
          </Button>
          <Button type="submit" disabled={loading || !projectId || !name || !sourceRepo}>
            <RocketIcon className="size-4" />
            {loading ? "Registering…" : "Register app"}
          </Button>
        </div>
      </form>
    </PageShell>
  );
}

function ManifestFetchBadge({
  state,
  message,
}: {
  state: "idle" | "fetching" | "loaded" | "missing" | "error";
  message: string;
}) {
  if (state === "idle") return null;
  if (state === "fetching") {
    return (
      <span className="text-muted-foreground text-[11px]">
        Fetching manifest…
      </span>
    );
  }
  if (state === "loaded") {
    return (
      <span className="text-emerald-600 dark:text-emerald-400 inline-flex items-center gap-1 text-[11px]">
        <CheckIcon className="size-3" />
        {message}
      </span>
    );
  }
  if (state === "missing") {
    return (
      <span className="text-amber-600 dark:text-amber-400 text-[11px]">
        Couldn&apos;t find a manifest at that path — paste yours below.
      </span>
    );
  }
  return (
    <span className="text-destructive text-[11px]" title={message}>
      Couldn&apos;t fetch manifest — {message}
    </span>
  );
}
