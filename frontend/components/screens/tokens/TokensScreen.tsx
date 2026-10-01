"use client";

import {
  AlertTriangleIcon,
  CableIcon,
  CheckCircle2Icon,
  CopyIcon,
  KeyIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";
import * as React from "react";
import { useTranslations, useNow } from "next-intl";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { adminCrumbs } from "@/components/screens/administration/insights/header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useFormatters } from "@/lib/i18n/formatters";
import type { AstroliftApiToken } from "@/graphql/identity/identity.types";

import type { useTokens } from "./use-tokens";

export type TokensScreenProps = ReturnType<typeof useTokens> & {
  /** The scope picker in the create sheet; it loads the scope catalog, so the
   *  route supplies it and it only fetches while the sheet is open. */
  renderScopePicker: (props: {
    value: string[];
    onChange: (next: string[]) => void;
  }) => React.ReactNode;
};

/** An admin token with no expiry, or one more than 90 days out (#2120). */
function isLongLivedAdmin(tk: AstroliftApiToken, now: Date): boolean {
  if (!tk.scopes.includes("admin") || tk.isRevoked) return false;
  if (!tk.expiresAt) return true;
  return new Date(tk.expiresAt).getTime() - now.getTime() > 90 * 24 * 60 * 60 * 1000;
}

const DEFAULT_SELECTED_SCOPES: string[] = ["read:apps", "read:clusters", "mcp:read"];

/**
 * Admin › API keys (spec 44 §5.1): the org's tokens on the shared list,
 * views All · Mine · Active · Revoked, cursor paged, revoke in each row's
 * `⋯`. The MCP endpoint and the one-time plaintext reveal sit above the
 * filters. Holds only UI state (the create form, the revoke confirm);
 * everything that talks to the server comes in from useTokens.
 */
export function TokensScreen({
  list,
  rows,
  totalCount,
  nextCursor,
  loading,
  stale,
  error,
  onRetry,
  creating,
  revoking,
  createdToken,
  onDismissCreated,
  mcpEndpoint,
  onCreate,
  onRevoke,
  onCopyPlaintext,
  onCopyMcpEndpoint,
  renderScopePicker,
}: TokensScreenProps) {
  const fmt = useFormatters();
  const t = useTranslations("apiKeys");
  const now = useNow({ updateInterval: 60_000 });
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [expiresInDays, setExpiresInDays] = React.useState("90");
  const [selectedScopes, setSelectedScopes] = React.useState<string[]>(DEFAULT_SELECTED_SCOPES);
  const [revokeTarget, setRevokeTarget] = React.useState<AstroliftApiToken | null>(null);

  function resetForm() {
    setName("");
    setExpiresInDays("90");
    setSelectedScopes(DEFAULT_SELECTED_SCOPES);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const ok = await onCreate({ name, expiresInDays, scopes: selectedScopes });
    if (ok) {
      resetForm();
      setOpen(false);
    }
  }

  const columns: Column<AstroliftApiToken>[] = [
    {
      id: "name",
      header: t("name"),
      cellClassName: "max-w-64 font-medium",
      cell: (tk) => (
        <span className="block truncate" title={tk.name}>
          {tk.name}
        </span>
      ),
    },
    {
      id: "suffix",
      header: t("suffix"),
      width: "w-28",
      cellClassName: "font-mono text-xs",
      cell: (tk) => `…${tk.tokenLast4}`,
    },
    {
      id: "scopes",
      header: t("scopes"),
      cell: (tk) => (
        <div className="flex flex-wrap gap-1">
          {tk.scopes.length === 0 ? (
            <span className="text-muted-foreground text-xs">{t("none")}</span>
          ) : (
            tk.scopes.map((s) => (
              <Badge key={s} variant="outline" className="text-2xs font-mono">
                {s}
              </Badge>
            ))
          )}
        </div>
      ),
    },
    {
      id: "canDo",
      header: t("canDo"),
      width: "w-36",
      cell: (tk) => (
        <Tooltip>
          {/* Above the row's stretched link, or the overlay eats the hover. */}
          <TooltipTrigger asChild>
            <span className="relative z-10 inline-flex cursor-help items-center gap-1 text-xs">
              {isLongLivedAdmin(tk, now) && (
                <AlertTriangleIcon
                  className="text-warning-fg size-3.5"
                  aria-label={t("longLivedAdmin")}
                />
              )}
              {t("permissionCount", { count: tk.effectivePermissions.length })}
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-sm">
            {isLongLivedAdmin(tk, now) && (
              <p className="mb-1 font-medium">{t("adminExpiryWarning")}</p>
            )}
            <p className="opacity-75">{t("effectiveGuidance")}</p>
            <p className="text-2xs font-mono [overflow-wrap:anywhere]">
              {tk.effectivePermissions.join(", ") || t("nothing")}
            </p>
          </TooltipContent>
        </Tooltip>
      ),
    },
    {
      id: "lastUsed",
      header: t("lastUsed"),
      cellClassName: "text-muted-foreground text-xs",
      cell: (tk) => <LastUsedCell token={tk} />,
    },
    {
      id: "created",
      header: t("created"),
      width: "w-28",
      cellClassName: "text-muted-foreground text-sm",
      cell: (tk) => fmt.formatDate(tk.createdAt),
    },
    {
      id: "expires",
      header: t("expires"),
      width: "w-28",
      cellClassName: "text-muted-foreground text-sm",
      cell: (tk) => (tk.expiresAt ? fmt.formatDate(tk.expiresAt) : t("never")),
    },
    {
      id: "status",
      header: t("status"),
      width: "w-28",
      cell: (tk) =>
        tk.isRevoked ? (
          <Badge variant="destructive" className="gap-1">
            <AlertTriangleIcon className="size-3" />
            {t("revoked")}
          </Badge>
        ) : (
          <Badge variant="secondary">{t("active")}</Badge>
        ),
    },
  ];

  const notice = (
    <>
      {createdToken && (
        <Card className="border-success-border bg-success/5">
          <CardContent className="flex flex-col gap-3 p-4">
            <div className="flex min-w-0 items-center gap-2">
              <CheckCircle2Icon className="text-success-fg size-4 shrink-0" />
              <p className="min-w-0 text-sm font-medium [overflow-wrap:anywhere]">
                {t.rich("createdNotice", {
                  name: () => <span className="font-mono">{createdToken.apiToken.name}</span>,
                })}
              </p>
            </div>
            <p className="text-muted-foreground text-xs">{t("revealGuidance")}</p>
            <div className="flex min-w-0 items-center gap-2">
              <code className="bg-background min-w-0 flex-1 rounded-md border px-3 py-2 font-mono text-xs break-all">
                {createdToken.plaintext}
              </code>
              <Button size="sm" variant="outline" onClick={onCopyPlaintext}>
                <CopyIcon className="size-4" />
                {t("copy")}
              </Button>
            </div>
            <div className="flex justify-end">
              <Button size="sm" variant="ghost" onClick={onDismissCreated}>
                {t("dismissReveal")}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
      <Card>
        <CardContent className="space-y-3 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 space-y-1">
              <div className="flex items-center gap-2 font-medium">
                <CableIcon className="size-4" /> {t("mcpTitle")}
              </div>
              <p className="text-muted-foreground max-w-3xl text-xs">
                {t.rich("mcpGuidance", {
                  code: () => <code>Authorization: Bearer alft_at_…</code>,
                })}
              </p>
            </div>
            <div className="flex min-w-0 items-center gap-2">
              <code className="bg-muted min-w-0 rounded px-2 py-1.5 font-mono text-xs [overflow-wrap:anywhere]">
                {mcpEndpoint}
              </code>
              <Button size="sm" variant="outline" onClick={onCopyMcpEndpoint}>
                <CopyIcon className="size-4" /> {t("copyUrl")}
              </Button>
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Badge variant="outline">{t("mcpRead", { scope: "mcp:read" })}</Badge>
            <Badge variant="outline">{t("mcpDispatch", { scope: "mcp:dispatch" })}</Badge>
            <Badge variant="outline">{t("mcpWrite", { scope: "mcp:write" })}</Badge>
          </div>
        </CardContent>
      </Card>
    </>
  );

  return (
    <TooltipProvider>
      <ListPage<AstroliftApiToken>
        header={{
          crumbs: adminCrumbs("tokens", t("title")),
          title: t("title"),
          context: t("context"),
          primaryAction: (
            <Can permission="api_token.create">
              <Button onClick={() => setOpen(true)}>
                <PlusIcon className="size-4" />
                {t("newToken")}
              </Button>
            </Can>
          ),
        }}
        notice={notice}
        list={list}
        label={t("title")}
        columns={columns}
        rows={rows}
        getRowId={(t) => t.id}
        rowHref={(t) => `/tokens/${t.id}`}
        rowActions={(tk) => (
          <Can permission="api_token.revoke">
            <DropdownMenuItem
              disabled={revoking || tk.isRevoked}
              onSelect={() => setRevokeTarget(tk)}
            >
              <Trash2Icon className="size-4" />
              {t("revoke")}
            </DropdownMenuItem>
          </Can>
        )}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        nextCursor={nextCursor}
        empty={{
          icon: <KeyIcon className="size-5" />,
          title: t("emptyTitle"),
          description: t("emptyDescription"),
        }}
      />

      <Sheet
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) resetForm();
        }}
      >
        <SheetContent className="flex flex-col">
          <SheetHeader>
            <SheetTitle>{t("createTitle")}</SheetTitle>
            <SheetDescription>{t("createDescription")}</SheetDescription>
          </SheetHeader>
          <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
            <div className="space-y-2">
              <Label htmlFor="token-name">{t("name")}</Label>
              <Input
                id="token-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="ci-runner-prod"
                autoFocus
                required
              />
              <p className="text-muted-foreground text-xs">{t("nameGuidance")}</p>
            </div>
            <div className="space-y-2">
              <Label>{t("scopes")}</Label>
              <p className="text-muted-foreground text-xs">{t("scopeGuidance")}</p>
              {renderScopePicker({ value: selectedScopes, onChange: setSelectedScopes })}
            </div>
            <div className="space-y-2">
              <Label htmlFor="token-expires">{t("expiresDays")}</Label>
              <Input
                id="token-expires"
                type="number"
                min={0}
                max={365}
                value={expiresInDays}
                onChange={(e) => setExpiresInDays(e.target.value)}
              />
              <p className="text-muted-foreground text-xs">
                {t("expiryGuidance", { defaultDays: 90, maxDays: 365 })}
              </p>
            </div>
            <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setOpen(false);
                  resetForm();
                }}
              >
                {t("cancel")}
              </Button>
              <Button type="submit" disabled={creating || !name || selectedScopes.length === 0}>
                {creating ? t("creating") : t("create")}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>

      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRevokeTarget(null);
        }}
        title={revokeTarget ? t("revokeNamed", { name: revokeTarget.name }) : t("revokeTitle")}
        description={t("revokeDescription")}
        confirmLabel={t("revokeConfirm")}
        destructive
        onConfirm={async () => {
          if (revokeTarget) await onRevoke(revokeTarget);
        }}
      />
    </TooltipProvider>
  );
}

function LastUsedCell({ token }: { token: AstroliftApiToken }) {
  const fmt = useFormatters();
  if (!token.lastUsedAt) {
    return <span className="text-muted-foreground">—</span>;
  }
  const ts = fmt.formatDateTime(token.lastUsedAt);
  const hasMeta = Boolean(token.lastUsedIp || token.lastUsedAgent);
  if (!hasMeta) {
    return <span>{ts}</span>;
  }
  return (
    <Tooltip>
      {/* Above the row's stretched link, or the overlay eats the hover and
          the IP / user-agent forensics never open. */}
      <TooltipTrigger asChild>
        <span className="relative z-10 cursor-help underline decoration-dotted underline-offset-2">
          {ts}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-sm">
        <p className="text-2xs font-mono">IP: {token.lastUsedIp ?? "—"}</p>
        {token.lastUsedAgent && (
          <p className="text-2xs font-mono break-all opacity-75">UA: {token.lastUsedAgent}</p>
        )}
      </TooltipContent>
    </Tooltip>
  );
}
