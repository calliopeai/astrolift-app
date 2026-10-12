"use client";

import {
  AlertTriangleIcon,
  CheckIcon,
  CopyIcon,
  KeyRoundIcon,
  Loader2Icon,
  RotateCwIcon,
  ShieldOffIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useFormatters } from "@/lib/i18n/formatters";

import type { useDeployToken } from "./use-deploy-token";

export type DeployTokenControlViewProps = ReturnType<typeof useDeployToken>;

/**
 * Manage the app's deploy token — generate, rotate, revoke. The plaintext
 * is shown exactly once; subsequent visits only see the last-4 digits.
 * Gated on `app.update` because token lifecycle controls CI access.
 */
export function DeployTokenControlView({
  loading,
  active,
  reveal,
  onDismissReveal,
  creating,
  rotating,
  revoking,
  onCreate,
  onRotate,
  onRevoke,
}: DeployTokenControlViewProps) {
  const [confirmRotate, setConfirmRotate] = useState(false);
  const [confirmRevoke, setConfirmRevoke] = useState(false);
  const t = useTranslations("appControls.deployToken");
  const fmt = useFormatters();

  const missingToken = !loading && !active;

  return (
    <Card className={missingToken ? "ring-warning-border bg-warning/5" : undefined}>
      <CardContent>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            {missingToken ? (
              <AlertTriangleIcon className="text-warning-fg mt-0.5 size-4" />
            ) : (
              <KeyRoundIcon className="text-muted-foreground mt-0.5 size-4" />
            )}
            <div>
              <p className="text-sm font-medium">{t("title")}</p>
              <p className="text-muted-foreground mt-0.5 max-w-md text-xs">
                {t.rich("description", { code })}
              </p>
              {!loading && active && (
                <div className="text-muted-foreground mt-2 flex flex-wrap items-center gap-2 text-xs">
                  <Badge variant="outline" className="gap-1 font-mono">
                    …{active.last4}
                  </Badge>
                  <span>
                    {active.lastUsedAt
                      ? t("lastUsed", { date: fmt.formatDate(active.lastUsedAt) })
                      : t("neverUsed")}
                  </span>
                </div>
              )}
              {missingToken && (
                <p className="text-warning-fg mt-2 text-xs font-medium">{t("missing")}</p>
              )}
            </div>
          </div>

          <Can permission="app.update">
            <div className="flex flex-wrap items-center gap-2">
              {active ? (
                <>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setConfirmRotate(true)}
                    disabled={rotating}
                    className="gap-1.5"
                  >
                    {rotating ? (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    ) : (
                      <RotateCwIcon className="size-3.5" />
                    )}
                    {t("rotate")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setConfirmRevoke(true)}
                    disabled={revoking}
                    className="text-muted-foreground hover:text-destructive gap-1.5"
                  >
                    {revoking ? (
                      <Loader2Icon className="size-3.5 animate-spin" />
                    ) : (
                      <ShieldOffIcon className="size-3.5" />
                    )}
                    {t("revoke")}
                  </Button>
                </>
              ) : (
                <Button
                  size="sm"
                  onClick={onCreate}
                  disabled={creating}
                  className="border-warning-border bg-warning hover:bg-warning/90 gap-1.5 text-white"
                >
                  {creating ? (
                    <Loader2Icon className="size-3.5 animate-spin" />
                  ) : (
                    <KeyRoundIcon className="size-3.5" />
                  )}
                  {t("generate")}
                </Button>
              )}
            </div>
          </Can>
        </div>
      </CardContent>

      {reveal && <RevealDialog token={reveal} onClose={onDismissReveal} />}

      <ConfirmDialog
        open={confirmRotate}
        onOpenChange={setConfirmRotate}
        title={t("rotateTitle")}
        description={t("rotateDescription")}
        confirmLabel={t("rotateConfirm")}
        onConfirm={onRotate}
      />

      <ConfirmDialog
        open={confirmRevoke}
        onOpenChange={setConfirmRevoke}
        title={t("revokeTitle")}
        description={t("revokeDescription")}
        confirmLabel={t("revokeConfirm")}
        destructive
        onConfirm={onRevoke}
      />
    </Card>
  );
}

const code = (chunks: ReactNode) => (
  <code className="bg-muted rounded px-1 font-mono">{chunks}</code>
);

function RevealDialog({ token, onClose }: { token: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const t = useTranslations("appControls.deployToken");

  async function copy() {
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
      toast.success(t("copied"));
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error(t("copyFailed"));
    }
  }

  return (
    <div className="bg-background/80 fixed inset-0 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
      <div className="bg-card border-border w-full max-w-lg rounded-lg border p-6 shadow-xl">
        <h3 className="text-base font-semibold">{t("revealTitle")}</h3>
        <p className="text-warning-fg mt-2 text-xs">{t("revealWarning")}</p>
        <div className="bg-muted/50 mt-4 flex items-center gap-2 rounded-md border p-3">
          <code className="flex-1 font-mono text-xs break-all">{token}</code>
          <Button size="sm" variant="outline" onClick={copy} className="shrink-0 gap-1.5">
            {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
            {copied ? t("copiedLabel") : t("copy")}
          </Button>
        </div>
        <p className="text-muted-foreground mt-4 text-xs">{t.rich("revealStore", { code })}</p>
        <div className="mt-5 flex justify-end">
          <Button size="sm" onClick={onClose}>
            {t("saved")}
          </Button>
        </div>
      </div>
    </div>
  );
}
