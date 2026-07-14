"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
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
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Section } from "@/components/ui/section";
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
  LOGOUT_ALL_SESSIONS,
  REVOKE_ASTROLIFT_SESSION,
} from "@/graphql/identity/identity.mutations";
import { LIST_ACTIVE_SESSIONS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftActiveSession,
  AstroliftClientKind,
  AstroliftLogoutAllSessionsPayload,
  AstroliftRevokeAstroliftSessionPayload,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

interface SessionsResp {
  astroliftActiveSessions: AstroliftActiveSession[];
}

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

export function SecuritySettingsClient() {
  const t = useTranslations("securitySessions");
  const fmt = useFormatters();
  const { data, loading, error, refetch } = useQuery<SessionsResp>(LIST_ACTIVE_SESSIONS, {
    fetchPolicy: "cache-and-network",
  });

  const [logoutAll, { loading: signingOut }] = useMutation<{
    logoutAllSessions: MutationResult<AstroliftLogoutAllSessionsPayload>;
  }>(LOGOUT_ALL_SESSIONS);

  const [revokeSession, { loading: revoking }] = useMutation<{
    revokeAstroliftSession: MutationResult<AstroliftRevokeAstroliftSessionPayload>;
  }>(REVOKE_ASTROLIFT_SESSION);

  const sessions = data?.astroliftActiveSessions ?? [];
  const otherCount = sessions.filter((s) => !s.isCurrent).length;

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
    if (otherCount === 0) {
      toast.info(t("toasts.noOthers"));
      return;
    }
    setConfirmSignOut(true);
  }

  async function handleSignOutAll() {
    const { data: resp } = await logoutAll({
      variables: { input: { keepCurrent: true } },
    });
    if (resp?.logoutAllSessions.ok) {
      toast.success(
        t("toasts.signedOut", {
          count: resp.logoutAllSessions.data?.revokedCount ?? 0,
        })
      );
      refetch();
    } else {
      throw new Error(resp?.logoutAllSessions.errors?.[0]?.message ?? t("toasts.signOutFailed"));
    }
  }

  async function handleRevokeOne() {
    if (!pendingRevokeId) return;
    const sessionId = pendingRevokeId;
    setPendingRevokeId(null);
    const { data: resp } = await revokeSession({
      variables: { input: { sessionId } },
    });
    if (resp?.revokeAstroliftSession.ok) {
      toast.success(
        resp.revokeAstroliftSession.data?.revoked ? t("toasts.revoked") : t("toasts.alreadyRevoked")
      );
      refetch();
    } else {
      toast.error(resp?.revokeAstroliftSession.errors?.[0]?.message ?? t("toasts.revokeFailed"));
    }
  }

  const pendingTarget = pendingRevokeId ? sessions.find((s) => s.id === pendingRevokeId) : null;

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
        {loading && !data ? (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : error ? (
          <div className="border-destructive/40 bg-destructive/5 flex items-start gap-2 rounded-md border p-3 text-sm">
            <AlertTriangleIcon className="text-destructive mt-0.5 size-4" />
            <div className="flex-1">
              <p className="text-destructive font-medium">{t("loadError")}</p>
              <p className="text-muted-foreground text-xs">{error.message}</p>
            </div>
          </div>
        ) : sessions.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("noSessions")}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("columns.kind")}</TableHead>
                <TableHead>{t("columns.label")}</TableHead>
                <TableHead>{t("columns.status")}</TableHead>
                <TableHead>{t("columns.lastSeen")}</TableHead>
                <TableHead>{t("columns.expires")}</TableHead>
                <TableHead className="text-right">{t("columns.actions")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sessions.map((s) => (
                <TableRow key={s.id}>
                  <TableCell>{clientKindBadge(s.clientKind)}</TableCell>
                  <TableCell className="text-muted-foreground text-xs">{s.label || "—"}</TableCell>
                  <TableCell>
                    {s.isCurrent ? (
                      <Badge className="bg-success/15 text-success-fg">{t("status.current")}</Badge>
                    ) : (
                      <Badge variant="secondary">{t("status.other")}</Badge>
                    )}
                  </TableCell>
                  <TableCell
                    className="text-muted-foreground text-xs"
                    title={s.lastSeenAt ? fmt.formatDateTime(s.lastSeenAt) : undefined}
                  >
                    {relativeFromNow(s.lastSeenAt)}
                  </TableCell>
                  <TableCell className="text-muted-foreground text-xs">
                    {s.expiresAt ? fmt.formatDateTime(s.expiresAt) : "—"}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={s.isCurrent || revoking}
                      onClick={() => setPendingRevokeId(s.id)}
                    >
                      {t("actions.revoke")}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
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
        onConfirm={handleSignOutAll}
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
