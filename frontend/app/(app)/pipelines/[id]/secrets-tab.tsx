"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  KeyIcon,
  Loader2Icon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  DELETE_PIPELINE_SECRET,
  SET_PIPELINE_SECRET,
} from "@/graphql/pipelines/pipelines.mutations";
import { LIST_PIPELINE_SECRETS } from "@/graphql/pipelines/pipelines.queries";
import type { PipelineSecret } from "@/graphql/pipelines/pipelines.types";

interface SecretsResp {
  astroliftPipelineSecrets: PipelineSecret[];
}

interface MutationResult<T> {
  ok: boolean;
  errors: { code: string; message: string; field?: string | null }[];
  data: T | null;
}

// ---------------------------------------------------------------------------
// AddSecretForm — inline form for adding a new secret value.
// Value field is type=password, write-only by design.
// ---------------------------------------------------------------------------

function AddSecretForm({
  pipelineId,
  onDone,
}: {
  pipelineId: string;
  onDone: () => void;
}) {
  const [name, setName] = React.useState("");
  const [value, setValue] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  const refetch = [
    { query: LIST_PIPELINE_SECRETS, variables: { pipelineId } },
  ];

  const [setSecret] = useMutation<{
    setPipelineSecret: MutationResult<{ pipelineId: string; name: string }>;
  }>(SET_PIPELINE_SECRET, { refetchQueries: refetch, awaitRefetchQueries: true });

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !value) return;
    setSaving(true);
    try {
      const { data } = await setSecret({
        variables: {
          input: { pipelineId, name: name.trim(), value },
        },
      });
      if (data?.setPipelineSecret.ok) {
        toast.success(`Secret "${name.trim()}" saved`);
        onDone();
      } else {
        const err = data?.setPipelineSecret.errors[0];
        toast.error(err?.message ?? "Failed to save secret");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="bg-muted/30 flex flex-col gap-3 rounded-md border p-4"
    >
      <p className="text-muted-foreground text-xs">
        Secret values are write-only. Once saved, the value cannot be retrieved.
      </p>
      <div className="flex gap-3">
        <div className="flex-1 space-y-1">
          <Label htmlFor="secret-name" className="text-xs">
            Name
          </Label>
          <Input
            id="secret-name"
            value={name}
            onChange={(e) =>
              setName(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))
            }
            placeholder="API_KEY"
            required
            spellCheck={false}
            className="font-mono"
            disabled={saving}
            autoFocus
          />
        </div>
        <div className="flex-1 space-y-1">
          <Label htmlFor="secret-value" className="text-xs">
            Value
          </Label>
          <Input
            id="secret-value"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            type="password"
            required
            spellCheck={false}
            className="font-mono"
            disabled={saving}
            autoComplete="new-password"
          />
        </div>
      </div>
      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onDone}
          disabled={saving}
        >
          Cancel
        </Button>
        <Button
          type="submit"
          size="sm"
          disabled={saving || !name.trim() || !value}
        >
          {saving ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : (
            "Save secret"
          )}
        </Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// PipelineSecretsTab — the main export
// ---------------------------------------------------------------------------

export function PipelineSecretsTab({ pipelineId }: { pipelineId: string }) {
  const [addOpen, setAddOpen] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState<PipelineSecret | null>(null);

  const { data, loading } = useQuery<SecretsResp>(LIST_PIPELINE_SECRETS, {
    variables: { pipelineId },
    fetchPolicy: "cache-and-network",
  });

  const refetch = [
    { query: LIST_PIPELINE_SECRETS, variables: { pipelineId } },
  ];

  const [deleteSecret, deleteState] = useMutation<{
    deletePipelineSecret: MutationResult<{ pipelineId: string; name: string }>;
  }>(DELETE_PIPELINE_SECRET, { refetchQueries: refetch, awaitRefetchQueries: true });

  const secrets = data?.astroliftPipelineSecrets ?? [];

  async function handleDelete(secret: PipelineSecret) {
    const { data: resp } = await deleteSecret({
      variables: { input: { pipelineId, name: secret.name } },
    });
    if (resp?.deletePipelineSecret.ok) {
      toast.success(`Deleted "${secret.name}"`);
    } else {
      throw new Error(
        resp?.deletePipelineSecret.errors[0]?.message ?? "Delete failed"
      );
    }
  }

  return (
    <div className="space-y-4">
      {/* Write-only notice banner */}
      <div className="bg-amber-500/5 border-amber-500/20 flex items-start gap-3 rounded-md border p-3">
        <AlertTriangleIcon className="text-amber-500 mt-0.5 size-4 shrink-0" />
        <div className="text-xs">
          <p className="font-medium">Secret values are write-only.</p>
          <p className="text-muted-foreground mt-0.5">
            Once saved, values cannot be retrieved. Reference secrets in your
            pipeline TOML using{" "}
            <code className="bg-muted rounded px-1 font-mono">
              {"${secrets.NAME}"}
            </code>
            . Never paste secret values directly into the TOML.
          </p>
        </div>
      </div>

      {/* Add secret form (toggled) */}
      {addOpen && (
        <AddSecretForm
          pipelineId={pipelineId}
          onDone={() => setAddOpen(false)}
        />
      )}

      <Card>
        <CardContent className="p-0">
          {loading && secrets.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : secrets.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<KeyIcon className="size-5" />}
                title="No secrets"
                description="Add a secret to make it available in this pipeline via ${secrets.NAME}."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Value</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead className="w-12 text-right" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {secrets.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="font-mono text-xs">{s.name}</TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="font-mono text-[10px]">
                        Value set
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {new Date(s.createdAt).toLocaleString()}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {new Date(s.updatedAt).toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right">
                      <Can permission="pipeline.secret.write">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-8"
                          onClick={() => setDeleteTarget(s)}
                          disabled={deleteState.loading}
                        >
                          <Trash2Icon className="size-4" />
                          <span className="sr-only">Delete {s.name}</span>
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

      {/* "Add secret" action — only when the form is not already open */}
      {!addOpen && (
        <Can permission="pipeline.secret.write">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setAddOpen(true)}
          >
            <PlusIcon className="size-4" />
            Add secret
          </Button>
        </Can>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={deleteTarget ? `Delete "${deleteTarget.name}"?` : "Delete secret?"}
        description="This will permanently remove the secret. Any pipeline jobs that reference it will fail until you set a replacement value."
        confirmLabel="Delete"
        destructive
        onConfirm={async () => {
          if (deleteTarget) await handleDelete(deleteTarget);
        }}
      />
    </div>
  );
}
