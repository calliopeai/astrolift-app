"use client";

import { CopyIcon, KeyRoundIcon, PlusIcon, RefreshCwIcon, ShieldOffIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

import type { CreateDeployTokenInput, DeployToken, DeployTokenSecretReveal } from "./secrets.types";
import type { useDeployTokens } from "./use-deploy-tokens";

export type DeployTokensScreenProps = ReturnType<typeof useDeployTokens> & {
  /** The app tab bar. */
  tabs: React.ReactNode;
};

/**
 * The app's Access › Deploy tokens section, on the embedded list (spec 44
 * §5.1): cursor pages, search over name, last 4, IP and user agent. Holds
 * only UI state (the create sheet and the rotate / revoke confirms);
 * everything that talks to the server comes in from useDeployTokens.
 */
export function DeployTokensScreen({
  slug,
  list,
  rows,
  loading,
  stale,
  error,
  onRetry,
  nextCursor,
  totalCount,
  busy,
  reveal,
  onDismissReveal,
  onCreate,
  onRotate,
  onLoadRotationGrace,
  rotationScopeKey,
  onRevoke,
  tabs,
}: DeployTokensScreenProps) {
  const tr = useTranslations("apps.tokens");
  const [createOpen, setCreateOpen] = React.useState(false);
  const [rotation, setRotation] = React.useState<{ token: DeployToken; scope: string } | null>(
    null
  );
  if (rotation && rotation.scope !== rotationScopeKey) setRotation(null);
  const rotateTarget = rotation?.scope === rotationScopeKey ? rotation.token : null;
  const [revokeTarget, setRevokeTarget] = React.useState<DeployToken | null>(null);

  const columns: Column<DeployToken>[] = [
    {
      id: "name",
      header: tr("columns.name"),
      cellClassName: "font-medium",
      cell: (token) => token.name,
    },
    {
      id: "last4",
      header: tr("columns.last4"),
      width: "w-24",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (token) => `…${token.last4}`,
    },
    {
      id: "scopes",
      header: tr("columns.scopes"),
      cell: (token) => (
        <div className="flex flex-wrap gap-1">
          {token.scopes.map((s) => (
            <Badge key={s} variant="outline" className="text-2xs font-mono">
              {s}
            </Badge>
          ))}
          {token.scopes.length === 0 && (
            <span className="text-muted-foreground text-xs">{tr("allScopes")}</span>
          )}
        </div>
      ),
    },
    {
      id: "lastUsed",
      header: tr("columns.lastUsed"),
      cellClassName: "text-muted-foreground text-xs",
      cell: (token) => <LastUsedCell token={token} />,
    },
    {
      id: "expires",
      header: tr("columns.expires"),
      width: "w-28",
      cellClassName: "text-muted-foreground text-xs",
      cell: (token) =>
        token.expiresAt ? new Date(token.expiresAt).toLocaleDateString() : tr("never"),
    },
    {
      id: "state",
      header: tr("columns.state"),
      width: "w-24",
      cell: (token) =>
        token.isRevoked ? (
          <Badge variant="destructive">{tr("status.revoked")}</Badge>
        ) : (
          <Badge variant="secondary">{tr("status.active")}</Badge>
        ),
    },
    {
      id: "actions",
      header: tr("columns.actions"),
      align: "right",
      width: "w-48",
      cell: (token) =>
        token.isRevoked ? null : (
          <Can permission="app.update">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setRotation({ token, scope: rotationScopeKey })}
              disabled={busy}
            >
              <RefreshCwIcon className="size-3.5" />
              {tr("rotate")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setRevokeTarget(token)}
              disabled={busy}
            >
              <ShieldOffIcon className="size-3.5" />
              {tr("revoke")}
            </Button>
          </Can>
        ),
    },
  ];

  return (
    <PageShell
      title={tr("title")}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {tr("description", { slug })}
        </span>
      }
      actions={
        <Can permission="app.update">
          <Button onClick={() => setCreateOpen(true)}>
            <PlusIcon className="size-4" />
            {tr("createToken")}
          </Button>
        </Can>
      }
    >
      {tabs}
      <TooltipProvider>
        <ListPage<DeployToken>
          embedded
          list={list}
          label={tr("title")}
          columns={columns}
          rows={rows}
          getRowId={(token) => token.id}
          loading={loading}
          stale={stale}
          error={error}
          onRetry={onRetry}
          nextCursor={nextCursor}
          totalCount={totalCount}
          empty={{
            icon: <KeyRoundIcon className="size-5" />,
            title: tr("emptyTitle"),
            description: tr("emptyDescription"),
          }}
        />
      </TooltipProvider>

      <CreateTokenSheet
        open={createOpen}
        onOpenChange={setCreateOpen}
        onSubmit={async (input) => {
          const revealed = await onCreate(input);
          if (revealed) setCreateOpen(false);
          return revealed;
        }}
        busy={busy}
      />

      <RevealDialog reveal={reveal} onOpenChange={onDismissReveal} />

      {rotateTarget && (
        <RotationDialog
          key={`${rotationScopeKey}:${rotateTarget.id}`}
          token={rotateTarget}
          onClose={() => setRotation(null)}
          onLoad={onLoadRotationGrace}
          onRotate={onRotate}
        />
      )}

      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRevokeTarget(null);
        }}
        title={
          revokeTarget
            ? tr("revokeDialog.title", { name: revokeTarget.name })
            : tr("revokeDialog.fallbackTitle")
        }
        description={tr("revokeDialog.description")}
        confirmLabel={tr("revokeDialog.confirm")}
        destructive
        onConfirm={async () => {
          if (revokeTarget) await onRevoke(revokeTarget);
        }}
      />
    </PageShell>
  );
}

/** Each mount/retry reads fresh metadata; replies after close/scope change are ignored. */
function RotationDialog({
  token,
  onClose,
  onLoad,
  onRotate,
}: {
  token: DeployToken;
  onClose: () => void;
  onLoad: () => Promise<number | null>;
  onRotate: (token: DeployToken) => Promise<void>;
}) {
  const tr = useTranslations("apps.tokens.rotateDialog");
  const [attempt, retry] = React.useReducer((n: number) => n + 1, 0);
  const [result, setResult] = React.useState<{
    loader: typeof onLoad;
    attempt: number;
    seconds: number | null;
  } | null>(null);
  // A changed loader or retry invalidates the previous result synchronously,
  // before effects start a new request or a handler can admit confirmation.
  const metadata = result?.loader === onLoad && result.attempt === attempt ? result : null;
  const ready = metadata?.seconds != null;
  React.useEffect(() => {
    let current = true;
    const complete = (seconds: number | null) => {
      if (current)
        setResult({
          loader: onLoad,
          attempt,
          seconds: seconds !== null && Number.isInteger(seconds) && seconds > 0 ? seconds : null,
        });
    };
    onLoad().then(complete, () => complete(null));
    return () => {
      current = false;
    };
  }, [attempt, onLoad]);

  return (
    <ConfirmDialog
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={tr("title", { name: token.name })}
      description={
        <span className="block space-y-1">
          {metadata?.seconds != null ? (
            <>
              <span className="block">
                {tr("graceLine", { window: humanizeGrace(metadata.seconds) })}
              </span>
              <span className="text-muted-foreground block text-xs">{tr("snapshotHint")}</span>
            </>
          ) : metadata === null ? (
            <span className="block" role="status">
              {tr("loading")}
            </span>
          ) : (
            <>
              <span className="block" role="alert">
                {tr("unavailable")}
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  retry();
                }}
              >
                {tr("retry")}
              </Button>
            </>
          )}
          <span className="block">{tr("newLine")}</span>
          <span className="text-muted-foreground block text-xs">{tr("immediateHint")}</span>
        </span>
      }
      confirmLabel={tr("confirm")}
      confirmDisabled={!ready}
      onConfirm={async () => {
        if (metadata?.seconds != null) await onRotate(token);
      }}
    />
  );
}

// ─── LastUsedCell (#425) ────────────────────────────────────────────
// Displays the relative timestamp the token was last exercised by
// CI and — on hover — the IP + User-Agent of that caller. Operators
// investigating a leaked token use this to identify which runner
// last held it (the row only records the *most recent* use, so the
// forensic trail is one hop deep but enough to begin an audit).

function LastUsedCell({ token }: { token: DeployToken }) {
  const tr = useTranslations("apps.tokens.lastUsedCell");
  if (!token.lastUsedAt) {
    return <span>—</span>;
  }
  const stamp = new Date(token.lastUsedAt).toLocaleString();
  const hasForensics = Boolean(token.lastUsedIp || token.lastUsedAgent);
  if (!hasForensics) {
    return <span>{stamp}</span>;
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-help underline decoration-dotted underline-offset-2">{stamp}</span>
      </TooltipTrigger>
      <TooltipContent>
        <div className="max-w-[28rem] space-y-1">
          {token.lastUsedIp && (
            <div className="flex gap-2">
              <span className="text-background/70 font-medium">{tr("ip")}</span>
              <code className="font-mono break-all">{token.lastUsedIp}</code>
            </div>
          )}
          {token.lastUsedAgent && (
            <div className="flex gap-2">
              <span className="text-background/70 font-medium">{tr("agent")}</span>
              <code className="font-mono break-all">{token.lastUsedAgent}</code>
            </div>
          )}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

// Preserve the configured duration exactly, including non-whole hours/minutes.
function humanizeGrace(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remaining = seconds % 60;
  return [
    days && `${days}d`,
    hours && `${hours}h`,
    minutes && `${minutes}m`,
    remaining && `${remaining}s`,
  ]
    .filter(Boolean)
    .join(" ");
}

function CreateTokenSheet({
  open,
  onOpenChange,
  onSubmit,
  busy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (input: CreateDeployTokenInput) => Promise<boolean>;
  busy: boolean;
}) {
  const tr = useTranslations("apps.tokens.createSheet");
  const tCommon = useTranslations("apps.common");
  const [name, setName] = React.useState("");
  const [scopes, setScopes] = React.useState("");
  const [expiresIn, setExpiresIn] = React.useState("365");

  React.useEffect(() => {
    if (!open) {
      setName("");
      setScopes("");
      setExpiresIn("365");
    }
  }, [open]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>{tr("title")}</SheetTitle>
          <SheetDescription>{tr("description")}</SheetDescription>
        </SheetHeader>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!name.trim()) return;
            const days = Number(expiresIn) || 365;
            const expiresAt = new Date();
            expiresAt.setDate(expiresAt.getDate() + days);
            await onSubmit({
              name: name.trim(),
              scopes: scopes.trim()
                ? scopes
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean)
                : null,
              expiresAtIso: expiresAt.toISOString(),
            });
          }}
          className="flex flex-1 flex-col gap-4 px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="t-name">{tr("name")}</Label>
            <Input
              id="t-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="github-actions"
              autoFocus
              required
              spellCheck={false}
              className="font-mono"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="t-scopes">{tr("scopes")}</Label>
            <Input
              id="t-scopes"
              value={scopes}
              onChange={(e) => setScopes(e.target.value)}
              placeholder="deploy,rollback"
              spellCheck={false}
              className="font-mono"
            />
            <p className="text-muted-foreground text-xs">{tr("scopesHint")}</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="t-expires">{tr("expires")}</Label>
            <Input
              id="t-expires"
              value={expiresIn}
              onChange={(e) => setExpiresIn(e.target.value)}
              type="number"
              min={1}
              max={3650}
              className="font-mono"
            />
          </div>
          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {tCommon("cancel")}
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy ? tr("submitting") : tr("submit")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

function RevealDialog({
  reveal,
  onOpenChange,
}: {
  reveal: DeployTokenSecretReveal | null;
  onOpenChange: () => void;
}) {
  const tr = useTranslations("apps.tokens.revealDialog");
  return (
    <AlertDialog open={reveal !== null} onOpenChange={(o) => !o && onOpenChange()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{tr("title")}</AlertDialogTitle>
          <AlertDialogDescription>{tr("description")}</AlertDialogDescription>
        </AlertDialogHeader>
        {reveal && (
          <div className="space-y-3 py-2">
            <div className="text-muted-foreground text-xs">
              <span className="font-medium">{reveal.token.name}</span> · {tr("last4Label")}{" "}
              <code className="bg-muted rounded px-1">{reveal.token.last4}</code>
            </div>
            <div className="bg-muted relative rounded p-3 font-mono text-xs break-all">
              {reveal.plaintextSecret}
              <Button
                size="sm"
                variant="ghost"
                className="absolute top-1 right-1 h-7"
                onClick={() => {
                  navigator.clipboard.writeText(reveal.plaintextSecret);
                  toast.success(tr("copied"));
                }}
              >
                <CopyIcon className="size-3" />
                {tr("copy")}
              </Button>
            </div>
            {reveal.rotationGraceSeconds > 0 && (
              <p className="text-muted-foreground text-xs">
                {tr("graceNote", {
                  window: humanizeGrace(reveal.rotationGraceSeconds),
                })}
              </p>
            )}
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogAction onClick={onOpenChange}>{tr("done")}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
