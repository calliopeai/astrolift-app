"use client";

import { CopyPlusIcon } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import type { AstroliftRole, ScopeKind } from "@/graphql/identity/identity.types";

import type { RoleDraft } from "./use-roles";

const SCOPE_OPTIONS: ScopeKind[] = ["ORG", "TEAM", "PROJECT", "APP"];

export type EditorMode = "create" | "edit";

/** Split a `<resource>.<verb>` slug into its resource bucket, or "other". */
function resourceOf(permission: string): string {
  const dot = permission.indexOf(".");
  return dot >= 0 ? permission.slice(0, dot) : "other";
}

export interface RoleEditorSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: EditorMode;
  /** Edit: the role to edit. Create: optional seed to clone (null = blank). */
  role: AstroliftRole | null;
  allPermissions: string[];
  canManage: boolean;
  onClone: (role: AstroliftRole) => void;
  saving: boolean;
  /** Resolves true on success; the sheet then closes. */
  onCreate: (draft: RoleDraft) => Promise<boolean>;
  /** Resolves true on success; the sheet then closes. */
  onUpdate: (id: string, draft: RoleDraft) => Promise<boolean>;
}

export function RoleEditorSheet({
  open,
  onOpenChange,
  mode,
  role,
  allPermissions,
  canManage,
  onClone,
  saving,
  onCreate,
  onUpdate,
}: RoleEditorSheetProps) {
  const [slug, setSlug] = React.useState("");
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [scopeLevel, setScopeLevel] = React.useState<ScopeKind>("ORG");
  const [selected, setSelected] = React.useState<Set<string>>(() => new Set());
  const [filter, setFilter] = React.useState("");

  // System roles are read-only server-side; so is the whole editor for a
  // viewer without org.manage_members. Create mode always requires manage
  // (it's only reachable via a manage-gated button).
  const readOnly = mode === "edit" && (!canManage || (role?.isSystem ?? false));

  // Initialise the form whenever the sheet opens or its target changes
  // (including the edit→create switch on Clone). Reads the current mode +
  // role snapshot; deps intentionally cover open/mode/role.
  React.useEffect(() => {
    if (!open) return;
    if (mode === "edit" && role) {
      setSlug(role.slug);
      setName(role.name);
      setDescription(role.description ?? "");
      setScopeLevel(role.scopeLevel);
      setSelected(new Set(role.permissions));
    } else {
      // create — blank, or seeded from a role being cloned.
      setSlug("");
      setName(role ? `${role.name} (copy)` : "");
      setDescription(role?.description ?? "");
      setScopeLevel(role?.scopeLevel ?? "ORG");
      setSelected(new Set(role?.permissions ?? []));
    }
    setFilter("");
  }, [open, mode, role]);

  const grouped = React.useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const shown = needle
      ? allPermissions.filter((p) => p.toLowerCase().includes(needle))
      : allPermissions;
    const map = new Map<string, string[]>();
    for (const p of shown) {
      const resource = resourceOf(p);
      if (!map.has(resource)) map.set(resource, []);
      map.get(resource)!.push(p);
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [allPermissions, filter]);

  function togglePermission(permission: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(permission)) next.delete(permission);
      else next.add(permission);
      return next;
    });
  }

  function toggleGroup(groupPerms: string[]) {
    setSelected((prev) => {
      const next = new Set(prev);
      const allOn = groupPerms.every((p) => next.has(p));
      for (const p of groupPerms) {
        if (allOn) next.delete(p);
        else next.add(p);
      }
      return next;
    });
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (readOnly || saving) return;
    const draft: RoleDraft = {
      slug,
      name,
      description,
      scopeLevel,
      permissions: Array.from(selected).sort((a, b) => a.localeCompare(b)),
    };

    if (mode === "create") {
      if (!slug.trim() || !name.trim()) return;
      if (await onCreate(draft)) onOpenChange(false);
      return;
    }

    if (!role) return;
    if (await onUpdate(role.id, draft)) onOpenChange(false);
  }

  const title =
    mode === "create"
      ? "New role"
      : readOnly
        ? `Role: ${role?.name ?? ""}`
        : `Edit ${role?.name ?? "role"}`;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{title}</SheetTitle>
          <SheetDescription>
            {readOnly
              ? "System roles ship with the platform and can't be edited. Clone this role to create an editable copy you can prune."
              : "Toggle the permissions this role grants. Permissions are validated against the catalog on save."}
          </SheetDescription>
        </SheetHeader>

        <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col gap-4 px-4 pb-4">
          {mode === "create" && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="role-slug">Slug</Label>
                <Input
                  id="role-slug"
                  value={slug}
                  onChange={(e) => setSlug(e.target.value)}
                  placeholder="release-manager"
                  required
                  autoComplete="off"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="role-scope">Scope level</Label>
                <Select value={scopeLevel} onValueChange={(v) => setScopeLevel(v as ScopeKind)}>
                  <SelectTrigger id="role-scope">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SCOPE_OPTIONS.map((s) => (
                      <SelectItem key={s} value={s}>
                        {s}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="role-name">Name</Label>
            <Input
              id="role-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Release Manager"
              required
              readOnly={readOnly}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="role-description">Description</Label>
            <Textarea
              id="role-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this role is for."
              rows={2}
              readOnly={readOnly}
            />
          </div>

          <div className="flex min-h-0 flex-1 flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <Label>Permissions</Label>
              <span className="text-muted-foreground text-xs tabular-nums">
                {selected.size} of {allPermissions.length} selected
              </span>
            </div>
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter permissions (e.g. 'app.')"
              className="h-8"
            />
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto rounded-md border p-3">
              {grouped.length === 0 ? (
                <p className="text-muted-foreground text-sm">No permissions match “{filter}”.</p>
              ) : (
                grouped.map(([resource, groupPerms]) => {
                  const allOn = groupPerms.every((p) => selected.has(p));
                  return (
                    <div key={resource}>
                      <div className="mb-1.5 flex items-center gap-2">
                        <Badge variant="secondary">{resource}</Badge>
                        {!readOnly && (
                          <button
                            type="button"
                            onClick={() => toggleGroup(groupPerms)}
                            className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline"
                          >
                            {allOn ? "Clear all" : "Select all"}
                          </button>
                        )}
                      </div>
                      <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
                        {groupPerms.map((p) => (
                          <label
                            key={p}
                            className="hover:bg-muted/40 flex cursor-pointer items-center gap-2 rounded px-2 py-1 font-mono text-xs"
                          >
                            <input
                              type="checkbox"
                              className="size-4 shrink-0"
                              checked={selected.has(p)}
                              onChange={() => togglePermission(p)}
                              disabled={readOnly}
                            />
                            {p}
                          </label>
                        ))}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {readOnly ? "Close" : "Cancel"}
            </Button>
            {readOnly ? (
              canManage && role ? (
                <Button type="button" onClick={() => onClone(role)}>
                  <CopyPlusIcon className="size-4" />
                  Clone as custom role
                </Button>
              ) : null
            ) : (
              <Button
                type="submit"
                disabled={saving || !name.trim() || (mode === "create" && !slug.trim())}
              >
                {saving ? "Saving…" : mode === "create" ? "Create role" : "Save changes"}
              </Button>
            )}
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
