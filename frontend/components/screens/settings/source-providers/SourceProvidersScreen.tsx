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
import { useTranslations } from "next-intl";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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

// Kinds and scopes with a label under sourceProviders.kind / .scope; any
// other value renders as the raw API value.
const KIND_LABEL_KEYS = new Set([
  "github_oauth_app",
  "github_oauth_user",
  "github_app_install",
  "github_pat",
  "gitlab_oauth_app",
  "gitlab_oauth_user",
  "gitlab_pat",
  "bitbucket_oauth_app",
  "bitbucket_oauth_user",
  "bitbucket_pat",
  "gitea_oauth_app",
  "gitea_oauth_user",
  "gitea_pat",
]);

const SCOPE_LABEL_KEYS = new Set(["private_org", "public_org", "user_repos", "public_non_org"]);

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
  const t = useTranslations("sourceProviders");
  return (
    <Section
      title={t("title")}
      description={<span className="block max-w-2xl">{t("description")}</span>}
      className="gap-6"
    >
      <SettingsPage
        single={section}
        sections={[
          { id: "hosts", title: t("hostsTitle"), content: hosts },
          { id: "keys", title: t("keysTitle"), content: keys },
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
  const t = useTranslations("sourceProviders");
  const hostsRegion = React.useRef<HTMLElement | null>(null);
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
      header: t("hosts.columns.connection"),
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
      header: t("hosts.columns.kind"),
      cell: (c) => (
        <span className="flex min-w-0 flex-wrap gap-1">
          <Badge variant="outline">
            {KIND_LABEL_KEYS.has(c.kind) ? t(`kind.${c.kind}`) : c.kind}
          </Badge>
          {c.isOauthAppConfig && (
            <Badge variant="secondary" className="text-2xs gap-1">
              {t("hosts.oauthAppConfig")}
            </Badge>
          )}
          {c.isPersonal && (
            <Badge
              variant="secondary"
              className="bg-info/15 text-info-fg text-2xs max-w-full gap-1 truncate"
            >
              {t("hosts.personal")} {c.userUsername ? `· ${c.userUsername}` : ""}
            </Badge>
          )}
        </span>
      ),
    },
    {
      id: "account",
      header: t("hosts.columns.account"),
      cellClassName: "max-w-48",
      cell: (c) => (
        <span className="block truncate font-mono text-xs" title={c.accountLogin || undefined}>
          {c.accountLogin || "—"}
        </span>
      ),
    },
    {
      id: "scopes",
      header: t("hosts.columns.scopes"),
      cell: (c) =>
        c.repoVisibilityScopes.length === 0 ? (
          <span className="text-muted-foreground text-xs">{t("hosts.noScopeRestriction")}</span>
        ) : (
          <div className="flex flex-wrap gap-1">
            {c.repoVisibilityScopes.map((s) => (
              <Badge key={s} variant="secondary" className="text-2xs">
                {SCOPE_LABEL_KEYS.has(s) ? t(`scope.${s}`) : s}
              </Badge>
            ))}
          </div>
        ),
    },
    {
      id: "status",
      header: t("hosts.columns.status"),
      cell: (c) =>
        c.isActive ? (
          <Badge className="bg-success/15 text-success-fg gap-1" variant="secondary">
            <CheckCircle2Icon className="size-3" />
            {t("hosts.active")}
          </Badge>
        ) : (
          <Badge variant="outline">{t("hosts.disabled")}</Badge>
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
                {t("hosts.addClientId")}
              </DropdownMenuItem>
            </Can>
          ) : (
            <DropdownMenuItem asChild>
              <a
                href={`/app/auth1/scm/github/start?config_id=${encodeURIComponent(c.id)}&return_to=/providers%23source`}
              >
                <GithubIcon className="size-4" />
                {t("hosts.connectMyGithub")}
              </a>
            </DropdownMenuItem>
          ))}
        {c.kind === "gitlab_oauth_app" && c.isOauthAppConfig && (
          <DropdownMenuItem asChild>
            <a
              href={`/app/auth1/scm/gitlab/start?config_id=${encodeURIComponent(c.id)}&return_to=/providers%23source`}
            >
              <GitlabIcon className="size-4" />
              {t("hosts.connectMyGitlab")}
            </a>
          </DropdownMenuItem>
        )}
        {!c.isPersonal && (c.kind.startsWith("github_") || c.kind.startsWith("gitlab_")) && (
          <Can permission="scm.connect">
            <DropdownMenuItem disabled={rotatingSecret} onSelect={() => setRotateSecretTarget(c)}>
              <RotateCwIcon className="size-4" />
              {t("hosts.webhookSecret")}
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
            {t("hosts.disconnect")}
          </DropdownMenuItem>
        </Can>
      </>
    );
  }

  return (
    <Section
      ref={hostsRegion}
      tabIndex={-1}
      aria-label={t("hosts.regionLabel")}
      headingLevel="h3"
      title={
        <span className="flex items-center gap-2">
          <GitBranchIcon className="size-4" />
          {t("hostsTitle")}
        </span>
      }
      description={
        <span className="block max-w-2xl">
          {t.rich("hosts.description", { em: (chunks) => <em>{chunks}</em> })}
        </span>
      }
      action={
        <div className="flex flex-col items-end gap-1">
          <div className="flex flex-wrap justify-end gap-2">
            <Button asChild size="sm" variant="outline">
              <Link href={DOC_LINKS.sourceProviders}>
                <BookOpenIcon className="size-4" />
                {t("hosts.setupGuide")}
              </Link>
            </Button>
            <Can permission="scm.connect">
              <Button size="sm" onClick={() => setOpenConnectGithub(true)}>
                <GithubIcon className="size-4" />
                {t("hosts.connectGithub")}
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setOpenConnectGitlab(true)}>
                <GitlabIcon className="size-4" />
                {t("hosts.connectGitlab")}
              </Button>
            </Can>
          </div>
          <Link
            href={DOC_LINKS.sourceProviders}
            className="text-muted-foreground text-2xs underline"
          >
            {t("hosts.walkthrough")}
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
            {t("hosts.downloadLogo")}
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
          <div className="min-w-0 flex-1 space-y-2">
            <p className="text-foreground font-medium">{t("hosts.optionalTitle")}</p>
            <p>
              {t.rich("hosts.optionalBody", {
                count: incompleteClientIdConnections.length,
                code: (chunks) => <code className="font-mono">{chunks}</code>,
              })}
            </p>
            <div className="flex flex-wrap gap-2 pt-1">
              {incompleteClientIdConnections.map((c) => (
                <Button
                  key={c.id}
                  size="sm"
                  variant="outline"
                  className="max-w-full min-w-0"
                  title={t("hosts.addClientIdFor", { name: c.name })}
                  onClick={() => setClientIdTarget(c)}
                >
                  <span className="min-w-0 truncate">
                    {t("hosts.addClientIdFor", { name: c.name })}
                  </span>
                </Button>
              ))}
            </div>
          </div>
        </div>
      )}
      <ListPage<AstroliftSourceConnection>
        embedded
        list={list}
        label={t("hosts.listLabel")}
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
          title: t("hosts.emptyTitle"),
          description: t("hosts.emptyDescription"),
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
            {t("hosts.advancedToggle")}
          </button>
          {showAdvanced && (
            <div className="mt-2 flex items-center gap-2">
              <Button size="sm" variant="outline" onClick={() => setOpenConnect(true)}>
                <PlusIcon className="size-4" />
                {t("hosts.pasteCredentials")}
              </Button>
              <span className="text-muted-foreground text-xs">{t("hosts.pasteHint")}</span>
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
        <WebhookSecretReveal
          reveal={revealedSecret}
          onClose={() => setRevealedSecret(null)}
          returnFocusRef={hostsRegion}
        />
      )}

      <ConfirmDialog
        open={disconnectTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDisconnectTarget(null);
        }}
        title={
          disconnectTarget
            ? t("hosts.disconnectTitle", { name: disconnectTarget.name })
            : t("hosts.disconnectTitleGeneric")
        }
        description={t("hosts.disconnectDescription")}
        confirmLabel={t("hosts.disconnect")}
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
            ? t("hosts.rotateTitle", { name: rotateSecretTarget.name })
            : t("hosts.rotateTitleGeneric")
        }
        description={t("hosts.rotateDescription")}
        confirmLabel={t("hosts.rotateConfirm")}
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
  const t = useTranslations("sourceProviders");
  const [openGenerateKey, setOpenGenerateKey] = React.useState(false);
  const [deleteKeyTarget, setDeleteKeyTarget] = React.useState<AstroliftSshDeployKey | null>(null);

  const keyColumns: Column<AstroliftSshDeployKey>[] = [
    {
      id: "name",
      header: t("keys.columns.name"),
      cellClassName: "max-w-64",
      cell: (k) => (
        <span className="block truncate font-medium" title={k.name}>
          {k.name}
        </span>
      ),
    },
    {
      id: "scope",
      header: t("keys.columns.scope"),
      cellClassName: "max-w-56",
      cell: (k) =>
        k.registeredAppSlug ? (
          <Badge variant="secondary" className="max-w-full truncate">
            {t("keys.appScope", { slug: k.registeredAppSlug })}
          </Badge>
        ) : (
          <Badge variant="outline">{t("keys.orgScoped")}</Badge>
        ),
    },
    {
      id: "fingerprint",
      header: t("keys.columns.fingerprint"),
      cellClassName: "max-w-64",
      cell: (k) => (
        <span className="text-2xs block truncate font-mono" title={k.fingerprintSha256}>
          {k.fingerprintSha256}
        </span>
      ),
    },
    {
      id: "publicKey",
      header: t("keys.columns.publicKey"),
      cell: (k) => <PublicKeyCell value={k.publicKey} />,
    },
  ];

  return (
    <Section
      headingLevel="h3"
      title={
        <span className="flex items-center gap-2">
          <KeyRoundIcon className="size-4" />
          {t("keysTitle")}
        </span>
      }
      description={<span className="block max-w-2xl">{t("keys.description")}</span>}
      action={
        <Can permission="scm.key_create">
          <Button size="sm" onClick={() => setOpenGenerateKey(true)}>
            <PlusIcon className="size-4" />
            {t("keys.generate")}
          </Button>
        </Can>
      }
    >
      <ListPage<AstroliftSshDeployKey>
        embedded
        list={list}
        label={t("keysTitle")}
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
              {t("keys.delete")}
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
          title: t("keys.emptyTitle"),
          description: t("keys.emptyDescription"),
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
        title={
          deleteKeyTarget
            ? t("keys.deleteTitle", { name: deleteKeyTarget.name })
            : t("keys.deleteTitleGeneric")
        }
        description={t("keys.deleteDescription")}
        confirmLabel={t("keys.deleteConfirm")}
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
  returnFocusRef,
}: {
  reveal: AstroliftWebhookSecretReveal;
  onClose: () => void;
  returnFocusRef?: React.RefObject<HTMLElement | null>;
}) {
  const t = useTranslations("sourceProviders.reveal");
  const previousFocus = React.useRef<HTMLElement | null>(null);
  const fullUrl =
    typeof window !== "undefined"
      ? `${window.location.origin}${reveal.webhookUrlPath}`
      : reveal.webhookUrlPath;
  async function copy(value: string, copiedMessage: string) {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(copiedMessage);
    } catch {
      toast.error(t("copyFailed"));
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent
        aria-modal="true"
        onOpenAutoFocus={() => {
          previousFocus.current =
            document.activeElement instanceof HTMLElement ? document.activeElement : null;
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          const target =
            returnFocusRef?.current ??
            (previousFocus.current?.isConnected ? previousFocus.current : null);
          target?.focus();
        }}
      >
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>
            {t.rich("description", {
              strong: (chunks) => <span className="font-medium">{chunks}</span>,
            })}
          </DialogDescription>
        </DialogHeader>
        <div className="mt-4 space-y-3">
          <div>
            <span className="text-muted-foreground block text-xs tracking-wide uppercase">
              {t("webhookUrl")}
            </span>
            <div className="mt-1 flex items-center gap-2">
              <code className="bg-muted text-2xs flex-1 rounded px-2 py-1 font-mono break-all">
                {fullUrl}
              </code>
              <Button size="sm" variant="outline" onClick={() => copy(fullUrl, t("urlCopied"))}>
                {t("copyUrl")}
              </Button>
            </div>
          </div>
          <div>
            <span className="text-muted-foreground block text-xs tracking-wide uppercase">
              {t("secret")}
            </span>
            <div className="mt-1 flex items-center gap-2">
              <code className="bg-muted text-2xs flex-1 rounded px-2 py-1 font-mono break-all">
                {reveal.plaintextSecret}
              </code>
              <Button
                size="sm"
                variant="outline"
                onClick={() => copy(reveal.plaintextSecret, t("secretCopied"))}
              >
                {t("copySecret")}
              </Button>
            </div>
          </div>
          <p className="text-muted-foreground text-xs">
            {t.rich("help", { code: (chunks) => <code>{chunks}</code> })}
          </p>
        </div>
        <DialogFooter>
          <Button onClick={onClose}>{t("done")}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PublicKeyCell({ value }: { value: string }) {
  const t = useTranslations("sourceProviders.keys");
  const [copied, setCopied] = React.useState(false);
  const onCopy = React.useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
      toast.success(t("publicKeyCopied"));
    } catch {
      toast.error(t("copyFailedPaste"));
    }
  }, [t, value]);
  const truncated = value.length > 60 ? `${value.slice(0, 60)}…` : value;
  return (
    <div className="flex items-center gap-2">
      <code className="text-2xs font-mono">{truncated}</code>
      <Button size="sm" variant="ghost" onClick={onCopy}>
        {copied ? t("copied") : t("copy")}
      </Button>
    </div>
  );
}
