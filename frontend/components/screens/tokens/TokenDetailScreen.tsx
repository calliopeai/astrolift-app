"use client";

import {
  DetailTimestamp,
  EntityDetailShell,
  type Dot,
} from "@/components/detail/EntityDetailShell";
import { useTranslations, useNow } from "next-intl";
import { useFormatters } from "@/lib/i18n/formatters";
import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import type { useTokenDetail } from "./use-token-detail";

export type TokenDetailScreenProps = ReturnType<typeof useTokenDetail>;

/**
 * API token detail (#1106). Metadata only — the plaintext secret is shown
 * exactly once at creation and never stored, so only the non-secret last-4
 * suffix is displayed here.
 */
export function TokenDetailScreen({
  id,
  token: tk,
  loading,
  error,
  onRetry,
}: TokenDetailScreenProps) {
  const t = useTranslations("apiKeys");
  const fmt = useFormatters();
  const now = useNow({ updateInterval: 60_000 });
  const expired = tk?.expiresAt ? new Date(tk.expiresAt).getTime() < now.getTime() : false;
  const readFailure = error ? (
    <div role="alert" className="space-y-2 text-sm">
      <p>{t("detail.readFailed")}</p>
      <p className="[overflow-wrap:anywhere]">{error.message}</p>
      <Button variant="outline" onClick={onRetry}>
        {t("detail.retry")}
      </Button>
    </div>
  ) : null;
  if (error && !tk) return <PageShell title={t("title")}>{readFailure}</PageShell>;
  const state = tk ? (tk.isRevoked ? "revoked" : expired ? "expired" : "active") : undefined;
  const stateTone: Dot | undefined =
    state === "active"
      ? "ok"
      : state === "revoked"
        ? "error"
        : state === "expired"
          ? "muted"
          : undefined;

  return (
    <EntityDetailShell
      loading={loading}
      notFound={!tk}
      breadcrumb={{ label: t("title"), href: "/tokens" }}
      heading={tk ? tk.name : t("detail.shortToken", { id: id.slice(0, 8) })}
      status={state}
      statusTone={stateTone}
      createdAt={tk?.createdAt}
      notFoundLabel={t("detail.token")}
      presentation={{
        overview: t("detail.overview"),
        created: t("created"),
        statusLabel: state ? t(state) : undefined,
        formatTimestamp: fmt.formatDateTime,
        notFoundTitle: t("detail.notFoundTitle"),
        notFoundDescription: t("detail.notFoundDescription"),
        back: t("detail.back"),
      }}
      overview={
        tk
          ? [
              { term: t("name"), description: <span className="font-medium">{tk.name}</span> },
              {
                term: t("detail.token"),
                description: <span className="font-mono text-xs">••••{tk.tokenLast4}</span>,
              },
              {
                term: t("detail.owner"),
                description: <span className="font-mono text-xs">{tk.user.username}</span>,
              },
              {
                term: t("detail.team"),
                description: tk.teamSlug ? (
                  <span className="font-mono text-xs">{tk.teamSlug}</span>
                ) : (
                  <span className="text-muted-foreground">{t("detail.orgWide")}</span>
                ),
              },
              {
                term: t("expires"),
                description: <DetailTimestamp iso={tk.expiresAt} format={fmt.formatDateTime} />,
              },
              {
                term: t("lastUsed"),
                description: <DetailTimestamp iso={tk.lastUsedAt} format={fmt.formatDateTime} />,
              },
              {
                term: t("detail.lastIp"),
                description: tk.lastUsedIp ? (
                  <span className="font-mono text-xs">{tk.lastUsedIp}</span>
                ) : (
                  <span className="text-muted-foreground">—</span>
                ),
              },
              {
                term: t("detail.lastAgent"),
                description: tk.lastUsedAgent ? (
                  <span className="font-mono text-xs break-all">{tk.lastUsedAgent}</span>
                ) : (
                  <span className="text-muted-foreground">—</span>
                ),
              },
              {
                term: t("created"),
                description: <DetailTimestamp iso={tk.createdAt} format={fmt.formatDateTime} />,
              },
            ]
          : []
      }
    >
      {readFailure}
      {tk ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("scopes")}</CardTitle>
          </CardHeader>
          <CardContent>
            {tk.scopes.length === 0 ? (
              <p className="text-muted-foreground text-sm">{t("detail.noScopes")}</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {tk.scopes.map((scope) => (
                  <Badge key={scope} variant="outline" className="font-mono text-xs">
                    {scope}
                  </Badge>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
