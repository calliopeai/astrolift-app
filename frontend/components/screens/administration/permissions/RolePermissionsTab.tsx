"use client";

import { CopyPlusIcon, LockIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { diffPermissions } from "@/components/access/access-model";
import { PermissionMatrix } from "@/components/access/PermissionMatrix";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AstroliftRole } from "@/graphql/identity/identity.types";

export interface RolePermissionsTabProps {
  role: AstroliftRole;
  /** Every role, to compare this one against. */
  roles: AstroliftRole[];
  /** The whole catalog (roleCatalog), never a page's worth. */
  catalog: string[];
  /** The viewer may manage roles. A built-in role stays read-only regardless. */
  canManage: boolean;
  saving: boolean;
  /** Null when saved, or the reason, shown beside the buttons. */
  onSave: (permissions: string[]) => Promise<string | null>;
  duplicateHref: string;
}

const SAVED = "saved";
const NONE = "none";
/** The role this one was duplicated from (`duplicatedFrom`), even after it was deleted. */
const LINEAGE = "lineage";

/**
 * A role's Permissions tab (design 3.5): the `PermissionMatrix`, areas by
 * verbs. A built-in role is read-only with Duplicate to customise; a custom
 * role edits in place, a cell, a row or an area at a time, with Save and
 * Discard, and diffs against its saved version while it has edits. A role
 * made by duplicating says what it came from and how far it has moved, and
 * can be compared with that source (`duplicatedFrom`, kept even when the
 * source is deleted) as with any other role. The caller keys it by the
 * saved set, so a save or a refetch starts it afresh. Pure.
 */
export function RolePermissionsTab({
  role,
  roles,
  catalog,
  canManage,
  saving,
  onSave,
  duplicateHref,
}: RolePermissionsTabProps) {
  const editable = canManage && !role.isSystem;
  const lineage = role.duplicatedFrom ?? null;
  const [draft, setDraft] = React.useState<string[]>(role.permissions);
  const [compare, setCompare] = React.useState(editable ? SAVED : lineage ? LINEAGE : NONE);
  const [error, setError] = React.useState<string | null>(null);

  const pending = diffPermissions(draft, role.permissions);
  const dirty = pending.added.length + pending.removed.length > 0;
  const moved = lineage ? diffPermissions(role.permissions, lineage.permissions) : null;
  const other = roles.find((r) => r.id === compare);
  const base =
    compare === SAVED
      ? role.permissions
      : compare === LINEAGE
        ? lineage?.permissions
        : other?.permissions;
  const baseLabel = compare === SAVED ? "saved" : compare === LINEAGE ? lineage?.name : other?.name;

  async function save() {
    setError(await onSave([...draft].sort((a, b) => a.localeCompare(b))));
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {role.isSystem && (
        <div className="bg-muted/30 flex min-w-0 flex-wrap items-center gap-3 rounded-md border p-3 text-sm">
          <LockIcon aria-hidden className="text-muted-foreground size-4 shrink-0" />
          <p className="min-w-0 flex-1">
            Built-in roles ship with the platform and cannot be changed. Duplicate it to make a
            custom role you can prune.
          </p>
          {canManage && (
            <Button size="sm" variant="outline" asChild>
              <Link href={duplicateHref}>
                <CopyPlusIcon className="size-4" />
                Duplicate to customise
              </Link>
            </Button>
          )}
        </div>
      )}

      {lineage && moved && (
        <p className="text-muted-foreground min-w-0 text-sm [overflow-wrap:anywhere]">
          Duplicated from <span className="text-foreground font-medium">{lineage.name}</span>
          {lineage.isSystem ? " (built-in)" : ""}
          {lineage.deleted ? ", since deleted" : ""}:{" "}
          <span className="font-mono text-xs tabular-nums">
            <span className="text-success-fg">+{moved.added.length}</span>{" "}
            <span className="text-danger-fg">−{moved.removed.length}</span>
          </span>{" "}
          since.
          {compare !== LINEAGE && (
            <>
              {" "}
              <button
                type="button"
                onClick={() => setCompare(LINEAGE)}
                className="text-foreground underline underline-offset-2"
              >
                Compare with {lineage.name}
              </button>
            </>
          )}
        </p>
      )}

      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <Label htmlFor="role-compare" className="text-muted-foreground text-sm font-normal">
          Compare with
        </Label>
        <Select value={compare} onValueChange={setCompare}>
          <SelectTrigger id="role-compare" className="h-8 w-64 max-w-full min-w-0">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {editable ? (
              <SelectItem value={SAVED}>Saved version</SelectItem>
            ) : (
              <SelectItem value={NONE}>Nothing</SelectItem>
            )}
            {lineage && (
              <SelectItem value={LINEAGE}>
                {lineage.name} (duplicated from{lineage.deleted ? ", deleted" : ""})
              </SelectItem>
            )}
            {roles
              .filter((r) => r.id !== role.id && r.id !== lineage?.id)
              .map((r) => (
                <SelectItem key={r.id} value={r.id}>
                  {r.name}
                  {r.isSystem ? " (built-in)" : ""}
                </SelectItem>
              ))}
          </SelectContent>
        </Select>
      </div>

      <PermissionMatrix
        permissions={draft}
        catalog={catalog}
        onChange={editable ? setDraft : undefined}
        base={base}
        baseLabel={baseLabel}
      />

      {editable && (
        <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 border-t pt-3">
          {error ? (
            <p
              role="alert"
              className="text-danger mr-auto min-w-0 text-sm [overflow-wrap:anywhere]"
            >
              {error}
            </p>
          ) : (
            <p className="text-muted-foreground mr-auto font-mono text-xs tabular-nums">
              {dirty ? (
                <>
                  <span className="text-success-fg">+{pending.added.length}</span>{" "}
                  <span className="text-danger-fg">−{pending.removed.length}</span> unsaved
                </>
              ) : (
                "No unsaved changes"
              )}
            </p>
          )}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={!dirty || saving}
            onClick={() => {
              setDraft(role.permissions);
              setError(null);
            }}
          >
            Discard
          </Button>
          <Button type="button" size="sm" disabled={!dirty || saving} onClick={save}>
            {saving ? "Saving…" : "Save permissions"}
          </Button>
        </div>
      )}
    </div>
  );
}
