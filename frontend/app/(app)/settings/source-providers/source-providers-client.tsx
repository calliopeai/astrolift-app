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
  InfoIcon,
  KeyRoundIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DataTable, useCursorTable, type Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { DOC_LINKS } from "@/lib/docs/urls";
import {
  DELETE_SSH_DEPLOY_KEY,
  DISCONNECT_SOURCE,
  ROTATE_WEBHOOK_SECRET,
} from "@/graphql/scm/scm.mutations";
import {
  LIST_SOURCE_CONNECTIONS,
  LIST_SOURCE_CONNECTIONS_PAGE,
  LIST_SSH_DEPLOY_KEYS_PAGE,
} from "@/graphql/scm/scm.queries";
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

interface ConnectionsPageResp {
  astroliftSourceConnectionsPage: {
    items: AstroliftSourceConnection[];
    nextCursor?: string | null;
    totalCount?: number | null;
  };
}

interface KeysPageResp {
  astroliftSshDeployKeysPage: {
    items: AstroliftSshDeployKey[];
    nextCursor?: string | null;
    totalCount?: number | null;
  };
}

// Refetched by operation NAME, not by document: the cursor, page size and
// search term live inside the controller, so no literal variables object
// names the page the operator is actually looking at.
const REFETCH_CONNECTIONS = "ListSourceConnectionsPage";
const REFETCH_KEYS = "ListSshDeployKeysPage";

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

  // OAuth-callback outcome toasts moved to a layout-level component
  // (#759, `components/ScmCallbackToast`) so they surface regardless
  // of where `return_to` lands. Refetching the list when we land on
  // this page with a success param is now the only page-specific
  // bit; the toast handler in the layout consumes the param before
  // we run, so we can't read it directly. The queries already
  // cache-and-network-fetch on mount, so the row state is up to
  // date without an explicit refetch hook here.
  const connTable = useCursorTable<AstroliftSourceConnection>({
    query: LIST_SOURCE_CONNECTIONS_PAGE,
    extract: (d) => (d as ConnectionsPageResp | undefined)?.astroliftSourceConnectionsPage,
    searchVariable: "search",
    urlKey: "conn",
  });

  const keyTable = useCursorTable<AstroliftSshDeployKey>({
    query: LIST_SSH_DEPLOY_KEYS_PAGE,
    // `null` keeps the list field's meaning: every key in the org,
    // org-scoped and per-app alike. `""` would narrow to org-scoped only.
    variables: { appSlug: null },
    extract: (d) => (d as KeysPageResp | undefined)?.astroliftSshDeployKeysPage,
    searchVariable: "search",
    urlKey: "key",
  });

  // Org-wide connection list, kept for the "needs a Client ID" callout
  // below — NOT for table rows. The callout enumerates every incomplete
  // GitHub App connection in the org; sourcing it from the table's page
  // would silently scope the prompt to whichever 25 rows are on screen.
  // It is the same document `/providers` already preloads and the repo
  // pickers already watch, so on this route it costs nothing extra.
  const conns = useQuery<ConnectionsResp>(LIST_SOURCE_CONNECTIONS);

  const [disconnect, disconnectState] = useMutation<{
    disconnectSource: MutationResult<{ id: string }>;
  }>(DISCONNECT_SOURCE, {
    refetchQueries: [{ query: LIST_SOURCE_CONNECTIONS }, REFETCH_CONNECTIONS],
    awaitRefetchQueries: true,
  });

  const [deleteKey, deleteKeyState] = useMutation<{
    deleteSshDeployKey: MutationResult<{ id: string }>;
  }>(DELETE_SSH_DEPLOY_KEY, {
    refetchQueries: [REFETCH_KEYS],
    awaitRefetchQueries: true,
  });

  const [revealedSecret, setRevealedSecret] = React.useState<AstroliftWebhookSecretReveal | null>(
    null
  );

  const [rotateSecret, rotateSecretState] = useMutation<{
    rotateWebhookSecret: MutationResult<AstroliftWebhookSecretReveal>;
  }>(ROTATE_WEBHOOK_SECRET, {
    refetchQueries: [{ query: LIST_SOURCE_CONNECTIONS }, REFETCH_CONNECTIONS],
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

  const incompleteClientIdConnections = (conns.data?.astroliftSourceConnections ?? []).filter(
    (c) => c.needsClientId
  );

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

  // The connect / generate dialogs live in their own files and still
  // refetch the deprecated flat lists, which no longer feed these tables.
  // Refreshing on close is the in-place fix: a dialog that closes after a
  // successful mutation is exactly when the page needs new rows.
  const refreshConnections = connTable.refetch;
  const refreshKeys = keyTable.refetch;

  // No sort controls on either table: neither page field takes a sort
  // argument (both seek on `-created_at, -guid`), and reordering the page
  // in hand while the rest of the list sits on the server is wrong at
  // every page boundary.
  const connectionColumns: Column<AstroliftSourceConnection>[] = [
    {
      id: "connection",
      header: "Connection",
      cell: (c) => (
        <>
          <div className="font-medium">{c.name}</div>
          {c.apiBaseUrl && (
            <div className="text-muted-foreground font-mono text-xs">{c.apiBaseUrl}</div>
          )}
        </>
      ),
    },
    {
      id: "kind",
      header: "Kind",
      cell: (c) => (
        <>
          <Badge variant="outline">{KIND_LABEL[c.kind] ?? c.kind}</Badge>
          {c.isOauthAppConfig && (
            <Badge variant="secondary" className="ml-2 gap-1 text-2xs">
              OAuth-app config
            </Badge>
          )}
          {c.isPersonal && (
            <Badge variant="secondary" className="bg-info/15 text-info-fg ml-2 gap-1 text-2xs">
              personal {c.userUsername ? `· ${c.userUsername}` : ""}
            </Badge>
          )}
        </>
      ),
    },
    {
      id: "account",
      header: "Account",
      cellClassName: "font-mono text-xs",
      cell: (c) => c.accountLogin || "—",
    },
    {
      id: "scopes",
      header: "Scopes",
      cell: (c) =>
        c.repoVisibilityScopes.length === 0 ? (
          <span className="text-muted-foreground text-xs">— no scope restriction —</span>
        ) : (
          <div className="flex flex-wrap gap-1">
            {c.repoVisibilityScopes.map((s) => (
              <Badge key={s} variant="secondary" className="text-2xs">
                {SCOPE_LABEL[s] ?? s}
              </Badge>
            ))}
          </div>
        ),
    },
    {
      id: "status",
      header: "Status",
      cell: (c) =>
        c.isActive ? (
          <Badge className="bg-success/15 text-success-fg gap-1" variant="secondary">
            <CheckCircle2Icon className="size-3" />
            active
          </Badge>
        ) : (
          <Badge variant="outline">disabled</Badge>
        ),
    },
    {
      id: "actions",
      header: "Actions",
      align: "right",
      cell: (c) => (
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
                <Button size="sm" variant="ghost" onClick={() => setClientIdTarget(c)}>
                  <PlusIcon className="size-3.5" />
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
          {!c.isPersonal && (c.kind.startsWith("github_") || c.kind.startsWith("gitlab_")) && (
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
      ),
    },
  ];

  const keyColumns: Column<AstroliftSshDeployKey>[] = [
    {
      id: "name",
      header: "Name",
      cellClassName: "font-medium",
      cell: (k) => k.name,
    },
    {
      id: "scope",
      header: "Scope",
      cell: (k) =>
        k.registeredAppSlug ? (
          <Badge variant="secondary">app: {k.registeredAppSlug}</Badge>
        ) : (
          <Badge variant="outline">org-scoped</Badge>
        ),
    },
    {
      id: "fingerprint",
      header: "Fingerprint",
      cellClassName: "font-mono text-2xs",
      cell: (k) => k.fingerprintSha256,
    },
    {
      id: "publicKey",
      header: "Public key",
      cell: (k) => <PublicKeyCell value={k.publicKey} />,
    },
    {
      id: "actions",
      header: "Actions",
      align: "right",
      cell: (k) => (
        <div className="flex justify-end">
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
        </div>
      ),
    },
  ];

  return (
    <Section
      title="Source providers"
      description={
        <span className="block max-w-2xl">
          Connect Astrolift to a code-hosting service so it can clone your repos and
          watch for pushes. Use OAuth Apps / GitHub Apps for org-wide access, PATs for
          self-hosted GitLab or Gitea, and SSH deploy keys for direct git access to any host.
        </span>
      }
      className="gap-6"
    >
      {/* Connections */}
      <Section
        headingLevel="h3"
        title={
          <span className="flex items-center gap-2">
            <GitBranchIcon className="size-4" />
            Hosts
          </span>
        }
        description={
          <span className="block max-w-2xl">
            One-click GitHub App registration via the manifest flow, or a guided GitLab Group
            OAuth wizard. Personal Access Tokens and pre-registered OAuth apps remain available
            under <em>Advanced</em>.
          </span>
        }
        action={
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
        }
      >
          {/*
            Optional-enhancement note — surfaces GitHub App connections
            that don't yet carry an OAuth Client ID (post-#525 schema
            split). These connections already work: cloning, autowiring
            and deploys run on the App installation token. Supplying a
            Client ID additionally enables the user-to-server "Connect
            my GitHub" flow, so it's offered here as an optional recovery
            action (focused dialog → UpdateSourceConnection in place),
            not a failure state.
          */}
          {incompleteClientIdConnections.length > 0 && (
            <div className="text-muted-foreground bg-muted/40 flex items-start gap-3 rounded-md border p-3 text-xs">
              <InfoIcon className="mt-0.5 size-4 shrink-0" />
              <div className="flex-1 space-y-2">
                <p className="text-foreground font-medium">
                  Optional: enable &quot;Connect my GitHub&quot; repo browsing
                </p>
                <p>
                  {incompleteClientIdConnections.length === 1
                    ? "This GitHub App connection is"
                    : `These ${incompleteClientIdConnections.length} GitHub App connections are`}{" "}
                  ready to use — cloning, autowiring and deploys run on the App
                  installation token. Adding an OAuth Client ID is optional; it
                  turns on the user-to-server &quot;Connect my GitHub&quot; flow so
                  people can browse their own repositories. The Client ID looks
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
          <DataTable
            label="Connections"
            controller={connTable}
            columns={connectionColumns}
            getRowId={(c) => c.id}
            searchPlaceholder="Search connections…"
            empty={{
              icon: <GitBranchIcon className="size-5" />,
              title: "No hosts connected yet",
              description:
                "Click 'Connect to GitHub' to register an Astrolift GitHub App on github.com in one step — no client_id / secret paste required. For GitLab, the wizard walks you through creating a Group OAuth Application with copy-friendly callback URLs.",
            }}
            emptyFiltered={{
              title: "No matching connections",
              description:
                "No connection matches that search. It looks at the kind, API base URL, account login, and display name — try another term, or clear the search to see every host.",
            }}
          />
      </Section>

      {/* SSH keys */}
      <Section
        headingLevel="h3"
        title={
          <span className="flex items-center gap-2">
            <KeyRoundIcon className="size-4" />
            SSH deploy keys
          </span>
        }
        description={
          <span className="block max-w-2xl">
            ed25519 keypairs for direct git-over-SSH access. The public key is shown for you to
            paste into the repo&apos;s deploy-key settings; the private key stays encrypted in the
            platform secrets backend.
          </span>
        }
        action={
          <Can permission="scm.key_create">
            <Button size="sm" onClick={() => setOpenGenerateKey(true)}>
              <PlusIcon className="size-4" />
              Generate key
            </Button>
          </Can>
        }
      >
          <DataTable
            label="SSH deploy keys"
            controller={keyTable}
            columns={keyColumns}
            getRowId={(k) => k.id}
            searchPlaceholder="Search deploy keys…"
            empty={{
              icon: <KeyRoundIcon className="size-5" />,
              title: "No SSH deploy keys yet",
              description:
                "Generate a key to let Astrolift clone over SSH. Org-scoped keys cover every app; per-app keys offer tighter blast-radius.",
            }}
            emptyFiltered={{
              title: "No matching deploy keys",
              description:
                "No key matches that search. It looks at the name, the SHA-256 fingerprint, and the app slug a key is scoped to — clear the search to see every key.",
            }}
          />
      </Section>

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

      <ConnectGitHubDialog
        open={openConnectGithub}
        onOpenChange={(next) => {
          setOpenConnectGithub(next);
          if (!next) refreshConnections();
        }}
      />
      <ConnectGitLabDialog
        open={openConnectGitlab}
        onOpenChange={(next) => {
          setOpenConnectGitlab(next);
          if (!next) refreshConnections();
        }}
      />
      <ConnectSourceDialog
        open={openConnect}
        onOpenChange={(next) => {
          setOpenConnect(next);
          if (!next) refreshConnections();
        }}
      />
      <GenerateSshKeyDialog
        open={openGenerateKey}
        onOpenChange={(next) => {
          setOpenGenerateKey(next);
          if (!next) refreshKeys();
        }}
      />
      <AddClientIdDialog
        connection={clientIdTarget}
        onClose={() => {
          setClientIdTarget(null);
          refreshConnections();
        }}
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
    </Section>
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
