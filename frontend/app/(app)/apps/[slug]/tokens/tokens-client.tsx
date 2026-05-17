"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  CopyIcon,
  KeyRoundIcon,
  PlusIcon,
  RefreshCwIcon,
  ShieldOffIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
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
import { Card, CardContent } from "@/components/ui/card";
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
  CREATE_DEPLOY_TOKEN,
  REVOKE_DEPLOY_TOKEN,
  ROTATE_DEPLOY_TOKEN,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_APP_DEPLOY_TOKENS } from "@/graphql/lifecycle/lifecycle.queries";
import type { MutationResult } from "@/graphql/identity/identity.types";

import { AppTabs } from "../components/app-tabs";

interface DeployToken {
  id: string;
  name: string;
  last4: string;
  scopes: string[];
  expiresAt?: string | null;
  lastUsedAt?: string | null;
  isRevoked: boolean;
  lastRotatedAt?: string | null;
  registeredAppSlug: string;
  createdAt: string;
}

interface DeployTokenSecretReveal {
  token: DeployToken;
  plaintextSecret: string;
}

interface Resp {
  astroliftAppDeployTokens: DeployToken[];
}

export function AppDeployTokensClient({ slug }: { slug: string }) {
  const [createOpen, setCreateOpen] = React.useState(false);
  const [reveal, setReveal] = React.useState<DeployTokenSecretReveal | null>(null);
  const [rotateTarget, setRotateTarget] = React.useState<DeployToken | null>(null);
  const [revokeTarget, setRevokeTarget] = React.useState<DeployToken | null>(null);

  const variables = { appSlug: slug };
  const tokens = useQuery<Resp>(LIST_APP_DEPLOY_TOKENS, {
    variables,
    fetchPolicy: "cache-and-network",
  });

  const refetch = [{ query: LIST_APP_DEPLOY_TOKENS, variables }];

  const [createToken, createState] = useMutation<{
    createDeployToken: MutationResult<DeployTokenSecretReveal>;
  }>(CREATE_DEPLOY_TOKEN, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [rotateToken, rotateState] = useMutation<{
    rotateDeployToken: MutationResult<DeployTokenSecretReveal>;
  }>(ROTATE_DEPLOY_TOKEN, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [revokeToken, revokeState] = useMutation<{
    revokeDeployToken: MutationResult<{ id: string; revoked: boolean }>;
  }>(REVOKE_DEPLOY_TOKEN, { refetchQueries: refetch, awaitRefetchQueries: true });

  const busy = createState.loading || rotateState.loading || revokeState.loading;
  const list = tokens.data?.astroliftAppDeployTokens ?? [];

  async function handleRotate(t: DeployToken) {
    const { data } = await rotateToken({ variables: { input: { id: t.id } } });
    if (data?.rotateDeployToken.ok) {
      const next = data.rotateDeployToken.data;
      if (next) setReveal(next);
    } else {
      throw new Error(data?.rotateDeployToken.errors?.[0]?.message ?? "Rotate failed");
    }
  }

  async function handleRevoke(t: DeployToken) {
    const { data } = await revokeToken({ variables: { input: { id: t.id } } });
    if (data?.revokeDeployToken.ok) {
      toast.success(`Revoked ${t.name}`);
    } else {
      throw new Error(data?.revokeDeployToken.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  return (
    <PageShell
      title="Deploy tokens"
      description={
        <span className="text-muted-foreground font-mono text-xs">
          API tokens that CI runners use to push deployments to {slug}.
          Plaintext is shown once at create/rotate; we store only the hash.
        </span>
      }
      actions={
        <Can permission="app.deploy">
          <Button onClick={() => setCreateOpen(true)}>
            <PlusIcon className="size-4" />
            Create token
          </Button>
        </Can>
      }
    >
      <AppTabs slug={slug} active="secrets" />
      <Card>
        <CardContent className="p-0">
          {tokens.loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<KeyRoundIcon className="size-5" />}
                title="No deploy tokens yet"
                description="Create a token to run deployments from CI. The plaintext is shown once on creation."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Last 4</TableHead>
                  <TableHead>Scopes</TableHead>
                  <TableHead>Last used</TableHead>
                  <TableHead>Expires</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="font-medium">{t.name}</TableCell>
                    <TableCell className="text-muted-foreground font-mono text-xs">
                      …{t.last4}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {t.scopes.map((s) => (
                          <Badge key={s} variant="outline" className="font-mono text-[10px]">
                            {s}
                          </Badge>
                        ))}
                        {t.scopes.length === 0 && (
                          <span className="text-muted-foreground text-xs">all</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {t.lastUsedAt ? new Date(t.lastUsedAt).toLocaleString() : "—"}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {t.expiresAt ? new Date(t.expiresAt).toLocaleDateString() : "never"}
                    </TableCell>
                    <TableCell>
                      {t.isRevoked ? (
                        <Badge variant="destructive">revoked</Badge>
                      ) : (
                        <Badge variant="secondary">active</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {!t.isRevoked && (
                        <Can permission="app.deploy">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setRotateTarget(t)}
                            disabled={busy}
                          >
                            <RefreshCwIcon className="size-3.5" />
                            Rotate
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setRevokeTarget(t)}
                            disabled={busy}
                          >
                            <ShieldOffIcon className="size-3.5" />
                            Revoke
                          </Button>
                        </Can>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <CreateTokenSheet
        open={createOpen}
        onOpenChange={setCreateOpen}
        onSubmit={async (input) => {
          const { data } = await createToken({
            variables: { input: { appSlug: slug, ...input } },
          });
          if (data?.createDeployToken.ok) {
            const next = data.createDeployToken.data;
            if (next) {
              setReveal(next);
              setCreateOpen(false);
            }
            return true;
          }
          toast.error(
            data?.createDeployToken.errors?.[0]?.message ?? "Create failed",
          );
          return false;
        }}
        busy={busy}
      />

      <RevealDialog reveal={reveal} onOpenChange={() => setReveal(null)} />

      <ConfirmDialog
        open={rotateTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRotateTarget(null);
        }}
        title={rotateTarget ? `Rotate ${rotateTarget.name}?` : "Rotate token?"}
        description="A fresh plaintext is generated and shown once. The previous secret remains valid for a 24h grace period to let in-flight CI runs finish — use Revoke for an immediate cutover."
        confirmLabel="Rotate token"
        onConfirm={async () => {
          if (rotateTarget) await handleRotate(rotateTarget);
        }}
      />

      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRevokeTarget(null);
        }}
        title={revokeTarget ? `Revoke ${revokeTarget.name}?` : "Revoke token?"}
        description="CI flows holding this token break immediately. Mint a new token afterwards and update the CI secret. Use Rotate instead for a grace-period swap."
        confirmLabel="Revoke token"
        destructive
        onConfirm={async () => {
          if (revokeTarget) await handleRevoke(revokeTarget);
        }}
      />
    </PageShell>
  );
}

function CreateTokenSheet({
  open,
  onOpenChange,
  onSubmit,
  busy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (input: {
    name: string;
    scopes: string[] | null;
    expiresAtIso: string | null;
  }) => Promise<boolean>;
  busy: boolean;
}) {
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
          <SheetTitle>Create deploy token</SheetTitle>
          <SheetDescription>
            The plaintext secret is shown once on the next screen — copy it
            into your CI secret store immediately.
          </SheetDescription>
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
                ? scopes.split(",").map((s) => s.trim()).filter(Boolean)
                : null,
              expiresAtIso: expiresAt.toISOString(),
            });
          }}
          className="flex flex-1 flex-col gap-4 px-4 pb-4"
        >
          <div className="space-y-2">
            <Label htmlFor="t-name">Name</Label>
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
            <Label htmlFor="t-scopes">Scopes (comma-separated)</Label>
            <Input
              id="t-scopes"
              value={scopes}
              onChange={(e) => setScopes(e.target.value)}
              placeholder="deploy,rollback"
              spellCheck={false}
              className="font-mono"
            />
            <p className="text-muted-foreground text-xs">
              Leave blank for all scopes.
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="t-expires">Expires in (days)</Label>
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
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy ? "Creating…" : "Create token"}
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
  return (
    <AlertDialog open={reveal !== null} onOpenChange={(o) => !o && onOpenChange()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Token created — copy now
          </AlertDialogTitle>
          <AlertDialogDescription>
            This is the only time the plaintext secret will be shown. Paste
            it into your CI secret store immediately.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {reveal && (
          <div className="space-y-3 py-2">
            <div className="text-muted-foreground text-xs">
              <span className="font-medium">{reveal.token.name}</span> · last 4{" "}
              <code className="bg-muted rounded px-1">{reveal.token.last4}</code>
            </div>
            <div className="bg-muted relative rounded p-3 font-mono text-xs break-all">
              {reveal.plaintextSecret}
              <Button
                size="sm"
                variant="ghost"
                className="absolute right-1 top-1 h-7"
                onClick={() => {
                  navigator.clipboard.writeText(reveal.plaintextSecret);
                  toast.success("Copied to clipboard");
                }}
              >
                <CopyIcon className="size-3" />
                Copy
              </Button>
            </div>
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogAction onClick={onOpenChange}>Done</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
