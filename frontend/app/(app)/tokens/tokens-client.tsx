"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  CopyIcon,
  KeyIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { PageShell } from "@/components/PageShell";
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
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useListControls } from "@/hooks/use-list-controls";
import { CREATE_API_TOKEN, REVOKE_API_TOKEN } from "@/graphql/identity/identity.mutations";
import { LIST_API_TOKENS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftApiToken,
  AstroliftApiTokenPlaintext,
  MutationResult,
} from "@/graphql/identity/identity.types";

interface Resp {
  astroliftApiTokens: AstroliftApiToken[];
}

// Mirror of backend ALLOWED_SCOPES (astrolift_identity/api_tokens.py).
// Keep in sync — server validates and rejects unknowns; clients picking
// up new scopes need both halves landed.
const SCOPE_CHOICES = [
  {
    value: "read:apps",
    label: "Read apps",
    hint: "List apps, deployments, environments, secrets metadata.",
  },
  {
    value: "write:apps",
    label: "Write apps",
    hint: "Trigger deploys, edit env vars, manage app config.",
  },
  {
    value: "read:clusters",
    label: "Read clusters",
    hint: "List clusters, view kubeconfig metadata, read provider state.",
  },
  {
    value: "admin",
    label: "Admin",
    hint: "Full power — implies every other scope. Use sparingly.",
  },
] as const;

const DEFAULT_SELECTED_SCOPES: string[] = SCOPE_CHOICES.map((s) => s.value);

export function TokensClient() {
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [expiresInDays, setExpiresInDays] = React.useState("90");
  const [selectedScopes, setSelectedScopes] = React.useState<string[]>(DEFAULT_SELECTED_SCOPES);
  const [createdToken, setCreatedToken] = React.useState<AstroliftApiTokenPlaintext | null>(null);
  const [revokeTarget, setRevokeTarget] = React.useState<AstroliftApiToken | null>(null);

  const tokens = useQuery<Resp>(LIST_API_TOKENS, { fetchPolicy: "cache-and-network" });

  const [createToken, { loading: creating }] = useMutation<{
    createApiToken: MutationResult<AstroliftApiTokenPlaintext>;
  }>(CREATE_API_TOKEN, {
    refetchQueries: [{ query: LIST_API_TOKENS }],
    awaitRefetchQueries: true,
  });

  const [revokeToken, { loading: revoking }] = useMutation<{
    revokeApiToken: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_API_TOKEN, {
    refetchQueries: [{ query: LIST_API_TOKENS }],
    awaitRefetchQueries: true,
  });

  function resetForm() {
    setName("");
    setExpiresInDays("90");
    setSelectedScopes(DEFAULT_SELECTED_SCOPES);
  }

  function toggleScope(value: string) {
    setSelectedScopes((prev) =>
      prev.includes(value) ? prev.filter((s) => s !== value) : [...prev, value]
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (selectedScopes.length === 0) {
      toast.error("Pick at least one scope");
      return;
    }
    const { data } = await createToken({
      variables: {
        input: {
          name: name.trim(),
          expiresInDays: Number(expiresInDays) || null,
          scopes: selectedScopes,
        },
      },
    });
    if (data?.createApiToken.ok && data.createApiToken.data) {
      setCreatedToken(data.createApiToken.data);
      resetForm();
      setOpen(false);
    } else {
      toast.error(data?.createApiToken.errors?.[0]?.message ?? "Create failed");
    }
  }

  async function handleRevoke(t: AstroliftApiToken) {
    const { data } = await revokeToken({ variables: { input: { id: t.id } } });
    if (data?.revokeApiToken.ok) {
      toast.success("Revoked");
    } else {
      throw new Error(data?.revokeApiToken.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  function copyPlaintext() {
    if (!createdToken) return;
    navigator.clipboard.writeText(createdToken.plaintext);
    toast.success("Copied to clipboard");
  }

  const list = tokens.data?.astroliftApiTokens ?? [];

  const ctrl = useListControls({
    data: list,
    searchFn: (t) => [t.name, t.tokenLast4, ...t.scopes].join(" "),
    initialPageSize: 25,
    initialSort: { key: "createdAt", dir: "desc" },
    sortFn: (a, b, sort) => {
      if (sort.key === "name") {
        const cmp = a.name.localeCompare(b.name);
        return sort.dir === "asc" ? cmp : -cmp;
      }
      if (sort.key === "createdAt") {
        const cmp = new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime();
        return sort.dir === "asc" ? cmp : -cmp;
      }
      if (sort.key === "lastUsedAt") {
        const at = a.lastUsedAt ? new Date(a.lastUsedAt).getTime() : 0;
        const bt = b.lastUsedAt ? new Date(b.lastUsedAt).getTime() : 0;
        const cmp = at - bt;
        return sort.dir === "asc" ? cmp : -cmp;
      }
      return 0;
    },
  });

  return (
    <TooltipProvider>
      <PageShell
        title="API tokens"
        description="Long-lived bearer credentials for CLIs, bots, and scripts. Tokens are hashed at rest — the plaintext is shown exactly once at creation."
        actions={
          <Can permission="api_token.create">
            <Button onClick={() => setOpen(true)}>
              <PlusIcon className="size-4" />
              New token
            </Button>
          </Can>
        }
      >
        {createdToken && (
          <Card className="border-emerald-500/30 bg-emerald-500/5">
            <CardContent className="flex flex-col gap-3 p-4">
              <div className="flex items-center gap-2">
                <CheckCircle2Icon className="size-4 text-emerald-600" />
                <p className="text-sm font-medium">
                  Token <span className="font-mono">{createdToken.apiToken.name}</span> created
                </p>
              </div>
              <p className="text-muted-foreground text-xs">
                Copy the value below now — it&apos;s never shown again. We store only the SHA-256
                hash and the last 4 characters.
              </p>
              <div className="flex items-center gap-2">
                <code className="bg-background flex-1 rounded-md border px-3 py-2 font-mono text-xs break-all">
                  {createdToken.plaintext}
                </code>
                <Button size="sm" variant="outline" onClick={copyPlaintext}>
                  <CopyIcon className="size-4" />
                  Copy
                </Button>
              </div>
              <div className="flex justify-end">
                <Button size="sm" variant="ghost" onClick={() => setCreatedToken(null)}>
                  I&apos;ve saved it — dismiss
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

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
                  icon={<KeyIcon className="size-5" />}
                  title="No API tokens"
                  description="Create one to authenticate the CLI, CI runs, or your own scripts against the platform."
                />
              </div>
            ) : (
              <>
                <div className="border-b px-4 py-2">
                  <ListControls controls={ctrl} searchPlaceholder="Search tokens…" />
                </div>
                <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>
                      <SortableHeader sortKey="name" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                        Name
                      </SortableHeader>
                    </TableHead>
                    <TableHead>Suffix</TableHead>
                    <TableHead>Scopes</TableHead>
                    <TableHead>
                      <SortableHeader sortKey="lastUsedAt" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                        Last used
                      </SortableHeader>
                    </TableHead>
                    <TableHead>
                      <SortableHeader sortKey="createdAt" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                        Created
                      </SortableHeader>
                    </TableHead>
                    <TableHead>Expires</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {ctrl.rows.map((t) => (
                    <TableRow key={t.id}>
                      <TableCell className="font-medium">{t.name}</TableCell>
                      <TableCell className="font-mono text-xs">…{t.tokenLast4}</TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {t.scopes.length === 0 ? (
                            <span className="text-muted-foreground text-xs">none</span>
                          ) : (
                            t.scopes.map((s) => (
                              <Badge key={s} variant="outline" className="font-mono text-[10px]">
                                {s}
                              </Badge>
                            ))
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-xs">
                        <LastUsedCell token={t} />
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {new Date(t.createdAt).toLocaleDateString()}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {t.expiresAt ? new Date(t.expiresAt).toLocaleDateString() : "never"}
                      </TableCell>
                      <TableCell>
                        {t.isRevoked ? (
                          <Badge variant="destructive" className="gap-1">
                            <AlertTriangleIcon className="size-3" />
                            revoked
                          </Badge>
                        ) : (
                          <Badge variant="secondary">active</Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <Can permission="api_token.revoke">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setRevokeTarget(t)}
                            disabled={revoking || t.isRevoked}
                          >
                            <Trash2Icon className="size-4" />
                            <span className="sr-only">Revoke</span>
                          </Button>
                        </Can>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              </>
            )}
          </CardContent>
        </Card>

        <Sheet
          open={open}
          onOpenChange={(next) => {
            setOpen(next);
            if (!next) resetForm();
          }}
        >
          <SheetContent className="flex flex-col">
            <SheetHeader>
              <SheetTitle>New API token</SheetTitle>
              <SheetDescription>
                The plaintext is shown exactly once after creation. Save it somewhere secure —
                there&apos;s no way to retrieve it later.
              </SheetDescription>
            </SheetHeader>
            <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
              <div className="space-y-2">
                <Label htmlFor="token-name">Name</Label>
                <Input
                  id="token-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="ci-runner-prod"
                  autoFocus
                  required
                />
                <p className="text-muted-foreground text-xs">
                  Descriptive — appears in the audit log next to every action this token takes.
                </p>
              </div>
              <div className="space-y-2">
                <Label>Scopes</Label>
                <div className="flex flex-wrap gap-2">
                  {SCOPE_CHOICES.map((scope) => {
                    const active = selectedScopes.includes(scope.value);
                    return (
                      <Tooltip key={scope.value}>
                        <TooltipTrigger asChild>
                          <button
                            type="button"
                            onClick={() => toggleScope(scope.value)}
                            aria-pressed={active}
                            className={cn(
                              "rounded-md border px-2.5 py-1 font-mono text-xs transition",
                              active
                                ? "border-primary bg-primary/10 text-primary"
                                : "border-input text-muted-foreground hover:border-foreground/30 hover:text-foreground"
                            )}
                          >
                            {scope.value}
                          </button>
                        </TooltipTrigger>
                        <TooltipContent>
                          <p className="font-medium">{scope.label}</p>
                          <p className="opacity-75">{scope.hint}</p>
                        </TooltipContent>
                      </Tooltip>
                    );
                  })}
                </div>
                <p className="text-muted-foreground text-xs">
                  Token may only exercise these scopes. ``admin`` is the full-power wildcard.
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="token-expires">Expires in (days)</Label>
                <Input
                  id="token-expires"
                  type="number"
                  min={1}
                  max={365}
                  value={expiresInDays}
                  onChange={(e) => setExpiresInDays(e.target.value)}
                />
                <p className="text-muted-foreground text-xs">
                  Leave 0 or empty for no expiry. Default 90 days, max 365.
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
                  Cancel
                </Button>
                <Button type="submit" disabled={creating || !name || selectedScopes.length === 0}>
                  {creating ? "Creating…" : "Create token"}
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
          title={revokeTarget ? `Revoke token ${revokeTarget.name}?` : "Revoke token?"}
          description="Existing CLIs and bots using this token stop working immediately. There's no way to un-revoke — mint a new token if you need to restore access."
          confirmLabel="Revoke token"
          destructive
          onConfirm={async () => {
            if (revokeTarget) await handleRevoke(revokeTarget);
          }}
        />
      </PageShell>
    </TooltipProvider>
  );
}

function LastUsedCell({ token }: { token: AstroliftApiToken }) {
  if (!token.lastUsedAt) {
    return <span className="text-muted-foreground">—</span>;
  }
  const ts = new Date(token.lastUsedAt).toLocaleString();
  const hasMeta = Boolean(token.lastUsedIp || token.lastUsedAgent);
  if (!hasMeta) {
    return <span>{ts}</span>;
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-help underline decoration-dotted underline-offset-2">{ts}</span>
      </TooltipTrigger>
      <TooltipContent className="max-w-sm">
        <p className="font-mono text-[10px]">IP: {token.lastUsedIp ?? "—"}</p>
        {token.lastUsedAgent && (
          <p className="font-mono text-[10px] break-all opacity-75">UA: {token.lastUsedAgent}</p>
        )}
      </TooltipContent>
    </Tooltip>
  );
}
