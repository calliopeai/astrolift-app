"use client";

import { useMutation } from "@apollo/client/react";
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
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import { DataTable, useCursorTable, type Column } from "@/components/data-table";
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
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { CREATE_API_TOKEN, REVOKE_API_TOKEN } from "@/graphql/identity/identity.mutations";
import { LIST_API_TOKENS_PAGE } from "@/graphql/identity/identity.queries";
import type {
  AstroliftApiToken,
  AstroliftApiTokenPlaintext,
  MutationResult,
} from "@/graphql/identity/identity.types";

interface Resp {
  astroliftApiTokensPage: {
    items: AstroliftApiToken[];
    nextCursor?: string | null;
    totalCount?: number | null;
  };
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
    value: "agent-env-spec:write",
    label: "Write agent environments",
    hint: "Create, update, and delete agent environment specs without broader app writes.",
  },
  {
    value: "project:write",
    label: "Write project resources",
    hint: "Provision and manage project-owned shared resources without broader app writes.",
  },
  {
    value: "secret:read",
    label: "Reveal secrets",
    hint: "Reveal stored secret values. Sensitive; grant only when required.",
  },
  {
    value: "secret:write",
    label: "Write secrets",
    hint: "Set, rotate, and delete stored secret values without revealing them.",
  },
  {
    value: "mcp:read",
    label: "MCP read",
    hint: "List agent packages and inspect run status through remote MCP.",
  },
  {
    value: "mcp:dispatch",
    label: "MCP dispatch",
    hint: "Run and hard-stop agents through remote MCP.",
  },
  {
    value: "mcp:write",
    label: "MCP write",
    hint: "Sync agent repositories and package definitions through remote MCP.",
  },
  {
    value: "workflow:write",
    label: "Write workflows",
    hint: "Create, update, and delete workflows without broader app writes.",
  },
  {
    value: "workflow:trigger",
    label: "Run workflows",
    hint: "Start workflow runs without granting workflow configuration writes.",
  },
  {
    value: "admin",
    label: "Admin",
    hint: "Full power — implies every other scope. Use sparingly.",
  },
] as const;

const DEFAULT_SELECTED_SCOPES: string[] = ["read:apps", "read:clusters", "mcp:read"];

export function TokensClient() {
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [expiresInDays, setExpiresInDays] = React.useState("90");
  const [selectedScopes, setSelectedScopes] = React.useState<string[]>(DEFAULT_SELECTED_SCOPES);
  const [createdToken, setCreatedToken] = React.useState<AstroliftApiTokenPlaintext | null>(null);
  const [revokeTarget, setRevokeTarget] = React.useState<AstroliftApiToken | null>(null);
  const [mcpEndpoint, setMcpEndpoint] = React.useState("/api/mcp/v1/");

  React.useEffect(() => {
    setMcpEndpoint(`${window.location.origin}/api/mcp/v1/`);
  }, []);

  const table = useCursorTable<AstroliftApiToken>({
    query: LIST_API_TOKENS_PAGE,
    extract: (d) => (d as Resp | undefined)?.astroliftApiTokensPage,
    searchVariable: "search",
    urlKey: "tok",
  });

  // Refetched by operation name, not by document: the walk's cursor, page
  // size and search term live in the controller, so only the active query
  // knows the variables of the page the operator is looking at.
  const refetchPage = ["ListApiTokensPage"];

  const [createToken, { loading: creating }] = useMutation<{
    createApiToken: MutationResult<AstroliftApiTokenPlaintext>;
  }>(CREATE_API_TOKEN, {
    refetchQueries: refetchPage,
    awaitRefetchQueries: true,
  });

  const [revokeToken, { loading: revoking }] = useMutation<{
    revokeApiToken: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_API_TOKEN, {
    refetchQueries: refetchPage,
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

  function copyMcpEndpoint() {
    navigator.clipboard.writeText(mcpEndpoint);
    toast.success("MCP endpoint copied");
  }

  const columns: Column<AstroliftApiToken>[] = [
    {
      id: "name",
      header: "Name",
      cellClassName: "font-medium",
      cell: (t) => t.name,
    },
    {
      id: "suffix",
      header: "Suffix",
      width: "w-28",
      cellClassName: "font-mono text-xs",
      cell: (t) => `…${t.tokenLast4}`,
    },
    {
      id: "scopes",
      header: "Scopes",
      cell: (t) => (
        <div className="flex flex-wrap gap-1">
          {t.scopes.length === 0 ? (
            <span className="text-muted-foreground text-xs">none</span>
          ) : (
            t.scopes.map((s) => (
              <Badge key={s} variant="outline" className="text-2xs font-mono">
                {s}
              </Badge>
            ))
          )}
        </div>
      ),
    },
    {
      id: "lastUsed",
      header: "Last used",
      cellClassName: "text-muted-foreground text-xs",
      cell: (t) => <LastUsedCell token={t} />,
    },
    {
      id: "created",
      header: "Created",
      width: "w-28",
      cellClassName: "text-muted-foreground text-sm",
      cell: (t) => new Date(t.createdAt).toLocaleDateString(),
    },
    {
      id: "expires",
      header: "Expires",
      width: "w-28",
      cellClassName: "text-muted-foreground text-sm",
      cell: (t) => (t.expiresAt ? new Date(t.expiresAt).toLocaleDateString() : "never"),
    },
    {
      id: "status",
      header: "Status",
      width: "w-28",
      cell: (t) =>
        t.isRevoked ? (
          <Badge variant="destructive" className="gap-1">
            <AlertTriangleIcon className="size-3" />
            revoked
          </Badge>
        ) : (
          <Badge variant="secondary">active</Badge>
        ),
    },
    {
      id: "actions",
      header: "Actions",
      align: "right",
      width: "w-24",
      // The row is a stretched link (`rowHref`), whose ::after covers the
      // whole row — the button has to sit above it to stay clickable.
      cell: (t) => (
        <div className="relative z-10 flex justify-end">
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
        </div>
      ),
    },
  ];

  return (
    <TooltipProvider>
      <PageShell
        title="API keys"
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
        <Card>
          <CardContent className="space-y-3 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="space-y-1">
                <div className="flex items-center gap-2 font-medium">
                  <CableIcon className="size-4" /> Remote agent MCP
                </div>
                <p className="text-muted-foreground max-w-3xl text-xs">
                  Connect CI or coding clients over authenticated Streamable HTTP. Send an API token
                  as <code>Authorization: Bearer alft_at_…</code>. MCP scopes still require the
                  token owner&apos;s matching agent permissions; secret values are not exposed.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <code className="bg-muted rounded px-2 py-1.5 font-mono text-xs">
                  {mcpEndpoint}
                </code>
                <Button size="sm" variant="outline" onClick={copyMcpEndpoint}>
                  <CopyIcon className="size-4" /> Copy URL
                </Button>
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <Badge variant="outline">mcp:read · inspect packages/tasks</Badge>
              <Badge variant="outline">mcp:dispatch · run/kill</Badge>
              <Badge variant="outline">mcp:write · sync repos</Badge>
            </div>
          </CardContent>
        </Card>

        {createdToken && (
          <Card className="border-success-border bg-success/5">
            <CardContent className="flex flex-col gap-3 p-4">
              <div className="flex items-center gap-2">
                <CheckCircle2Icon className="text-success-fg size-4" />
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

        <DataTable
          label="API keys"
          controller={table}
          columns={columns}
          getRowId={(t) => t.id}
          rowHref={(t) => `/tokens/${t.id}`}
          searchPlaceholder="Search tokens…"
          empty={{
            icon: <KeyIcon className="size-5" />,
            title: "No API keys",
            description:
              "Create one to authenticate the CLI, CI runs, or your own scripts against the platform.",
          }}
          emptyFiltered={{
            title: "No matching API keys",
            description:
              "No token matches that name, owner, team, or last-4 suffix. Clear the search to see every key.",
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
                  Token may only exercise these scopes. <code>admin</code> is the full-power
                  wildcard and is never selected by default.
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
