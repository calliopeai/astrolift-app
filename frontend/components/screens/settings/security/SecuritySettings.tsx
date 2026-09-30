"use client";

import {
  GlobeIcon,
  KeyRoundIcon,
  LogOutIcon,
  MonitorIcon,
  PuzzleIcon,
  ShieldCheckIcon,
  SmartphoneIcon,
  TerminalIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { selectRows } from "@/components/list/select-rows";
import type { ListStateController } from "@/components/list/list-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Section } from "@/components/ui/section";
import type {
  AstroliftActiveSession,
  AstroliftClientKind,
} from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { SESSIONS_SELECT } from "./sessions-list";
import type { useSecuritySettings } from "./use-security-settings";

export type SecuritySettingsViewProps = Omit<ReturnType<typeof useSecuritySettings>, "list">;

const CLIENT_KIND_ICONS: Record<
  AstroliftClientKind,
  React.ComponentType<{ className?: string }>
> = {
  web: GlobeIcon,
  cli: TerminalIcon,
  mobile: SmartphoneIcon,
  browser_extension: PuzzleIcon,
  api_token: KeyRoundIcon,
};

/**
 * Settings > Security: the account's active sessions as the page's one
 * embedded list (search, kind and status filters, sort, numbered pages run
 * over the sessions in hand; sign out everywhere, revoke one in `⋯`), plus
 * the API tokens and MFA pointers.
 */
export function SecuritySettingsView({
  list,
  sessions,
  otherCount,
  loading,
  errorMessage,
  signingOut,
  revoking,
  onSignOutAll,
  onRevoke,
}: SecuritySettingsViewProps & { list: ListStateController }) {
  const t = useTranslations("securitySessions");
  const fmt = useFormatters();

  const [confirmSignOut, setConfirmSignOut] = React.useState(false);
  const [pendingRevokeId, setPendingRevokeId] = React.useState<string | null>(null);

  // Captured at render-time once so the relative-time helper stays pure
  // (lint disallows calling Date.now() during render). The "Last seen"
  // column is naturally fresh because Apollo re-queries every navigation
  // and the toast/refetch flow re-renders this component.
  const [renderNow, setRenderNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    // Tick once a minute so a long-lived tab doesn't show stale
    // "3m ago" hours later. Cheap — single setInterval per page.
    const id = setInterval(() => setRenderNow(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  function relativeFromNow(iso: string | null | undefined): string {
    if (!iso) return t("relative.never");
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return t("relative.never");
    const diffSec = Math.round((renderNow - then) / 1000);
    if (diffSec < 5) return t("relative.justNow");
    if (diffSec < 60) return t("relative.sAgo", { n: diffSec });
    const minutes = Math.round(diffSec / 60);
    if (minutes < 60) return t("relative.mAgo", { n: minutes });
    const hours = Math.round(minutes / 60);
    if (hours < 24) return t("relative.hAgo", { n: hours });
    const days = Math.round(hours / 24);
    if (days < 30) return t("relative.dAgo", { n: days });
    const months = Math.round(days / 30);
    if (months < 12) return t("relative.moAgo", { n: months });
    const years = Math.round(months / 12);
    return t("relative.yAgo", { n: years });
  }

  function clientKindBadge(kind: AstroliftClientKind): React.JSX.Element {
    const Icon = CLIENT_KIND_ICONS[kind] ?? MonitorIcon;
    return (
      <Badge variant="outline" className="gap-1">
        <Icon className="size-3" />
        {t(`kind.${kind}`)}
      </Badge>
    );
  }

  function requestSignOutAll() {
    if (!signingOut && otherCount > 0) setConfirmSignOut(true);
  }

  async function handleRevokeOne() {
    if (!pendingRevokeId) return;
    const sessionId = pendingRevokeId;
    setPendingRevokeId(null);
    await onRevoke(sessionId);
  }

  const pendingTarget = pendingRevokeId ? sessions.find((s) => s.id === pendingRevokeId) : null;

  const { state } = list;
  const page = selectRows(
    sessions,
    {
      filters: list.filters,
      q: state.q,
      sort: state.sort,
      page: state.page,
      pageSize: state.pageSize,
    },
    SESSIONS_SELECT
  );

  const columns: Column<AstroliftActiveSession>[] = [
    {
      id: "kind",
      header: t("columns.kind"),
      sortKey: "kind",
      cell: (s) => clientKindBadge(s.clientKind),
    },
    {
      id: "label",
      header: t("columns.label"),
      cellClassName: "text-muted-foreground max-w-72",
      cell: (s) => (
        <span className="block truncate text-xs" title={s.label || undefined}>
          {s.label || "—"}
        </span>
      ),
    },
    {
      id: "status",
      header: t("columns.status"),
      cell: (s) =>
        s.isCurrent ? (
          <Badge className="bg-success/15 text-success-fg">{t("status.current")}</Badge>
        ) : (
          <Badge variant="secondary">{t("status.other")}</Badge>
        ),
    },
    {
      id: "lastSeen",
      header: t("columns.lastSeen"),
      sortKey: "lastSeen",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (s) => (
        <span title={s.lastSeenAt ? fmt.formatDateTime(s.lastSeenAt) : undefined}>
          {relativeFromNow(s.lastSeenAt)}
        </span>
      ),
    },
    {
      id: "expires",
      header: t("columns.expires"),
      sortKey: "expires",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (s) => (s.expiresAt ? fmt.formatDateTime(s.expiresAt) : "—"),
    },
  ];

  return (
    <div className="grid gap-4">
      <Section
        title={
          <span className="flex items-center gap-2">
            <MonitorIcon className="size-4" /> {t("title")}
          </span>
        }
        description={t("description")}
        action={
          <Button
            onClick={requestSignOutAll}
            variant="outline"
            disabled={signingOut || otherCount === 0}
          >
            <LogOutIcon className="size-4" />
            {signingOut ? t("signingOut") : t("signOutEverywhere")}
          </Button>
        }
      >
        <ListPage<AstroliftActiveSession>
          embedded
          list={list}
          label={t("title")}
          columns={columns}
          rows={page.rows}
          getRowId={(s) => s.id}
          rowActions={(s) => (
            <DropdownMenuItem
              disabled={s.isCurrent || revoking}
              onSelect={() => setPendingRevokeId(s.id)}
            >
              <LogOutIcon className="size-4" />
              {t("actions.revoke")}
            </DropdownMenuItem>
          )}
          loading={loading}
          error={errorMessage !== null ? { message: errorMessage } : null}
          totalCount={page.totalCount}
          empty={{
            icon: <MonitorIcon className="size-5" />,
            title: t("noSessions"),
          }}
        />
      </Section>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <KeyRoundIcon className="size-4" /> API tokens
            </CardTitle>
            <CardDescription>
              Long-lived credentials for CLIs, CI runs, and scripts.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild variant="outline">
              <Link href="/tokens">Manage tokens</Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldCheckIcon className="size-4" /> Multi-factor authentication
            </CardTitle>
            <CardDescription>
              Configured at the IdP layer. Astrolift sees the assertion via OIDC.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-sm">
              Set up MFA in your identity provider (Auth0 / Okta / Azure AD / Google Workspace). The
              platform enforces session-freshness for high-risk actions via ABAC policies.
            </p>
          </CardContent>
        </Card>
      </div>

      <ConfirmDialog
        open={confirmSignOut}
        onOpenChange={setConfirmSignOut}
        title={t("confirmSignOutAll.title", { count: otherCount })}
        description={t("confirmSignOutAll.description")}
        confirmLabel={t("confirmSignOutAll.confirm")}
        destructive
        onConfirm={onSignOutAll}
      />

      <ConfirmDialog
        open={pendingRevokeId !== null}
        onOpenChange={(open) => {
          if (!open) setPendingRevokeId(null);
        }}
        title={t("confirmRevoke.title")}
        description={
          pendingTarget
            ? `${t(`kind.${pendingTarget.clientKind}`)}${pendingTarget.label ? ` — ${pendingTarget.label}` : ""}: ${t("confirmRevoke.description")}`
            : t("confirmRevoke.description")
        }
        confirmLabel={t("confirmRevoke.confirm")}
        destructive
        onConfirm={handleRevokeOne}
      />
    </div>
  );
}
