"use client";

import { useMutation, useQuery } from "@apollo/client/react";
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

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  CREATE_DEPLOY_TOKEN,
  REVOKE_DEPLOY_TOKEN,
  ROTATE_DEPLOY_TOKEN,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_APP_DEPLOY_TOKENS } from "@/graphql/lifecycle/lifecycle.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { MutationResult } from "@/graphql/identity/identity.types";

interface DeployToken {
  id: string;
  name: string;
  last4: string;
  scopes: string[];
  isRevoked: boolean;
  lastUsedAt: string | null;
  lastRotatedAt: string | null;
  createdAt: string;
}

interface DeployTokenSecretReveal {
  token: DeployToken;
  plaintextSecret: string;
}

interface ListResp {
  astroliftAppDeployTokens: DeployToken[];
}

interface CreateResp {
  createDeployToken: MutationResult<DeployTokenSecretReveal>;
}

interface RotateResp {
  rotateDeployToken: MutationResult<DeployTokenSecretReveal>;
}

interface RevokeResp {
  revokeDeployToken: MutationResult<{ id: string; revoked: boolean }>;
}

interface Props {
  appSlug: string;
}

/**
 * Manage the app's deploy token — generate, rotate, revoke. The plaintext
 * is shown exactly once; subsequent visits only see the last-4 digits.
 * Gated on `app.update` because token lifecycle controls CI access.
 */
export function DeployTokenControl({ appSlug }: Props) {
  const { data, loading, refetch } = useQuery<ListResp>(LIST_APP_DEPLOY_TOKENS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const tokens = data?.astroliftAppDeployTokens ?? [];
  const active = tokens.find((t) => !t.isRevoked) ?? null;
  const [reveal, setReveal] = useState<string | null>(null);
  const [confirmRotate, setConfirmRotate] = useState(false);
  const [confirmRevoke, setConfirmRevoke] = useState(false);

  const refetchAll = async () => {
    await refetch();
  };

  const [create, { loading: creating }] = useMutation<CreateResp>(CREATE_DEPLOY_TOKEN, {
    onCompleted: refetchAll,
  });
  const [rotate, { loading: rotating }] = useMutation<RotateResp>(ROTATE_DEPLOY_TOKEN, {
    onCompleted: refetchAll,
  });
  const [revoke, { loading: revoking }] = useMutation<RevokeResp>(REVOKE_DEPLOY_TOKEN, {
    refetchQueries: [
      { query: LIST_APP_DEPLOY_TOKENS, variables: { appSlug } },
      { query: GET_APP, variables: { slug: appSlug } },
    ],
    awaitRefetchQueries: true,
  });

  async function handleCreate() {
    const { data } = await create({
      variables: {
        input: { appSlug, name: "default", scopes: ["deploy"] },
      },
    });
    if (data?.createDeployToken.ok && data.createDeployToken.data) {
      setReveal(data.createDeployToken.data.plaintextSecret);
      toast.success("Deploy token minted.");
    } else {
      toast.error(data?.createDeployToken.errors?.[0]?.message ?? "Create failed.");
    }
  }

  async function handleRotate() {
    if (!active) return;
    const { data } = await rotate({
      variables: { input: { id: active.id } },
    });
    if (data?.rotateDeployToken.ok && data.rotateDeployToken.data) {
      setReveal(data.rotateDeployToken.data.plaintextSecret);
      toast.success("Token rotated — copy the new value now.");
    } else {
      throw new Error(data?.rotateDeployToken.errors?.[0]?.message ?? "Rotate failed.");
    }
  }

  async function handleRevoke() {
    if (!active) return;
    const { data } = await revoke({
      variables: { input: { id: active.id } },
    });
    if (data?.revokeDeployToken.ok) {
      toast.success("Token revoked.");
    } else {
      throw new Error(data?.revokeDeployToken.errors?.[0]?.message ?? "Revoke failed.");
    }
  }

  const missingToken = !loading && !active;

  return (
    <section className={`rounded-lg border p-5${missingToken ? " border-amber-400/60 bg-amber-500/5 dark:border-amber-500/40" : ""}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          {missingToken ? (
            <AlertTriangleIcon className="mt-0.5 size-4 text-amber-600 dark:text-amber-400" />
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
              <p className="mt-2 text-xs font-medium text-amber-700 dark:text-amber-400">
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
                onClick={handleCreate}
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

      {reveal && <RevealDialog token={reveal} onClose={() => setReveal(null)} />}

      <ConfirmDialog
        open={confirmRotate}
        onOpenChange={setConfirmRotate}
        title="Rotate deploy token?"
        description="The previous value stops working immediately. Any CI run still holding the old token will fail until you update the secret on the CI side."
        confirmLabel="Rotate token"
        onConfirm={handleRotate}
      />

      <ConfirmDialog
        open={confirmRevoke}
        onOpenChange={setConfirmRevoke}
        title="Revoke deploy token?"
        description="CI runs using this token will start failing immediately. Mint a new token afterwards and update your CI secret to recover. No way to un-revoke — soft delete only."
        confirmLabel="Revoke token"
        destructive
        onConfirm={handleRevoke}
      />
    </section>
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
        <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
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
