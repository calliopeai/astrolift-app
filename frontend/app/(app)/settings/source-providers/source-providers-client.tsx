"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
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
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ListControls } from "@/components/ListControls";
import { useListControls } from "@/hooks/use-list-controls";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
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

import { useLocalStorage } from "@/hooks/use-local-storage";

import { AddClientIdDialog } from "./add-client-id-dialog";
import { ConnectGitHubDialog } from "./connect-github-dialog";
import { ConnectGitLabDialog } from "./connect-gitlab-dialog";
import { ConnectSourceDialog } from "./connect-source-dialog";
import { GenerateSshKeyDialog } from "./generate-ssh-key-dialog";

// localStorage key for the Advanced (paste-credentials) toggle.
// Persisting it removes a chore from re-opening the page after a
// config session: operators don't have to re-expand to get back to
// the form they were just in.
const ADVANCED_OPEN_STORAGE_KEY = "astrolift.settings.source-providers.advancedOpen";

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

export function SourceProvidersPanel() {
  const [openConnect, setOpenConnect] = React.useState(false);
  const [openConnectGithub, setOpenConnectGithub] = React.useState(false);
  const [openConnectGitlab, setOpenConnectGitlab] = React.useState(false);
  const [openGenerateKey, setOpenGenerateKey] = React.useState(false);
  const [showAdvanced, setShowAdvanced] = useLocalStorage<boolean>(
    ADVANCED_OPEN_STORAGE_KEY,
    false
  );
  const [disconnectTarget, setDisconnectTarget] = React.useState<AstroliftSourceConnection | null>(
    null
  );
  const [deleteKeyTarget, setDeleteKeyTarget] = React.useState<AstroliftSshDeployKey | null>(null);

  const conns = useQuery<ConnectionsResp>(LIST_SOURCE_CONNECTIONS);

  // OAuth-callback outcome toasts moved to a layout-level component
  // (#759, `components/ScmCallbackToast`) so they surface regardless
  // of where `return_to` lands. Refetching the list when we land on
  // this page with a success param is now the only page-specific
  // bit; the toast handler in the layout consumes the param before
  // we run, so we can't read it directly. The query already
  // cache-and-network-fetches on mount, so the row state is up to
  // date without an explicit refetch hook here.
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

  const [rotateSecretTarget, setRotateSecretTarget] =
    React.useState<AstroliftSourceConnection | null>(null);

  // Connection currently being edited to add a missing Client ID (#525
  // recovery flow). NULL when the dialog is closed.
  const [clientIdTarget, setClientIdTarget] =
    React.useState<AstroliftSourceConnection | null>(null);

  async function handleRotateSecret(c: AstroliftSourceConnection) {
    const { data } = await rotateSecret({
      variables: { input: { connectionId: c.id } },
    });
    if (data?.rotateWebhookSecret.ok && data.rotateWebhookSecret.data) {
      setRevealedSecret(data.rotateWebhookSecret.data);
    } else {
      throw new Error(data?.rotateWebhookSecret.errors?.[0]?.message ?? "Rotation failed");
    }
  }

  const allConnections = conns.data?.astroliftSourceConnections ?? [];
  const connCtrl = useListControls({
    data: allConnections,
    searchFn: (c) => [c.kind, c.apiBaseUrl, c.accountLogin, c.displayName].join(" "),
    initialPageSize: 25,
  });
  const connectionList = connCtrl.rows;
  const allKeys = keys.data?.astroliftSshDeployKeys ?? [];
  const keyCtrl = useListControls({
    data: allKeys,
    searchFn: (k) => [k.name, k.fingerprintSha256, k.registeredAppSlug ?? ""].join(" "),
    initialPageSize: 25,
  });
  const keyList = keyCtrl.rows;
  const incompleteClientIdConnections = allConnections.filter((c) => c.needsClientId);

  async function handleDisconnect(c: AstroliftSourceConnection) {
    const { data } = await disconnect({ variables: { input: { id: c.id } } });
    if (data?.disconnectSource.ok) toast.success(`Disconnected ${c.name}`);
    else throw new Error(data?.disconnectSource.errors?.[0]?.message ?? "Disconnect failed");
  }

  async function handleDeleteKey(k: AstroliftSshDeployKey) {
    const { data } = await deleteKey({ variables: { input: { id: k.id } } });
    if (data?.deleteSshDeployKey.ok) toast.success(`Deleted ${k.name}`);
    else throw new Error(data?.deleteSshDeployKey.errors?.[0]?.message ?? "Delete failed");
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-lg font-semibold tracking-tight">Source providers</h2>
        <p className="text-muted-foreground mt-1 max-w-2xl text-sm">
          Connect Astrolift to a code-hosting service so it can clone your repos and
          watch for pushes. Use OAuth Apps / GitHub Apps for org-wide access, PATs for
          self-hosted GitLab or Gitea, and SSH deploy keys for direct git access to any host.
        </p>
      </div>
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
                <Link href={DOC_LINKS.sourceProviders}>
                  <BookOpenIcon className="size-4" />
                  Setup guide
                </Link>
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
            <Link
              href={DOC_LINKS.sourceProviders}
              className="text-muted-foreground text-2xs underline"
            >
              one-click GitHub flow + GitLab wizard — full walkthrough
            </Link>
            {/* #760 — GitHub's manifest API can't set the App's logo, so
                the App lands with a placeholder. Surface the platform's
                logo asset here so operators can grab it once + upload
                manually under App settings → Display information. */}
            <a
              href="/static/img/astrolift-app-icon.png"
              download="astrolift-app-icon.png"
              className="text-muted-foreground text-2xs underline"
            >
              download Astrolift logo for your new App
            </a>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {/*
            Action-required banner — surfaces GitHub connections that
            are missing the OAuth Client ID (post-#525 schema split).
            Existing rows registered before the column was added carry
            an empty app_client_id; clicking "Connect my GitHub" on
            them would redirect to github.com with the numeric App ID
            in the client_id query param and 404 there. The banner
            walks the operator to a focused dialog that takes only the
            Client ID and runs UpdateSourceConnection in place.
          */}
          {incompleteClientIdConnections.length > 0 && (
            <div className="border-amber-500/30 bg-amber-500/5 mx-6 mt-6 flex items-start gap-3 rounded-md border p-3 text-xs text-amber-900 dark:text-amber-200">
              <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
              <div className="flex-1 space-y-2">
                <p className="font-medium">
                  Action required: {incompleteClientIdConnections.length === 1
                    ? "a GitHub connection needs"
                    : `${incompleteClientIdConnections.length} GitHub connections need`}{" "}
                  a Client ID
                </p>
                <p>
                  The user-to-server OAuth flow (&quot;Connect my GitHub&quot;) will
                  fail with a 404 at github.com until you add the GitHub App
                  Client ID. Find it on your GitHub App settings page — it looks
                  like <code className="font-mono">Iv23l…</code> for new GitHub
                  Apps.
                </p>
                <div className="flex flex-wrap gap-2 pt-1">
                  {incompleteClientIdConnections.map((c) => (
                    <Button
                      key={c.id}
                      size="sm"
                      variant="outline"
                      onClick={() => setClientIdTarget(c)}
                    >
                      Add Client ID for {c.name}
                    </Button>
                  ))}
                </div>
              </div>
            </div>
          )}
          {allConnections.length > 0 && (
            <ListControls controls={connCtrl} searchPlaceholder="Search connections…" className="mb-3" />
          )}
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
                        <Badge variant="secondary" className="ml-2 gap-1 text-2xs">
                          OAuth-app config
                        </Badge>
                      )}
                      {c.isPersonal && (
                        <Badge
                          variant="secondary"
                          className="ml-2 gap-1 bg-blue-500/15 text-2xs text-blue-700 dark:text-blue-300"
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
                            <Badge key={s} variant="secondary" className="text-2xs">
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
                          c.kind === "github_app_install") &&
                          (c.needsClientId ? (
                            <Can permission="scm.connect">
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => setClientIdTarget(c)}
                              >
                                <AlertTriangleIcon className="size-3.5 text-amber-600 dark:text-amber-400" />
                                Add Client ID
                              </Button>
                            </Can>
                          ) : (
                            <Button asChild size="sm" variant="outline">
                              <a
                                href={`/app/auth1/scm/github/start?config_id=${encodeURIComponent(c.id)}&return_to=/providers%23source`}
                              >
                                Connect my GitHub
                              </a>
                            </Button>
                          ))}
                        {c.kind === "gitlab_oauth_app" && c.isOauthAppConfig && (
                          <Button asChild size="sm" variant="outline">
                            <a
                              href={`/app/auth1/scm/gitlab/start?config_id=${encodeURIComponent(c.id)}&return_to=/providers%23source`}
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
                                onClick={() => setRotateSecretTarget(c)}
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
                            onClick={() => setDisconnectTarget(c)}
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
          {allKeys.length > 0 && (
            <ListControls controls={keyCtrl} searchPlaceholder="Search deploy keys…" className="mb-3" />
          )}
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
                    <TableCell className="font-mono text-2xs">{k.fingerprintSha256}</TableCell>
                    <TableCell>
                      <PublicKeyCell value={k.publicKey} />
                    </TableCell>
                    <TableCell className="text-right">
                      <Can permission="scm.key_delete">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setDeleteKeyTarget(k)}
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
            onClick={() => setShowAdvanced(!showAdvanced)}
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
      <AddClientIdDialog
        connection={clientIdTarget}
        onClose={() => setClientIdTarget(null)}
      />
      {revealedSecret && (
        <WebhookSecretReveal reveal={revealedSecret} onClose={() => setRevealedSecret(null)} />
      )}

      <ConfirmDialog
        open={disconnectTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDisconnectTarget(null);
        }}
        title={disconnectTarget ? `Disconnect ${disconnectTarget.name}?` : "Disconnect?"}
        description="Apps using this connection lose access until you reconnect. Webhooks signed with the old secret will fail until the integration is set up again."
        confirmLabel="Disconnect"
        destructive
        onConfirm={async () => {
          if (disconnectTarget) await handleDisconnect(disconnectTarget);
        }}
      />

      <ConfirmDialog
        open={deleteKeyTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteKeyTarget(null);
        }}
        title={deleteKeyTarget ? `Delete SSH key ${deleteKeyTarget.name}?` : "Delete SSH key?"}
        description="Remove the matching public key from any repo deploy-key lists first, otherwise git operations will fail abruptly. The private key is erased from the secrets backend."
        confirmLabel="Delete key"
        destructive
        onConfirm={async () => {
          if (deleteKeyTarget) await handleDeleteKey(deleteKeyTarget);
        }}
      />

      <ConfirmDialog
        open={rotateSecretTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRotateSecretTarget(null);
        }}
        title={
          rotateSecretTarget
            ? `Rotate webhook secret for ${rotateSecretTarget.name}?`
            : "Rotate webhook secret?"
        }
        description="Generates a fresh secret and shows it once. Webhook deliveries signed with the old secret start failing immediately — paste the new value into the SCM host's webhook config before any pushes happen, or expect a short window where deploy triggers are rejected with a 401."
        confirmLabel="Rotate secret"
        destructive
        onConfirm={async () => {
          if (rotateSecretTarget) await handleRotateSecret(rotateSecretTarget);
        }}
      />
    </div>
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
              <code className="bg-muted flex-1 rounded px-2 py-1 font-mono text-2xs break-all">
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
              <code className="bg-muted flex-1 rounded px-2 py-1 font-mono text-2xs break-all">
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
      toast.success("Public key copied");
    } catch {
      toast.error("Couldn't copy — paste manually below");
    }
  }, [value]);
  const truncated = value.length > 60 ? `${value.slice(0, 60)}…` : value;
  return (
    <div className="flex items-center gap-2">
      <code className="font-mono text-2xs">{truncated}</code>
      <Button size="sm" variant="ghost" onClick={onCopy}>
        {copied ? "copied" : "copy"}
      </Button>
    </div>
  );
}
