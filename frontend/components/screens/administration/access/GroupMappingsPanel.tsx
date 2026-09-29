"use client";

import { LinkIcon, PlusIcon, Trash2Icon } from "lucide-react";
import * as React from "react";

import {
  canBindAt,
  type RoleRef,
  SCOPE_NOUN,
  type ScopeNode,
  type ScopeRef,
} from "@/components/access/access-model";
import { ScopePicker } from "@/components/access/ScopePicker";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useFormatters } from "@/lib/i18n/formatters";

/** An `AstroliftGroupRoleMapping` row, as far as the panel reads it. */
export interface GroupMapping {
  id: string;
  role: { id: string; slug: string; name: string };
  scopeKind: string;
  sourceScopeLabel: string;
  memberCount: number;
  createdAt: string;
}

export interface GroupMappingsPanelProps {
  externalId: string;
  /** In memory, not the URL: the Access tab's own list owns the page's query string. */
  list: ListStateController;
  rows: GroupMapping[];
  totalCount: number;
  loading: boolean;
  stale?: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  canManage: boolean;
  /** Roles the add form offers. */
  roles: RoleRef[];
  scopeTree: {
    roots: ScopeNode[];
    loading?: boolean;
    error?: { message: string } | null;
    onRetry?: () => void;
    loadChildren?: (node: ScopeNode) => Promise<ScopeNode[]>;
  };
  /** Null when mapped, or the server's reason, shown in the form. */
  onCreate: (roleId: string, scope: ScopeRef) => Promise<string | null>;
  /** Throws on failure, so the confirm stays open and says why. */
  onDelete: (mapping: GroupMapping) => Promise<void>;
  /** Stories: open with the add form showing. */
  initialAdding?: boolean;
}

/**
 * An IdP group's role mappings (#2157), under its grants on the Access tab:
 * the group mapped to a role at a scope applies to everyone the identity
 * provider puts in the group, as a group grant does, and is how an org
 * wires its IdP groups to Astrolift roles. Add maps a role at a scope; a
 * row's menu removes it after a confirm. Pure.
 */
export function GroupMappingsPanel({
  externalId,
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
  canManage,
  roles,
  scopeTree,
  onCreate,
  onDelete,
  initialAdding = false,
}: GroupMappingsPanelProps) {
  const fmt = useFormatters();
  const [adding, setAdding] = React.useState(initialAdding);
  const [target, setTarget] = React.useState<GroupMapping | null>(null);

  const columns: Column<GroupMapping>[] = [
    {
      id: "role",
      header: "Role",
      cellClassName: "max-w-72",
      cell: (m) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium" title={m.role.name}>
            {m.role.name}
          </span>
          <span
            className="text-muted-foreground block truncate font-mono text-xs"
            title={m.role.slug}
          >
            {m.role.slug}
          </span>
        </span>
      ),
    },
    {
      id: "scope",
      header: "Where",
      cellClassName: "max-w-64",
      cell: (m) => (
        <span className="flex min-w-0 items-center gap-1.5">
          <Badge variant="outline" className="text-2xs shrink-0 font-mono uppercase">
            {SCOPE_NOUN[m.scopeKind as keyof typeof SCOPE_NOUN] ?? m.scopeKind}
          </Badge>
          <span className="min-w-0 truncate text-xs" title={m.sourceScopeLabel}>
            {m.sourceScopeLabel}
          </span>
        </span>
      ),
    },
    {
      id: "members",
      header: "Reaches",
      align: "right",
      cellClassName: "font-mono text-xs tabular-nums",
      cell: (m) => `${m.memberCount} ${m.memberCount === 1 ? "member" : "members"}`,
    },
    {
      id: "created",
      header: "Mapped",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (m) => fmt.formatDate(m.createdAt),
    },
  ];

  return (
    <section aria-label="Role mappings" className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-medium">Role mappings</h2>
          <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
            Everyone the identity provider puts in {externalId} gets these roles, as with a grant.
          </p>
        </div>
        {canManage && !adding && (
          <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
            <PlusIcon className="size-4" />
            Map a role
          </Button>
        )}
      </div>

      {adding && (
        <AddMapping
          roles={roles}
          scopeTree={scopeTree}
          onCreate={onCreate}
          onDone={() => setAdding(false)}
        />
      )}

      <ListPage<GroupMapping>
        embedded
        list={list}
        label="Role mappings"
        columns={columns}
        rows={rows}
        getRowId={(m) => m.id}
        loading={loading}
        stale={stale}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        rowActions={
          canManage
            ? (m) => (
                <DropdownMenuItem variant="destructive" onSelect={() => setTarget(m)}>
                  <Trash2Icon className="size-4" />
                  Remove this mapping
                </DropdownMenuItem>
              )
            : undefined
        }
        empty={{
          icon: <LinkIcon className="size-5" />,
          title: "No role mappings",
          description: `${externalId} is not mapped to any role. Its members get only what grants give them.`,
        }}
      />

      <ConfirmDialog
        open={target !== null}
        onOpenChange={(next) => {
          if (!next) setTarget(null);
        }}
        title={target ? `Remove ${target.role.slug} from ${externalId}?` : "Remove mapping?"}
        description={
          target
            ? `The ${target.memberCount} ${target.memberCount === 1 ? "member" : "members"} of ${externalId} lose what ${target.role.name} gives at ${target.sourceScopeLabel}, unless another grant gives it.`
            : ""
        }
        confirmLabel="Remove mapping"
        destructive
        onConfirm={async () => {
          if (target) await onDelete(target);
          setTarget(null);
        }}
      />
    </section>
  );
}

function AddMapping({
  roles,
  scopeTree,
  onCreate,
  onDone,
}: Pick<GroupMappingsPanelProps, "roles" | "scopeTree" | "onCreate"> & { onDone: () => void }) {
  const [roleId, setRoleId] = React.useState<string>("");
  const [scope, setScope] = React.useState<ScopeRef | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);
  const role = roles.find((r) => r.id === roleId) ?? null;

  async function submit() {
    if (!role) return setError("Pick a role.");
    if (!scope) return setError("Pick where the role applies.");
    const bindable = canBindAt(role, scope.kind);
    if (bindable !== true) return setError(bindable);
    setSaving(true);
    const reason = await onCreate(role.id, scope);
    setSaving(false);
    if (reason) setError(reason);
    else onDone();
  }

  return (
    <div className="flex min-w-0 flex-col gap-3 border-t pt-3">
      <div className="flex min-w-0 flex-col gap-2">
        <Label htmlFor="mapping-role">Role</Label>
        <Select
          value={roleId}
          onValueChange={(v) => {
            setRoleId(v);
            setError(null);
          }}
        >
          <SelectTrigger id="mapping-role" className="max-w-sm min-w-0">
            <SelectValue placeholder="Pick a role" />
          </SelectTrigger>
          <SelectContent>
            {roles.map((r) => (
              <SelectItem key={r.id} value={r.id}>
                {r.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <ScopePicker
        label="Where it applies"
        roots={scopeTree.roots}
        loading={scopeTree.loading}
        error={scopeTree.error}
        onRetry={scopeTree.onRetry}
        loadChildren={scopeTree.loadChildren}
        value={scope}
        onChange={(n) => {
          setScope({ kind: n.kind, id: n.id, name: n.name });
          setError(null);
        }}
        selectable={role ? (n) => canBindAt(role, n.kind) : undefined}
      />
      {error && (
        <p role="alert" className="text-danger-fg text-sm [overflow-wrap:anywhere]">
          {error}
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Cancel
        </Button>
        <Button type="button" size="sm" disabled={saving} onClick={() => void submit()}>
          {saving ? "Mapping…" : "Map role"}
        </Button>
      </div>
    </div>
  );
}
