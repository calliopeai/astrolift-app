"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  BookOpenIcon,
  CheckCircle2Icon,
  GitBranchIcon,
  KeyRoundIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  DELETE_SSH_DEPLOY_KEY,
  DISCONNECT_SOURCE,
} from "@/graphql/scm/scm.mutations";
import {
  LIST_SOURCE_CONNECTIONS,
  LIST_SSH_DEPLOY_KEYS,
} from "@/graphql/scm/scm.queries";
import type {
  AstroliftSourceConnection,
  AstroliftSshDeployKey,
  MutationResult,
} from "@/graphql/scm/scm.types";

import { ConnectSourceDialog } from "./connect-source-dialog";
import { GenerateSshKeyDialog } from "./generate-ssh-key-dialog";

const KIND_LABEL: Record<string, string> = {
  github_oauth_app: "GitHub OAuth App",
  github_oauth_user: "GitHub (OAuth user token)",
  github_app_install: "GitHub App install",
  github_pat: "GitHub PAT",
  gitlab_oauth_app: "GitLab OAuth App",
  gitlab_oauth_user: "GitLab (OAuth user token)",
  gitlab_pat: "GitLab PAT",
  bitbucket_oauth_app: "Bitbucket OAuth App",
  bitbucket_oauth_user: "Bitbucket (OAuth user token)",
  bitbucket_pat: "Bitbucket PAT",
  gitea_oauth_app: "Gitea OAuth App",
  gitea_oauth_user: "Gitea (OAuth user token)",
  gitea_pat: "Gitea PAT",
};

const SCOPE_LABEL: Record<string, string> = {
  private_org: "private org repos",
  public_org: "public org repos",
  user_repos: "user repos",
  public_non_org: "public non-org repos",
};

interface ConnectionsResp {
  astroliftSourceConnections: AstroliftSourceConnection[];
}
interface KeysResp {
  astroliftSshDeployKeys: AstroliftSshDeployKey[];
}

export function SourceProvidersClient() {
  const [openConnect, setOpenConnect] = React.useState(false);
  const [openGenerateKey, setOpenGenerateKey] = React.useState(false);

  const conns = useQuery<ConnectionsResp>(LIST_SOURCE_CONNECTIONS);

  // Surface OAuth-callback outcomes from /app/auth1/scm/github/callback
  // — the dance lands the browser back here with ?scm_connected or
  // ?scm_error so we can toast and clean up the URL.
  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    const ok = url.searchParams.get("scm_connected");
    const err = url.searchParams.get("scm_error");
    if (ok) {
      toast.success(`Connected GitHub: ${ok}`);
      void conns.refetch();
    } else if (err) {
      toast.error(`GitHub OAuth: ${err.replace(/_/g, " ")}`);
    }
    if (ok || err) {
      url.searchParams.delete("scm_connected");
      url.searchParams.delete("scm_error");
      window.history.replaceState({}, "", url.toString());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const keys = useQuery<KeysResp>(LIST_SSH_DEPLOY_KEYS, {
    variables: { appSlug: null },
  });

  const [disconnect, disconnectState] = useMutation<{
    disconnectSource: MutationResult<{ id: string }>;
  }>(DISCONNECT_SOURCE, {
    refetchQueries: [{ query: LIST_SOURCE_CONNECTIONS }],
    awaitRefetchQueries: true,
  });

  const [deleteKey, deleteKeyState] = useMutation<{
    deleteSshDeployKey: MutationResult<{ id: string }>;
  }>(DELETE_SSH_DEPLOY_KEY, {
    refetchQueries: [
      { query: LIST_SSH_DEPLOY_KEYS, variables: { appSlug: null } },
    ],
    awaitRefetchQueries: true,
  });

  const connectionList = conns.data?.astroliftSourceConnections ?? [];
  const keyList = keys.data?.astroliftSshDeployKeys ?? [];

  async function handleDisconnect(c: AstroliftSourceConnection) {
    if (
      !confirm(
        `Disconnect ${c.name}? Apps using this connection will lose access until you connect again.`,
      )
    ) {
      return;
    }
    const { data } = await disconnect({ variables: { input: { id: c.id } } });
    if (data?.disconnectSource.ok) toast.success(`Disconnected ${c.name}`);
    else
      toast.error(
        data?.disconnectSource.errors?.[0]?.message ?? "Disconnect failed",
      );
  }

  async function handleDeleteKey(k: AstroliftSshDeployKey) {
    if (
      !confirm(
        `Delete SSH key ${k.name}? Remove it from any repo deploy-key lists first.`,
      )
    )
      return;
    const { data } = await deleteKey({ variables: { input: { id: k.id } } });
    if (data?.deleteSshDeployKey.ok) toast.success(`Deleted ${k.name}`);
    else
      toast.error(
        data?.deleteSshDeployKey.errors?.[0]?.message ?? "Delete failed",
      );
  }

  return (
    <PageShell
      title="Source providers"
      description="Connect Astrolift to a code-hosting service so it can clone your repos and watch for pushes. Use OAuth Apps / GitHub Apps for org-wide access, PATs for self-hosted GitLab or Gitea, and SSH deploy keys for direct git access to any host."
    >
      {/* Connections */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <GitBranchIcon className="size-4" />
              Hosts
            </CardTitle>
            <p className="text-muted-foreground mt-1 text-xs">
              GitHub, GitLab, Bitbucket, Gitea — OAuth App config or PAT.
              Visibility scopes constrain what repos this connection can
              surface (e.g. private org repos only).
            </p>
          </div>
          <div className="flex gap-2">
            <Button asChild size="sm" variant="outline">
              <a
                href="https://github.com/calliopeai/astrolift-app/blob/main/docs/operators/scm-github-oauth.md"
                target="_blank"
                rel="noreferrer"
              >
                <BookOpenIcon className="size-4" />
                Setup guide
              </a>
            </Button>
            <Can permission="scm.connect">
              <Button size="sm" onClick={() => setOpenConnect(true)}>
                <PlusIcon className="size-4" />
                Connect host
              </Button>
            </Can>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {conns.loading && connectionList.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : connectionList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<GitBranchIcon className="size-5" />}
                title="No hosts connected yet"
                description="Connect GitHub or GitLab so Astrolift can clone your repos. Phase 1 supports OAuth-app config + PAT paste; the OAuth dance and repo browser ship in phase 2."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Connection</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Account</TableHead>
                  <TableHead>Scopes</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {connectionList.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell>
                      <div className="font-medium">{c.name}</div>
                      {c.apiBaseUrl && (
                        <div className="text-muted-foreground font-mono text-xs">
                          {c.apiBaseUrl}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">
                        {KIND_LABEL[c.kind] ?? c.kind}
                      </Badge>
                      {c.isOauthAppConfig && (
                        <Badge
                          variant="secondary"
                          className="ml-2 gap-1 text-[10px]"
                        >
                          OAuth-app config
                        </Badge>
                      )}
                      {c.isPersonal && (
                        <Badge
                          variant="secondary"
                          className="ml-2 gap-1 text-[10px] bg-blue-500/15 text-blue-700 dark:text-blue-300"
                        >
                          personal {c.userUsername ? `· ${c.userUsername}` : ""}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {c.accountLogin || "—"}
                    </TableCell>
                    <TableCell>
                      {c.repoVisibilityScopes.length === 0 ? (
                        <span className="text-muted-foreground text-xs">
                          — no scope restriction —
                        </span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {c.repoVisibilityScopes.map((s) => (
                            <Badge
                              key={s}
                              variant="secondary"
                              className="text-[10px]"
                            >
                              {SCOPE_LABEL[s] ?? s}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      {c.isActive ? (
                        <Badge
                          className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 gap-1"
                          variant="secondary"
                        >
                          <CheckCircle2Icon className="size-3" />
                          active
                        </Badge>
                      ) : (
                        <Badge variant="outline">disabled</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        {c.kind === "github_oauth_app" && c.isOauthAppConfig && (
                          <Button
                            asChild
                            size="sm"
                            variant="outline"
                          >
                            <a
                              href={`/app/auth1/scm/github/start?config_id=${encodeURIComponent(c.id)}&return_to=/settings/source-providers`}
                            >
                              Connect my GitHub
                            </a>
                          </Button>
                        )}
                        <Can permission="scm.disconnect">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => handleDisconnect(c)}
                            disabled={disconnectState.loading}
                          >
                            <Trash2Icon className="size-4" />
                            <span className="sr-only">Disconnect</span>
                          </Button>
                        </Can>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* SSH keys */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <KeyRoundIcon className="size-4" />
              SSH deploy keys
            </CardTitle>
            <p className="text-muted-foreground mt-1 text-xs">
              ed25519 keypairs for direct git-over-SSH access. The public key
              is shown for you to paste into the repo&apos;s deploy-key
              settings; the private key stays encrypted in the platform
              secrets backend.
            </p>
          </div>
          <Can permission="scm.key_create">
            <Button size="sm" onClick={() => setOpenGenerateKey(true)}>
              <PlusIcon className="size-4" />
              Generate key
            </Button>
          </Can>
        </CardHeader>
        <CardContent className="p-0">
          {keys.loading && keyList.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
            </div>
          ) : keyList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<KeyRoundIcon className="size-5" />}
                title="No SSH deploy keys yet"
                description="Generate a key to let Astrolift clone over SSH. Org-scoped keys cover every app; per-app keys offer tighter blast-radius."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Scope</TableHead>
                  <TableHead>Fingerprint</TableHead>
                  <TableHead>Public key</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {keyList.map((k) => (
                  <TableRow key={k.id}>
                    <TableCell className="font-medium">{k.name}</TableCell>
                    <TableCell>
                      {k.registeredAppSlug ? (
                        <Badge variant="secondary">
                          app: {k.registeredAppSlug}
                        </Badge>
                      ) : (
                        <Badge variant="outline">org-scoped</Badge>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-[11px]">
                      {k.fingerprintSha256}
                    </TableCell>
                    <TableCell>
                      <PublicKeyCell value={k.publicKey} />
                    </TableCell>
                    <TableCell className="text-right">
                      <Can permission="scm.key_delete">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => handleDeleteKey(k)}
                          disabled={deleteKeyState.loading}
                        >
                          <Trash2Icon className="size-4" />
                          <span className="sr-only">Delete</span>
                        </Button>
                      </Can>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ConnectSourceDialog open={openConnect} onOpenChange={setOpenConnect} />
      <GenerateSshKeyDialog
        open={openGenerateKey}
        onOpenChange={setOpenGenerateKey}
      />
    </PageShell>
  );
}

function PublicKeyCell({ value }: { value: string }) {
  const [copied, setCopied] = React.useState(false);
  const onCopy = React.useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      toast.error("Couldn't copy — paste manually below");
    }
  }, [value]);
  const truncated = value.length > 60 ? `${value.slice(0, 60)}…` : value;
  return (
    <div className="flex items-center gap-2">
      <code className="text-[11px] font-mono">{truncated}</code>
      <Button size="sm" variant="ghost" onClick={onCopy}>
        {copied ? "copied" : "copy"}
      </Button>
    </div>
  );
}
