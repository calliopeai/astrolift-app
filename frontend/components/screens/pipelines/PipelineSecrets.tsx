"use client";

import { AlertTriangleIcon, KeyIcon, Loader2Icon, PlusIcon, Trash2Icon } from "lucide-react";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { selectRows } from "@/components/list/select-rows";
import type { ListStateController } from "@/components/list/list-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { PipelineSecret } from "@/graphql/pipelines/pipelines.types";

import { PIPELINE_SECRETS_SELECT } from "./pipelines-list";
import type { usePipelineSecrets } from "./use-pipeline-secrets";

// ---------------------------------------------------------------------------
// AddSecretForm — inline form for adding a new secret value.
// Value field is type=password, write-only by design.
// ---------------------------------------------------------------------------

function AddSecretForm({
  onSave,
  onDone,
}: {
  onSave: (name: string, value: string) => Promise<boolean>;
  onDone: () => void;
}) {
  const [name, setName] = React.useState("");
  const [value, setValue] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !value) return;
    setSaving(true);
    try {
      if (await onSave(name.trim(), value)) onDone();
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="bg-muted/30 flex flex-col gap-3 rounded-md border p-4">
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
            onChange={(e) => setName(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))}
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
        <Button type="button" variant="outline" size="sm" onClick={onDone} disabled={saving}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={saving || !name.trim() || !value}>
          {saving ? <Loader2Icon className="size-4 animate-spin" /> : "Save secret"}
        </Button>
      </div>
    </form>
  );
}

export type PipelineSecretsViewProps = Omit<ReturnType<typeof usePipelineSecrets>, "list">;

/**
 * A pipeline's Secrets tab (#100): write-only notice, the secrets as the
 * tab's one embedded list (search, sort, numbered pages, run over the names
 * in hand), add and delete. Pure.
 */
export function PipelineSecretsView({
  list,
  secrets,
  loading,
  error,
  onRetry,
  deleting,
  saveSecret,
  deleteSecret,
}: PipelineSecretsViewProps & { list: ListStateController }) {
  const [addOpen, setAddOpen] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState<PipelineSecret | null>(null);
  const { state } = list;
  const page = selectRows(
    secrets,
    {
      filters: list.filters,
      q: state.q,
      sort: state.sort,
      page: state.page,
      pageSize: state.pageSize,
    },
    PIPELINE_SECRETS_SELECT
  );

  const columns: Column<PipelineSecret>[] = [
    {
      id: "name",
      header: "Name",
      sortKey: "name",
      cellClassName: "max-w-80",
      cell: (s) => (
        <span className="block truncate font-mono text-xs" title={s.name}>
          {s.name}
        </span>
      ),
    },
    {
      id: "value",
      header: "Value",
      cell: () => (
        <Badge variant="secondary" className="text-2xs font-mono">
          Value set
        </Badge>
      ),
    },
    {
      id: "created",
      header: "Created",
      sortKey: "created",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (s) => new Date(s.createdAt).toLocaleString(),
    },
    {
      id: "updated",
      header: "Updated",
      sortKey: "updated",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (s) => new Date(s.updatedAt).toLocaleString(),
    },
  ];

  return (
    <div className="space-y-4">
      {/* Write-only notice banner */}
      <div className="bg-warning/5 border-warning-border flex items-start gap-3 rounded-md border p-3">
        <AlertTriangleIcon className="text-warning-fg mt-0.5 size-4 shrink-0" />
        <div className="text-xs">
          <p className="font-medium">Secret values are write-only.</p>
          <p className="text-muted-foreground mt-0.5">
            Once saved, values cannot be retrieved. Reference secrets in your pipeline TOML using{" "}
            <code className="bg-muted rounded px-1 font-mono">{"${secrets.NAME}"}</code>. Never
            paste secret values directly into the TOML.
          </p>
        </div>
      </div>

      {/* Add secret form (toggled) */}
      {addOpen && <AddSecretForm onSave={saveSecret} onDone={() => setAddOpen(false)} />}

      <ListPage<PipelineSecret>
        embedded
        list={list}
        label="Secrets"
        columns={columns}
        rows={page.rows}
        getRowId={(s) => s.id}
        rowActions={(s) => (
          <Can permission="pipeline.secret_manage">
            <DropdownMenuItem
              variant="destructive"
              disabled={deleting}
              onSelect={() => setDeleteTarget(s)}
            >
              <Trash2Icon className="size-4" />
              Delete {s.name}
            </DropdownMenuItem>
          </Can>
        )}
        loading={loading && secrets.length === 0}
        error={error}
        onRetry={onRetry}
        totalCount={page.totalCount}
        empty={{
          icon: <KeyIcon className="size-5" />,
          title: "No secrets",
          description: "Add a secret to make it available in this pipeline via ${secrets.NAME}.",
        }}
      />

      {/* "Add secret" action — only when the form is not already open */}
      {!addOpen && (
        <Can permission="pipeline.secret_manage">
          <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>
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
          if (deleteTarget) await deleteSecret(deleteTarget);
        }}
      />
    </div>
  );
}
