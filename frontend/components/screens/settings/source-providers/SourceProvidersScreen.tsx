"use client";

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
  RotateCwIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { SettingsPage } from "@/components/settings/SettingsPage";
import type { SectionSelection } from "@/components/settings/use-settings-section";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem, DropdownMenuSeparator } from "@/components/ui/dropdown-menu";
import { Section } from "@/components/ui/section";
import type {
  AstroliftSourceConnection,
  AstroliftSshDeployKey,
  AstroliftWebhookSecretReveal,
} from "@/graphql/scm/scm.types";
import { useLocalStorage } from "@/hooks/use-local-storage";
import { DOC_LINKS } from "@/lib/docs/urls";

import type { DeployKeysData, SourceHostsData } from "./use-source-providers";

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

type DialogSlot = (open: boolean, onOpenChange: (next: boolean) => void) => React.ReactNode;

export interface SourceProvidersScreenProps {
  /** Which section is shown (`?section=`); only that one is mounted and fetches. */
  section: SectionSelection;
  /** The Hosts section (SourceHostsView), filled by the route. */
  hosts: React.ReactNode;
  /** The SSH deploy keys section (DeployKeysView), filled by the route. */
  keys: React.ReactNode;
}

/**
 * Providers › Source: hosts and SSH deploy keys, one section at a time
 * (list rules 1 to 3), so the tab holds one list and fetches only the
 * section on screen. Pure.
 */
export function SourceProvidersScreen({ section, hosts, keys }: SourceProvidersScreenProps) {
  return (
    <Section
      title="Source providers"
      description={
        <span className="block max-w-2xl">
          Connect Astrolift to a code-hosting service so it can clone your repos and watch for
          pushes. Use OAuth Apps / GitHub Apps for org-wide access, PATs for self-hosted GitLab or
          Gitea, and SSH deploy keys for direct git access to any host.
        </span>
      }
      className="gap-6"
    >
      <SettingsPage
        single={section}
        sections={[
          { id: "hosts", title: "Hosts", content: hosts },
          { id: "keys", title: "SSH deploy keys", content: keys },
        ]}
      />
    </Section>
  );
}

export type SourceHostsViewProps = SourceHostsData & {
  /** The connect sheets. The route fills them with containers so each
   *  sheet's hook runs only while it is mounted. */
  renderConnectGithub: DialogSlot;
  renderConnectGitlab: DialogSlot;
  renderConnectSource: DialogSlot;
  renderAddClientId: (
    connection: AstroliftSourceConnection | null,
    onClose: () => void
  ) => React.ReactNode;
};

/**
 * The Hosts section: the org's SCM connections on an embedded list (views
 * All · Mine, host and status filters, cursor pages), each row's actions in
 * its `⋯`. Pure; the data half is useSourceHosts.
 */
export function SourceHostsView({
  list,
  rows,
  totalCount,
  nextCursor,
  loading,
  error,
  onRetry,
  incompleteClientIdConnections,
  disconnecting,
  rotatingSecret,
  disconnect,
  rotateSecret,
  refreshConnections,
  renderConnectGithub,
  renderConnectGitlab,
  renderConnectSource,
  renderAddClientId,
}: SourceHostsViewProps) {
  const [openConnect, setOpenConnect] = React.useState(false);
  const [openConnectGithub, setOpenConnectGithub] = React.useState(false);
  const [openConnectGitlab, setOpenConnectGitlab] = React.useState(false);
  const [showAdvanced, setShowAdvanced] = useLocalStorage<boolean>(
    ADVANCED_OPEN_STORAGE_KEY,
    false
  );
  const [disconnectTarget, setDisconnectTarget] = React.useState<AstroliftSourceConnection | null>(
    null
  );
  const [revealedSecret, setRevealedSecret] = React.useState<AstroliftWebhookSecretReveal | null>(
    null
  );
  const [rotateSecretTarget, setRotateSecretTarget] =
    React.useState<AstroliftSourceConnection | null>(null);
  // Connection currently being edited to add a missing Client ID (#525
  // recovery flow). NULL when the dialog is closed.
  const [clientIdTarget, setClientIdTarget] = React.useState<AstroliftSourceConnection | null>(
    null
  );

  // No sortable headers: the page field takes no sort argument, and
  // reordering the page in hand while the rest sits on the server is wrong
  // at every page boundary.
  const connectionColumns: Column<AstroliftSourceConnection>[] = [
    {
      id: "connection",
      header: "Connection",
      cellClassName: "max-w-72",
      cell: (c) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium" title={c.name}>
            {c.name}
          </span>
          {c.apiBaseUrl && (
            <span
              className="text-muted-foreground block truncate font-mono text-xs"
              title={c.apiBaseUrl}
            >
              {c.apiBaseUrl}
            </span>
          )}
        </span>
      ),
    },
    {
      id: "kind",
      header: "Kind",
      cell: (c) => (
        <span className="flex min-w-0 flex-wrap gap-1">
          <Badge variant="outline">{KIND_LABEL[c.kind] ?? c.kind}</Badge>
          {c.isOauthAppConfig && (
            <Badge variant="secondary" className="text-2xs gap-1">
              OAuth-app config
            </Badge>
          )}
          {c.isPersonal && (
            <Badge
              variant="secondary"
              className="bg-info/15 text-info-fg text-2xs max-w-full gap-1 truncate"
            >
              personal {c.userUsername ? `· ${c.userUsername}` : ""}
            </Badge>
          )}
        </span>
      ),
    },
    {
      id: "account",
      header: "Account",
      cellClassName: "max-w-48",
      cell: (c) => (
        <span className="block truncate font-mono text-xs" title={c.accountLogin || undefined}>
          {c.accountLogin || "—"}
        </span>
      ),
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
  ];

  function rowActions(c: AstroliftSourceConnection) {
    // GitHub user-to-server OAuth works against both a classic OAuth App
    // config (kind=github_oauth_app + isOauthAppConfig) and a
    // manifest-registered GitHub App install (kind=github_app_install):
    // both have a client_id in oauth_client_id and GitHub's
    // /login/oauth/authorize endpoint accepts either.
    const githubUserOauth =
      (c.kind === "github_oauth_app" && c.isOauthAppConfig) || c.kind === "github_app_install";
    return (
      <>
        {githubUserOauth &&
          (c.needsClientId ? (
            <Can permission="scm.connect">
              <DropdownMenuItem onSelect={() => setClientIdTarget(c)}>
                <PlusIcon className="size-4" />
                Add Client ID
              </DropdownMenuItem>
            </Can>
          ) : (
            <DropdownMenuItem asChild>
              <a
                href={`/app/auth1/scm/github/start?config_id=${encodeURIComponent(c.id)}&return_to=/providers%23source`}
              >
                <GithubIcon className="size-4" />
                Connect my GitHub
              </a>
            </DropdownMenuItem>
          ))}
        {c.kind === "gitlab_oauth_app" && c.isOauthAppConfig && (
          <DropdownMenuItem asChild>
            <a
              href={`/app/auth1/scm/gitlab/start?config_id=${encodeURIComponent(c.id)}&return_to=/providers%23source`}
            >
              <GitlabIcon className="size-4" />
              Connect my GitLab
            </a>
          </DropdownMenuItem>
        )}
        {!c.isPersonal && (c.kind.startsWith("github_") || c.kind.startsWith("gitlab_")) && (
          <Can permission="scm.connect">
            <DropdownMenuItem disabled={rotatingSecret} onSelect={() => setRotateSecretTarget(c)}>
              <RotateCwIcon className="size-4" />
              Webhook secret
            </DropdownMenuItem>
          </Can>
        )}
        <Can permission="scm.disconnect">
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            disabled={disconnecting}
            onSelect={() => setDisconnectTarget(c)}
          >
            <Trash2Icon className="size-4" />
            Disconnect
          </DropdownMenuItem>
        </Can>
      </>
    );
  }

  return (
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
          One-click GitHub App registration via the manifest flow, or a guided GitLab Group OAuth
          wizard. Personal Access Tokens and pre-registered OAuth apps remain available under{" "}
          <em>Advanced</em>.
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
              ready to use — cloning, autowiring and deploys run on the App installation token.
              Adding an OAuth Client ID is optional; it turns on the user-to-server &quot;Connect my
              GitHub&quot; flow so people can browse their own repositories. The Client ID looks
              like <code className="font-mono">Iv23l…</code> for new GitHub Apps.
            </p>
            <div className="flex flex-wrap gap-2 pt-1">
              {incompleteClientIdConnections.map((c) => (
                <Button key={c.id} size="sm" variant="outline" onClick={() => setClientIdTarget(c)}>
                  Add Client ID for {c.name}
                </Button>
              ))}
            </div>
          </div>
        </div>
      )}
      <ListPage<AstroliftSourceConnection>
        embedded
        list={list}
        label="Connections"
        columns={connectionColumns}
        rows={rows}
        getRowId={(c) => c.id}
        rowActions={rowActions}
        loading={loading}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        nextCursor={nextCursor}
        empty={{
          icon: <GitBranchIcon className="size-5" />,
          title: "No hosts connected yet",
          description:
            "Click 'Connect to GitHub' to register an Astrolift GitHub App on github.com in one step — no client_id / secret paste required. For GitLab, the wizard walks you through creating a Group OAuth Application with copy-friendly callback URLs.",
        }}
      />

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

      {renderConnectGithub(openConnectGithub, (next) => {
        setOpenConnectGithub(next);
        if (!next) refreshConnections();
      })}
      {renderConnectGitlab(openConnectGitlab, (next) => {
        setOpenConnectGitlab(next);
        if (!next) refreshConnections();
      })}
      {renderConnectSource(openConnect, (next) => {
        setOpenConnect(next);
        if (!next) refreshConnections();
      })}
      {renderConnectGithub(openConnectGithub, (next) => {
        setOpenConnectGithub(next);
        if (!next) refreshConnections();
      })}
      {renderConnectGitlab(openConnectGitlab, (next) => {
        setOpenConnectGitlab(next);
        if (!next) refreshConnections();
      })}
      {renderConnectSource(openConnect, (next) => {
        setOpenConnect(next);
        if (!next) refreshConnections();
      })}
      {renderAddClientId(clientIdTarget, () => {
        setClientIdTarget(null);
        refreshConnections();
      })}
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
          if (disconnectTarget) await disconnect(disconnectTarget);
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
          if (rotateSecretTarget) setRevealedSecret(await rotateSecret(rotateSecretTarget));
        }}
      />
    </Section>
  );
}

export type DeployKeysViewProps = DeployKeysData & {
  /** The generate sheet, a container from the route. */
  renderGenerateKey: DialogSlot;
};

/**
 * The SSH deploy keys section: the org's keys on an embedded list (views
 * All · Mine, scope and app filters on the server, cursor pages), delete in
 * each row's `⋯`. Pure; the data half is useDeployKeys.
 */
export function DeployKeysView({
  list,
  rows,
  totalCount,
  nextCursor,
  loading,
  error,
  onRetry,
  deletingKey,
  deleteKey,
  refreshKeys,
  renderGenerateKey,
}: DeployKeysViewProps) {
  const [openGenerateKey, setOpenGenerateKey] = React.useState(false);
  const [deleteKeyTarget, setDeleteKeyTarget] = React.useState<AstroliftSshDeployKey | null>(null);

  const keyColumns: Column<AstroliftSshDeployKey>[] = [
    {
      id: "name",
      header: "Name",
      cellClassName: "max-w-64",
      cell: (k) => (
        <span className="block truncate font-medium" title={k.name}>
          {k.name}
        </span>
      ),
    },
    {
      id: "scope",
      header: "Scope",
      cellClassName: "max-w-56",
      cell: (k) =>
        k.registeredAppSlug ? (
          <Badge variant="secondary" className="max-w-full truncate">
            app: {k.registeredAppSlug}
          </Badge>
        ) : (
          <Badge variant="outline">org-scoped</Badge>
        ),
    },
    {
      id: "fingerprint",
      header: "Fingerprint",
      cellClassName: "max-w-64",
      cell: (k) => (
        <span className="text-2xs block truncate font-mono" title={k.fingerprintSha256}>
          {k.fingerprintSha256}
        </span>
      ),
    },
    {
      id: "publicKey",
      header: "Public key",
      cell: (k) => <PublicKeyCell value={k.publicKey} />,
    },
  ];

  return (
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
          ed25519 keypairs for direct git-over-SSH access. The public key is shown for you to paste
          into the repo&apos;s deploy-key settings; the private key stays encrypted in the platform
          secrets backend.
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
      <ListPage<AstroliftSshDeployKey>
        embedded
        list={list}
        label="SSH deploy keys"
        columns={keyColumns}
        rows={rows}
        getRowId={(k) => k.id}
        rowActions={(k) => (
          <Can permission="scm.key_delete">
            <DropdownMenuItem
              variant="destructive"
              disabled={deletingKey}
              onSelect={() => setDeleteKeyTarget(k)}
            >
              <Trash2Icon className="size-4" />
              Delete
            </DropdownMenuItem>
          </Can>
        )}
        loading={loading}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        nextCursor={nextCursor}
        empty={{
          icon: <KeyRoundIcon className="size-5" />,
          title: "No SSH deploy keys yet",
          description:
            "Generate a key to let Astrolift clone over SSH. Org-scoped keys cover every app; per-app keys offer tighter blast-radius.",
        }}
      />

      {renderGenerateKey(openGenerateKey, (next) => {
        setOpenGenerateKey(next);
        if (!next) refreshKeys();
      })}

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
          if (deleteKeyTarget) await deleteKey(deleteKeyTarget);
        }}
      />
    </Section>
  );
}

/** The one-time webhook secret reveal shown after a rotation. */
export function WebhookSecretReveal({
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
              <code className="bg-muted text-2xs flex-1 rounded px-2 py-1 font-mono break-all">
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
              <code className="bg-muted text-2xs flex-1 rounded px-2 py-1 font-mono break-all">
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
      <code className="text-2xs font-mono">{truncated}</code>
      <Button size="sm" variant="ghost" onClick={onCopy}>
        {copied ? "copied" : "copy"}
      </Button>
    </div>
  );
}
