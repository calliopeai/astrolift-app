"use client";

import { AlertTriangleIcon, KeyIcon, Loader2Icon, PlusIcon, Trash2Icon } from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";
import { useFormatters } from "@/lib/i18n/formatters";

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

import { localizedPipelineList, PIPELINE_SECRETS_SELECT } from "./pipelines-list";
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
  const t = useTranslations("PipelineUI");
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
      <p className="text-muted-foreground text-xs">{t("secrets.formNotice")}</p>
      <div className="flex gap-3">
        <div className="flex-1 space-y-1">
          <Label htmlFor="secret-name" className="text-xs">
            {t("name")}
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
            {t("secrets.value")}
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
          {t("secrets.cancel")}
        </Button>
        <Button type="submit" size="sm" disabled={saving || !name.trim() || !value}>
          {saving ? (
            <Loader2Icon className="size-4 animate-spin" aria-label={t("secrets.saving")} />
          ) : (
            t("secrets.save")
          )}
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
  const t = useTranslations("PipelineUI");
  const fmt = useFormatters();
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
      header: t("name"),
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
      header: t("secrets.value"),
      cell: () => (
        <Badge variant="secondary" className="text-2xs font-mono">
          {t("secrets.valueSet")}
        </Badge>
      ),
    },
    {
      id: "created",
      header: t("secrets.created"),
      sortKey: "created",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (s) => fmt.formatDateTime(s.createdAt),
    },
    {
      id: "updated",
      header: t("secrets.updated"),
      sortKey: "updated",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (s) => fmt.formatDateTime(s.updatedAt),
    },
  ];

  return (
    <div className="space-y-4">
      {/* Write-only notice banner */}
      <div className="bg-warning/5 border-warning-border flex items-start gap-3 rounded-md border p-3">
        <AlertTriangleIcon className="text-warning-fg mt-0.5 size-4 shrink-0" />
        <div className="text-xs">
          <p className="font-medium">{t("secrets.noticeTitle")}</p>
          <p className="text-muted-foreground mt-0.5">
            {t.rich("secrets.noticeDescription", {
              reference: () => (
                <code className="bg-muted rounded px-1 font-mono">{"${secrets.NAME}"}</code>
              ),
            })}
          </p>
        </div>
      </div>

      {/* Add secret form (toggled) */}
      {addOpen && <AddSecretForm onSave={saveSecret} onDone={() => setAddOpen(false)} />}

      <ListPage<PipelineSecret>
        embedded
        list={{ ...list, definition: localizedPipelineList(list.definition, t) }}
        label={t("tabs.secrets")}
        columns={columns}
        rows={page.rows}
        getRowId={(s) => s.id}
        rowActions={(s) => (
          <Can permission={{ allOf: ["pipeline.secret_manage", "secret.write"] }} loading={<></>}>
            <DropdownMenuItem
              variant="destructive"
              disabled={deleting}
              onSelect={() => setDeleteTarget(s)}
            >
              <Trash2Icon className="size-4" />
              {t("secrets.deleteNamed", { name: s.name })}
            </DropdownMenuItem>
          </Can>
        )}
        loading={loading && secrets.length === 0}
        error={error}
        onRetry={onRetry}
        totalCount={page.totalCount}
        empty={{
          icon: <KeyIcon className="size-5" />,
          title: t("secrets.empty"),
          description: t("secrets.emptyDescription", { reference: "${secrets.NAME}" }),
        }}
      />

      {/* "Add secret" action — only when the form is not already open */}
      {!addOpen && (
        <Can permission={{ allOf: ["pipeline.secret_manage", "secret.write"] }} loading={<></>}>
          <Button variant="outline" size="sm" onClick={() => setAddOpen(true)}>
            <PlusIcon className="size-4" />
            {t("secrets.add")}
          </Button>
        </Can>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={
          deleteTarget
            ? t("secrets.deleteTitle", { name: deleteTarget.name })
            : t("secrets.deleteFallback")
        }
        description={t("secrets.deleteDescription")}
        confirmLabel={t("secrets.delete")}
        destructive
        onConfirm={async () => {
          if (deleteTarget) await deleteSecret(deleteTarget);
        }}
      />
    </div>
  );
}
