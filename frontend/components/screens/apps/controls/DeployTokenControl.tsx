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
import { useState } from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

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
              <p className="text-sm font-medium">Deploy token</p>
              <p className="text-muted-foreground mt-0.5 max-w-md text-xs">
                App-scoped machine token. Use as{" "}
                <code className="bg-muted rounded px-1 font-mono">ASTROLIFT_DEPLOY_TOKEN</code> in
                your CI.
              </p>
              {!loading && active && (
                <div className="text-muted-foreground mt-2 flex flex-wrap items-center gap-2 text-xs">
                  <Badge variant="outline" className="gap-1 font-mono">
                    …{active.last4}
                  </Badge>
                  <span>
                    {active.lastUsedAt
                      ? `last used ${new Date(active.lastUsedAt).toLocaleDateString()}`
                      : "never used"}
                  </span>
                </div>
              )}
              {missingToken && (
                <p className="text-warning-fg mt-2 text-xs font-medium">
                  No token — CI cannot trigger deployments until one is generated.
                </p>
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
                    Rotate
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
                    Revoke
                  </Button>
                </>
              ) : (
                <Button
                  size="sm"
                  onClick={onCreate}
                  disabled={creating}
                  className="gap-1.5 border-amber-400/60 bg-amber-500 text-white hover:bg-amber-600 dark:bg-amber-600 dark:hover:bg-amber-700"
                >
                  {creating ? (
                    <Loader2Icon className="size-3.5 animate-spin" />
                  ) : (
                    <KeyRoundIcon className="size-3.5" />
                  )}
                  Generate token
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
        title="Rotate deploy token?"
        description="The previous value stops working immediately. Any CI run still holding the old token will fail until you update the secret on the CI side."
        confirmLabel="Rotate token"
        onConfirm={onRotate}
      />

      <ConfirmDialog
        open={confirmRevoke}
        onOpenChange={setConfirmRevoke}
        title="Revoke deploy token?"
        description="CI runs using this token will start failing immediately. Mint a new token afterwards and update your CI secret to recover. No way to un-revoke — soft delete only."
        confirmLabel="Revoke token"
        destructive
        onConfirm={onRevoke}
      />
    </Card>
  );
}

function RevealDialog({ token, onClose }: { token: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
      toast.success("Copied to clipboard.");
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Copy failed — select the value and copy manually.");
    }
  }

  return (
    <div className="bg-background/80 fixed inset-0 z-50 flex items-center justify-center p-4 backdrop-blur-sm">
      <div className="bg-card border-border w-full max-w-lg rounded-lg border p-6 shadow-xl">
        <h3 className="text-base font-semibold">Deploy token — copy it now</h3>
        <p className="text-warning-fg mt-2 text-xs">
          This is the only time the token is shown. If you navigate away without copying, rotate
          again to mint a fresh one.
        </p>
        <div className="bg-muted/50 mt-4 flex items-center gap-2 rounded-md border p-3">
          <code className="flex-1 font-mono text-xs break-all">{token}</code>
          <Button size="sm" variant="outline" onClick={copy} className="shrink-0 gap-1.5">
            {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
        <p className="text-muted-foreground mt-4 text-xs">
          Store as <code className="bg-muted rounded px-1 font-mono">ASTROLIFT_DEPLOY_TOKEN</code>{" "}
          in your CI provider&rsquo;s secrets.
        </p>
        <div className="mt-5 flex justify-end">
          <Button size="sm" onClick={onClose}>
            I&rsquo;ve saved it
          </Button>
        </div>
      </div>
    </div>
  );
}
