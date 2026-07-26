"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { CopyPlusIcon, LockIcon, PlusIcon, ShieldIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { CREATE_ROLE, UPDATE_ROLE } from "@/graphql/identity/identity.mutations";
import { LIST_ROLES } from "@/graphql/identity/identity.queries";
import type {
  AstroliftRole,
  MutationResult,
  ScopeKind,
} from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface RolesResp {
  astroliftRoles: AstroliftRole[];
}

const SCOPE_TONE: Record<string, string> = {
  ORG: "bg-info/15 text-info-fg",
  TEAM: "bg-purple-500/15 text-purple-700 dark:text-purple-300",
  PROJECT: "bg-success/15 text-success-fg",
  APP: "bg-warning/15 text-warning-fg",
};

const SCOPE_OPTIONS: ScopeKind[] = ["ORG", "TEAM", "PROJECT", "APP"];

type EditorMode = "create" | "edit";

/** Split a `<resource>.<verb>` slug into its resource bucket, or "other". */
function resourceOf(permission: string): string {
  const dot = permission.indexOf(".");
  return dot >= 0 ? permission.slice(0, dot) : "other";
}

export function RolesTab() {
  const perms = useMyPermissions();
  const canManage = perms.can("org.manage_members");

  const { data, loading } = useQuery<RolesResp>(LIST_ROLES, {
    fetchPolicy: "cache-and-network",
  });
  const roleList = React.useMemo(() => data?.astroliftRoles ?? [], [data]);

  // The available-permissions catalog for the checklist is the union of
  // every role's permission set. The `org_owner` system role carries the
  // full `Permission` enum (backend `system_roles.py`), so this union is
  // the complete catalog — derived client-side with no dedicated catalog
  // query, and it can never drift ahead of what the backend will accept.
  const allPermissions = React.useMemo(() => {
    const set = new Set<string>();
    for (const r of roleList) for (const p of r.permissions) set.add(p);
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [roleList]);

  const [sheetOpen, setSheetOpen] = React.useState(false);
  const [sheetMode, setSheetMode] = React.useState<EditorMode>("create");
  // In edit mode this is the role being edited; in create mode it is an
  // optional seed to clone from (null = blank new role).
  const [sheetRole, setSheetRole] = React.useState<AstroliftRole | null>(null);

  function openCreate() {
    setSheetMode("create");
    setSheetRole(null);
    setSheetOpen(true);
  }
  function openRole(role: AstroliftRole) {
    setSheetMode("edit");
    setSheetRole(role);
    setSheetOpen(true);
  }
  function cloneRole(role: AstroliftRole) {
    // Switch the open sheet from a read-only system role into a fresh
    // create form seeded with that role's permissions — the "clone and
    // prune" path the backend documents for system roles.
    setSheetMode("create");
    setSheetRole(role);
  }

  return (
    <>
      <Section
        title="Roles"
        description="A role is a named permission set. System roles ship with the platform and are read-only; create custom roles to tailor access."
        action={
          <Can permission="org.manage_members">
            <Button size="sm" onClick={openCreate}>
              <PlusIcon className="size-4" />
              New role
            </Button>
          </Can>
        }
      >
        {loading && roleList.length === 0 ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : roleList.length === 0 ? (
          <EmptyState
            icon={<ShieldIcon className="size-5" />}
            title="No roles"
            description="System roles are seeded on deploy; if none appear, the org context may not be resolved yet."
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Role</TableHead>
                <TableHead>Scope</TableHead>
                <TableHead>Type</TableHead>
                <TableHead className="text-right">Permissions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {roleList.map((role) => (
                <TableRow
                  key={role.id}
                  tabIndex={0}
                  role="button"
                  aria-label={`${role.isSystem || !canManage ? "View" : "Edit"} role ${role.name}`}
                  onClick={() => openRole(role)}
                  onKeyDown={(ev) => {
                    if (ev.key === "Enter" || ev.key === " ") {
                      ev.preventDefault();
                      openRole(role);
                    }
                  }}
                  className="hover:bg-accent/30 focus-visible:outline-ring cursor-pointer focus-visible:outline-2 focus-visible:outline-offset-[-2px]"
                >
                  <TableCell>
                    <div className="font-medium">{role.name}</div>
                    <div className="text-muted-foreground font-mono text-xs">{role.slug}</div>
                  </TableCell>
                  <TableCell>
                    <Badge className={SCOPE_TONE[role.scopeLevel]} variant="secondary">
                      {role.scopeLevel}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {role.isSystem ? (
                      <Badge variant="outline" className="gap-1">
                        <LockIcon className="size-3" />
                        System
                      </Badge>
                    ) : (
                      <Badge variant="secondary">Custom</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {role.permissions.length}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Section>

      <RoleEditorSheet
        open={sheetOpen}
        onOpenChange={setSheetOpen}
        mode={sheetMode}
        role={sheetRole}
        allPermissions={allPermissions}
        canManage={canManage}
        onClone={cloneRole}
      />
    </>
  );
}

interface RoleEditorSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: EditorMode;
  /** Edit: the role to edit. Create: optional seed to clone (null = blank). */
  role: AstroliftRole | null;
  allPermissions: string[];
  canManage: boolean;
  onClone: (role: AstroliftRole) => void;
}

function RoleEditorSheet({
  open,
  onOpenChange,
  mode,
  role,
  allPermissions,
  canManage,
  onClone,
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

  const [createRole, { loading: creating }] = useMutation<{
    createRole: MutationResult<AstroliftRole>;
  }>(CREATE_ROLE, {
    refetchQueries: [{ query: LIST_ROLES }],
    awaitRefetchQueries: true,
  });
  const [updateRole, { loading: updating }] = useMutation<{
    updateRole: MutationResult<AstroliftRole>;
  }>(UPDATE_ROLE, {
    refetchQueries: [{ query: LIST_ROLES }],
    awaitRefetchQueries: true,
  });
  const saving = creating || updating;

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
    const permissions = Array.from(selected).sort((a, b) => a.localeCompare(b));

    if (mode === "create") {
      if (!slug.trim() || !name.trim()) return;
      const { data } = await createRole({
        variables: {
          input: {
            slug: slug.trim().toLowerCase(),
            name: name.trim(),
            scopeLevel,
            permissions,
            description: description.trim(),
          },
        },
      });
      if (data?.createRole.ok) {
        toast.success(`Role “${name.trim()}” created`);
        onOpenChange(false);
      } else {
        toast.error(data?.createRole.errors?.[0]?.message ?? "Create failed");
      }
      return;
    }

    if (!role) return;
    const { data } = await updateRole({
      variables: {
        input: {
          id: role.id,
          name: name.trim(),
          description: description.trim(),
          permissions,
        },
      },
    });
    if (data?.updateRole.ok) {
      toast.success(`Role “${name.trim()}” updated`);
      onOpenChange(false);
    } else {
      toast.error(data?.updateRole.errors?.[0]?.message ?? "Update failed");
    }
  }

  const title =
    mode === "create" ? "New role" : readOnly ? `Role: ${role?.name ?? ""}` : `Edit ${role?.name ?? "role"}`;

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
                {saving
                  ? "Saving…"
                  : mode === "create"
                    ? "Create role"
                    : "Save changes"}
              </Button>
            )}
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
