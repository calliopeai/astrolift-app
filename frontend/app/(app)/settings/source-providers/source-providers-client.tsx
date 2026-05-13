"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  BookOpenIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  GitBranchIcon,
  GithubIcon,
  GitlabIcon,
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
import { DOC_LINKS } from "@/lib/docs/urls";
import {
  DELETE_SSH_DEPLOY_KEY,
  DISCONNECT_SOURCE,
  ROTATE_WEBHOOK_SECRET,
} from "@/graphql/scm/scm.mutations";
import { LIST_SOURCE_CONNECTIONS, LIST_SSH_DEPLOY_KEYS } from "@/graphql/scm/scm.queries";
import type {
  AstroliftSourceConnection,
  AstroliftSshDeployKey,
  AstroliftWebhookSecretReveal,
  MutationResult,
} from "@/graphql/scm/scm.types";

import { ConnectGitHubDialog } from "./connect-github-dialog";
import { ConnectGitLabDialog } from "./connect-gitlab-dialog";
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
  const [openConnectGithub, setOpenConnectGithub] = React.useState(false);
  const [openConnectGitlab, setOpenConnectGitlab] = React.useState(false);
  const [openGenerateKey, setOpenGenerateKey] = React.useState(false);
  const [showAdvanced, setShowAdvanced] = React.useState(false);

  const conns = useQuery<ConnectionsResp>(LIST_SOURCE_CONNECTIONS);

  // Surface OAuth-callback outcomes from /app/auth1/scm/<host>/callback
  // — the dance lands the browser back here with ?scm_connected or
  // ?scm_error so we can toast and clean up the URL. The callback
  // emits a host-agnostic ?scm_connected=<login> so the toast text
  // stays generic (we don't know which host it was at this point
  // unless we plumb that through; ?scm_error is also host-agnostic).
  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    const ok = url.searchParams.get("scm_connected");
    const err = url.searchParams.get("scm_error");
    if (ok) {
      toast.success(`Connected ${ok}`);
      void conns.refetch();
    } else if (err === "app_base_url_not_public") {
      toast.error(
        "GitHub can't reach this install. Set APP_BASE_URL on the backend to your public URL (e.g. https://astrolift.example.com) and redeploy.",
        { duration: 10_000 },
      );
    } else if (err) {
      toast.error(`SCM OAuth: ${err.replace(/_/g, " ")}`);
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
    refetchQueries: [{ query: LIST_SSH_DEPLOY_KEYS, variables: { appSlug: null } }],
    awaitRefetchQueries: true,
  });

  const [revealedSecret, setRevealedSecret] = React.useState<AstroliftWebhookSecretReveal | null>(
    null
  );

  const [rotateSecret, rotateSecretState] = useMutation<{
    rotateWebhookSecret: MutationResult<AstroliftWebhookSecretReveal>;
  }>(ROTATE_WEBHOOK_SECRET, {
    refetchQueries: [{ query: LIST_SOURCE_CONNECTIONS }],
    awaitRefetchQueries: true,
  });

  async function handleRotateSecret(c: AstroliftSourceConnection) {
    if (
      !confirm(
        `Generate a fresh webhook secret for ${c.name}? Any existing webhook signed with the old secret will start failing immediately — paste the new secret into the SCM host's webhook config.`
      )
    )
      return;
    const { data } = await rotateSecret({
      variables: { input: { connectionId: c.id } },
    });
    if (data?.rotateWebhookSecret.ok && data.rotateWebhookSecret.data) {
      setRevealedSecret(data.rotateWebhookSecret.data);
    } else {
      toast.error(data?.rotateWebhookSecret.errors?.[0]?.message ?? "Rotation failed");
    }
  }

  const connectionList = conns.data?.astroliftSourceConnections ?? [];
  const keyList = keys.data?.astroliftSshDeployKeys ?? [];

  async function handleDisconnect(c: AstroliftSourceConnection) {
    if (
      !confirm(
        `Disconnect ${c.name}? Apps using this connection will lose access until you connect again.`
      )
    ) {
      return;
    }
    const { data } = await disconnect({ variables: { input: { id: c.id } } });
    if (data?.disconnectSource.ok) toast.success(`Disconnected ${c.name}`);
    else toast.error(data?.disconnectSource.errors?.[0]?.message ?? "Disconnect failed");
  }

  async function handleDeleteKey(k: AstroliftSshDeployKey) {
    if (!confirm(`Delete SSH key ${k.name}? Remove it from any repo deploy-key lists first.`))
      return;
    const { data } = await deleteKey({ variables: { input: { id: k.id } } });
    if (data?.deleteSshDeployKey.ok) toast.success(`Deleted ${k.name}`);
    else toast.error(data?.deleteSshDeployKey.errors?.[0]?.message ?? "Delete failed");
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
              One-click GitHub App registration via the manifest flow, or a guided GitLab Group
              OAuth wizard. Personal Access Tokens and pre-registered OAuth apps remain available
              under <em>Advanced</em>.
            </p>
          </div>
          <div className="flex flex-col items-end gap-1">
            <div className="flex flex-wrap justify-end gap-2">
              <Button asChild size="sm" variant="outline">
                <a href={DOC_LINKS.scmGithubOauth.primary} target="_blank" rel="noreferrer">
                  <BookOpenIcon className="size-4" />
                  Setup guide
                </a>
              </Button>
              <Can permission="scm.connect">
                <Button size="sm" onClick={() => setOpenConnectGithub(true)}>
                  <GithubIcon className="size-4" />
                  Connect to GitHub
                </Button>
                <Button size="sm" variant="secondary" onClick={() => setOpenConnectGitlab(true)}>
                  <GitlabIcon className="size-4" />
                  Connect to GitLab
                </Button>
              </Can>
            </div>
            <a
              href={DOC_LINKS.scmGithubOauth.wiki}
              target="_blank"
              rel="noreferrer"
              className="text-muted-foreground text-[11px] underline"
            >
              one-click GitHub flow + GitLab wizard — also on the wiki
            </a>
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
                description="Click 'Connect to GitHub' to register an Astrolift GitHub App on github.com in one step — no client_id / secret paste required. For GitLab, the wizard walks you through creating a Group OAuth Application with copy-friendly callback URLs."
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
                      <Badge variant="outline">{KIND_LABEL[c.kind] ?? c.kind}</Badge>
                      {c.isOauthAppConfig && (
                        <Badge variant="secondary" className="ml-2 gap-1 text-[10px]">
                          OAuth-app config
                        </Badge>
                      )}
                      {c.isPersonal && (
                        <Badge
                          variant="secondary"
                          className="ml-2 gap-1 bg-blue-500/15 text-[10px] text-blue-700 dark:text-blue-300"
                        >
                          personal {c.userUsername ? `· ${c.userUsername}` : ""}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{c.accountLogin || "—"}</TableCell>
                    <TableCell>
                      {c.repoVisibilityScopes.length === 0 ? (
                        <span className="text-muted-foreground text-xs">
                          — no scope restriction —
                        </span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {c.repoVisibilityScopes.map((s) => (
                            <Badge key={s} variant="secondary" className="text-[10px]">
                              {SCOPE_LABEL[s] ?? s}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      {c.isActive ? (
                        <Badge
                          className="gap-1 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
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
                        {/* GitHub user-to-server OAuth works against both a classic
                            OAuth App config (kind=github_oauth_app + isOauthAppConfig)
                            and a manifest-registered GitHub App install
                            (kind=github_app_install) — both have a client_id sitting
                            in oauth_client_id and GitHub's /login/oauth/authorize
                            endpoint accepts either. */}
                        {((c.kind === "github_oauth_app" && c.isOauthAppConfig) ||
                          c.kind === "github_app_install") && (
                          <Button asChild size="sm" variant="outline">
                            <a
                              href={`/app/auth1/scm/github/start?config_id=${encodeURIComponent(c.id)}&return_to=/settings/source-providers`}
                            >
                              Connect my GitHub
                            </a>
                          </Button>
                        )}
                        {c.kind === "gitlab_oauth_app" && c.isOauthAppConfig && (
                          <Button asChild size="sm" variant="outline">
                            <a
                              href={`/app/auth1/scm/gitlab/start?config_id=${encodeURIComponent(c.id)}&return_to=/settings/source-providers`}
                            >
                              Connect my GitLab
                            </a>
                          </Button>
                        )}
                        {!c.isPersonal &&
                          (c.kind.startsWith("github_") || c.kind.startsWith("gitlab_")) && (
                            <Can permission="scm.connect">
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => handleRotateSecret(c)}
                                disabled={rotateSecretState.loading}
                              >
                                Webhook secret
                              </Button>
                            </Can>
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
              ed25519 keypairs for direct git-over-SSH access. The public key is shown for you to
              paste into the repo&apos;s deploy-key settings; the private key stays encrypted in the
              platform secrets backend.
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
                        <Badge variant="secondary">app: {k.registeredAppSlug}</Badge>
                      ) : (
                        <Badge variant="outline">org-scoped</Badge>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-[11px]">{k.fingerprintSha256}</TableCell>
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

      {/* Advanced: pre-registered OAuth apps / PATs / GitHub-App-from-paste */}
      <Can permission="scm.connect">
        <div className="text-muted-foreground text-xs">
          <button
            type="button"
            onClick={() => setShowAdvanced((x) => !x)}
            className="hover:text-foreground inline-flex items-center gap-1.5 underline-offset-2 hover:underline"
          >
            {showAdvanced ? (
              <ChevronDownIcon className="size-3.5" />
            ) : (
              <ChevronRightIcon className="size-3.5" />
            )}
            Advanced: paste a Personal Access Token or pre-registered OAuth app
          </button>
          {showAdvanced && (
            <div className="mt-2 flex items-center gap-2">
              <Button size="sm" variant="outline" onClick={() => setOpenConnect(true)}>
                <PlusIcon className="size-4" />
                Paste credentials…
              </Button>
              <span className="text-muted-foreground text-xs">
                Use this if you already have a registered OAuth App or want to connect via a PAT.
              </span>
            </div>
          )}
        </div>
      </Can>

      <ConnectGitHubDialog open={openConnectGithub} onOpenChange={setOpenConnectGithub} />
      <ConnectGitLabDialog open={openConnectGitlab} onOpenChange={setOpenConnectGitlab} />
      <ConnectSourceDialog open={openConnect} onOpenChange={setOpenConnect} />
      <GenerateSshKeyDialog open={openGenerateKey} onOpenChange={setOpenGenerateKey} />
      {revealedSecret && (
        <WebhookSecretReveal reveal={revealedSecret} onClose={() => setRevealedSecret(null)} />
      )}
    </PageShell>
  );
}

function WebhookSecretReveal({
  reveal,
  onClose,
}: {
  reveal: AstroliftWebhookSecretReveal;
  onClose: () => void;
}) {
  const fullUrl =
    typeof window !== "undefined"
      ? `${window.location.origin}${reveal.webhookUrlPath}`
      : reveal.webhookUrlPath;
  async function copy(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(`${label} copied`);
    } catch {
      toast.error("Couldn't copy — select the text and copy manually");
    }
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6">
      <div className="bg-background w-full max-w-xl rounded-lg border p-6 shadow-lg">
        <h2 className="text-lg font-semibold">Webhook secret generated</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          Paste these into the SCM host&apos;s webhook config{" "}
          <span className="font-medium">now</span> — the plaintext secret is shown exactly once,
          then encrypted at rest.
        </p>
        <div className="mt-4 space-y-3">
          <div>
            <span className="text-muted-foreground block text-xs tracking-wide uppercase">
              Webhook URL
            </span>
            <div className="mt-1 flex items-center gap-2">
              <code className="bg-muted flex-1 rounded px-2 py-1 font-mono text-[11px] break-all">
                {fullUrl}
              </code>
              <Button size="sm" variant="outline" onClick={() => copy(fullUrl, "URL")}>
                copy
              </Button>
            </div>
          </div>
          <div>
            <span className="text-muted-foreground block text-xs tracking-wide uppercase">
              Secret
            </span>
            <div className="mt-1 flex items-center gap-2">
              <code className="bg-muted flex-1 rounded px-2 py-1 font-mono text-[11px] break-all">
                {reveal.plaintextSecret}
              </code>
              <Button
                size="sm"
                variant="outline"
                onClick={() => copy(reveal.plaintextSecret, "Secret")}
              >
                copy
              </Button>
            </div>
          </div>
          <p className="text-muted-foreground text-xs">
            GitHub: <code>Settings → Webhooks → Add webhook</code>. Set
            <code> Content type: application/json</code>, paste the URL + secret, choose{" "}
            <code>Just the push event</code>. GitLab: <code>Settings → Webhooks</code>, paste both,
            tick <code>Push events</code>.
          </p>
        </div>
        <div className="mt-6 flex justify-end">
          <Button onClick={onClose}>Done</Button>
        </div>
      </div>
    </div>
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
      <code className="font-mono text-[11px]">{truncated}</code>
      <Button size="sm" variant="ghost" onClick={onCopy}>
        {copied ? "copied" : "copy"}
      </Button>
    </div>
  );
}
