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
import { EmptyState } from "@/components/EmptyState";
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
import {
  CREATE_API_TOKEN,
  REVOKE_API_TOKEN,
} from "@/graphql/identity/identity.mutations";
import { LIST_API_TOKENS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftApiToken,
  AstroliftApiTokenPlaintext,
  MutationResult,
} from "@/graphql/identity/identity.types";

interface Resp {
  astroliftApiTokens: AstroliftApiToken[];
}

export function TokensClient() {
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [expiresInDays, setExpiresInDays] = React.useState("90");
  const [createdToken, setCreatedToken] =
    React.useState<AstroliftApiTokenPlaintext | null>(null);

  const tokens = useQuery<Resp>(LIST_API_TOKENS);

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

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const { data } = await createToken({
      variables: {
        input: {
          name: name.trim(),
          expiresInDays: Number(expiresInDays) || null,
        },
      },
    });
    if (data?.createApiToken.ok && data.createApiToken.data) {
      setCreatedToken(data.createApiToken.data);
      setName("");
      setOpen(false);
    } else {
      toast.error(data?.createApiToken.errors?.[0]?.message ?? "Create failed");
    }
  }

  async function handleRevoke(t: AstroliftApiToken) {
    if (!confirm(`Revoke token ${t.name}? Existing CLIs/bots using it stop working immediately.`)) {
      return;
    }
    const { data } = await revokeToken({ variables: { input: { id: t.id } } });
    if (data?.revokeApiToken.ok) {
      toast.success("Revoked");
    } else {
      toast.error(data?.revokeApiToken.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  function copyPlaintext() {
    if (!createdToken) return;
    navigator.clipboard.writeText(createdToken.plaintext);
    toast.success("Copied to clipboard");
  }

  const list = tokens.data?.astroliftApiTokens ?? [];

  return (
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
              <CheckCircle2Icon className="text-emerald-600 size-4" />
              <p className="text-sm font-medium">
                Token <span className="font-mono">{createdToken.apiToken.name}</span> created
              </p>
            </div>
            <p className="text-muted-foreground text-xs">
              Copy the value below now — it&apos;s never shown again. We store
              only the SHA-256 hash and the last 4 characters.
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
          {tokens.loading ? (
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
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Suffix</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Expires</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="font-medium">{t.name}</TableCell>
                    <TableCell className="font-mono text-xs">
                      …{t.tokenLast4}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {new Date(t.createdAt).toLocaleDateString()}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {t.expiresAt
                        ? new Date(t.expiresAt).toLocaleDateString()
                        : "never"}
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
                          onClick={() => handleRevoke(t)}
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
          )}
        </CardContent>
      </Card>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="flex flex-col">
          <SheetHeader>
            <SheetTitle>New API token</SheetTitle>
            <SheetDescription>
              The plaintext is shown exactly once after creation. Save it
              somewhere secure — there&apos;s no way to retrieve it later.
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
                Descriptive — appears in the audit log next to every action this
                token takes.
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
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={creating || !name}>
                {creating ? "Creating…" : "Create token"}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>
    </PageShell>
  );
}
