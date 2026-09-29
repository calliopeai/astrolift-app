"use client";

import { KeyRoundIcon, PlusIcon, Trash2Icon, UsersIcon } from "lucide-react";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { selectRows } from "@/components/list/select-rows";
import { useLocalListState } from "@/components/list/use-list-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { DropdownMenuItem, DropdownMenuSeparator } from "@/components/ui/dropdown-menu";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftClusterAuthUser } from "@/graphql/__generated__/schema";

import { AUTH_USERS_LIST, AUTH_USERS_SELECT } from "./auth-users-list";
import type { NewAuthUser } from "./types";
import type { useAuthUsers } from "./use-auth-users";

export type AuthUsersViewProps = ReturnType<typeof useAuthUsers>;

/**
 * The users of the cluster's central auth (#2131): who can sign in to the
 * apps behind it, as the section's embedded list (search, group and state
 * filters, sort, numbered pages over the provider's answer; groups edit in
 * place, password, enable and delete in each row's `⋯`). Passwords are
 * typed here and sent once; nothing the server returns carries one.
 */
export function AuthUsersView({
  view,
  loading,
  onSetGroups,
  onCreateGroup,
  onToggleEnabled,
  onCreate,
  onSetPassword,
  onResetPassword,
  onDelete,
}: AuthUsersViewProps) {
  const [creating, setCreating] = React.useState(false);
  const [passwordFor, setPasswordFor] = React.useState<AstroliftClusterAuthUser | null>(null);
  const [deleting, setDeleting] = React.useState<AstroliftClusterAuthUser | null>(null);
  const list = useLocalListState(AUTH_USERS_LIST);

  if (loading && !view) {
    return (
      <Section title="Sign-in users" divided>
        <Skeleton className="h-24 w-full" />
      </Section>
    );
  }
  if (!view) return null;

  const page = selectRows(
    view.users,
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    AUTH_USERS_SELECT
  );

  const columns: Column<AstroliftClusterAuthUser>[] = [
    {
      id: "user",
      header: "User",
      sortKey: "user",
      cellClassName: "max-w-72",
      cell: (u) => (
        <span className="block truncate font-mono text-sm" title={u.email || u.username}>
          {u.email || u.username}
        </span>
      ),
    },
    {
      id: "status",
      header: "Status",
      sortKey: "status",
      cell: (u) => (
        <div className="flex flex-wrap gap-1">
          <Badge variant={u.enabled ? "secondary" : "outline"}>
            {u.enabled ? "enabled" : "disabled"}
          </Badge>
          <span className="text-muted-foreground text-2xs font-mono">
            {u.status.toLowerCase().replace(/_/g, " ")}
          </span>
        </div>
      ),
    },
    {
      id: "groups",
      header: "Groups",
      cell: (u) => (
        <GroupEditor
          user={u}
          groups={view.groups}
          onChange={(add, remove) => onSetGroups(u.username, add, remove)}
          onCreateGroup={onCreateGroup}
        />
      ),
    },
  ];

  return (
    <Section
      title={
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          Sign-in users
          {view.provider && (
            <Badge variant="outline" className="text-2xs font-normal">
              {view.provider}
            </Badge>
          )}
        </span>
      }
      description={<>The logins of this cluster&apos;s central auth. {view.reachNote}</>}
      action={
        view.supported && (
          <Button size="sm" onClick={() => setCreating(true)}>
            <PlusIcon className="size-4" />
            Add user
          </Button>
        )
      }
      divided
    >
      <div className="min-w-0">
        {!view.supported ? (
          <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">{view.reason}</p>
        ) : (
          <>
            <ListPage<AstroliftClusterAuthUser>
              embedded
              list={list}
              label="Sign-in users"
              columns={columns}
              rows={page.rows}
              getRowId={(u) => u.username}
              rowActions={(u) => (
                <>
                  <DropdownMenuItem onSelect={() => setPasswordFor(u)}>
                    <KeyRoundIcon className="size-4" />
                    Password
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => void onToggleEnabled(u)}>
                    {u.enabled ? "Disable" : "Enable"}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive" onSelect={() => setDeleting(u)}>
                    <Trash2Icon className="size-4" />
                    Delete {u.email || u.username}
                  </DropdownMenuItem>
                </>
              )}
              totalCount={page.totalCount}
              empty={{ icon: <UsersIcon className="size-5" />, title: "No users yet" }}
            />
          </>
        )}
      </div>

      <CreateUserDialog
        open={creating}
        onOpenChange={setCreating}
        groups={view.groups}
        onCreate={onCreate}
      />

      <PasswordDialog
        user={passwordFor}
        onClose={() => setPasswordFor(null)}
        onSet={async (password, permanent) =>
          passwordFor ? onSetPassword(passwordFor.username, password, permanent) : false
        }
        onReset={async () => (passwordFor ? onResetPassword(passwordFor.username) : false)}
      />

      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title={`Delete ${deleting?.email || deleting?.username || "user"}?`}
        description="They can no longer sign in to any app on this cluster. This cannot be undone."
        confirmLabel="Delete user"
        destructive
        onConfirm={async () => {
          if (!deleting) return;
          await onDelete(deleting.username);
        }}
      />
    </Section>
  );
}

function GroupEditor({
  user,
  groups,
  onChange,
  onCreateGroup,
}: {
  user: AstroliftClusterAuthUser;
  groups: string[];
  onChange: (add: string[], remove: string[]) => Promise<void>;
  onCreateGroup: (name: string) => Promise<boolean>;
}) {
  const [adding, setAdding] = React.useState("");
  const others = groups.filter((g) => !user.groups.includes(g));
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1">
      {user.groups.map((g) => (
        <Badge key={g} variant="secondary" className="gap-1">
          {g}
          <button
            type="button"
            className="hover:text-destructive"
            aria-label={`Remove from ${g}`}
            onClick={() => onChange([], [g])}
          >
            ×
          </button>
        </Badge>
      ))}
      <form
        className="flex items-center gap-1"
        onSubmit={async (e) => {
          e.preventDefault();
          const name = adding.trim();
          if (!name) return;
          if (!groups.includes(name) && !(await onCreateGroup(name))) return;
          await onChange([name], []);
          setAdding("");
        }}
      >
        <Input
          aria-label={`Add ${user.email || user.username} to a group`}
          list={`groups-${user.username}`}
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
          placeholder="Add group"
          className="h-7 w-28 text-xs"
        />
        <datalist id={`groups-${user.username}`}>
          {others.map((g) => (
            <option key={g} value={g} />
          ))}
        </datalist>
      </form>
    </div>
  );
}

function CreateUserDialog({
  open,
  onOpenChange,
  groups,
  onCreate,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  groups: string[];
  onCreate: (input: NewAuthUser) => Promise<boolean>;
}) {
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [permanent, setPermanent] = React.useState(false);
  const [chosen, setChosen] = React.useState<string[]>([]);
  const [busy, setBusy] = React.useState(false);

  function close() {
    setEmail("");
    setPassword("");
    setPermanent(false);
    setChosen([]);
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={(o) => (o ? onOpenChange(true) : close())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a sign-in user</DialogTitle>
          <DialogDescription>
            Leave the password empty and the provider emails a temporary one. A password you type
            here is sent once and never shown again.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            const ok = await onCreate({
              email: email.trim(),
              password: password || null,
              permanent: Boolean(password) && permanent,
              groups: chosen,
            });
            setBusy(false);
            if (ok) close();
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="auth-user-email">Email</Label>
            <Input
              id="auth-user-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="auth-user-password">Password (optional)</Label>
            <Input
              id="auth-user-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {password && (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={permanent} onCheckedChange={(v) => setPermanent(v === true)} />
              Permanent (skip the change-password prompt at first sign-in)
            </label>
          )}
          {groups.length > 0 && (
            <fieldset className="space-y-1.5">
              <legend className="text-sm font-medium">Groups</legend>
              <div className="flex flex-wrap gap-3">
                {groups.map((g) => (
                  <label key={g} className="flex items-center gap-1.5 text-sm">
                    <Checkbox
                      checked={chosen.includes(g)}
                      onCheckedChange={(v) =>
                        setChosen((c) => (v === true ? [...c, g] : c.filter((x) => x !== g)))
                      }
                    />
                    {g}
                  </label>
                ))}
              </div>
            </fieldset>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={close}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !email.trim()}>
              {busy ? "Adding…" : "Add user"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function PasswordDialog({
  user,
  onClose,
  onSet,
  onReset,
}: {
  user: AstroliftClusterAuthUser | null;
  onClose: () => void;
  onSet: (password: string, permanent: boolean) => Promise<boolean>;
  onReset: () => Promise<boolean>;
}) {
  const [password, setPassword] = React.useState("");
  const [permanent, setPermanent] = React.useState(true);
  const [busy, setBusy] = React.useState(false);

  function close() {
    setPassword("");
    setPermanent(true);
    onClose();
  }

  return (
    <Dialog open={user !== null} onOpenChange={(o) => (o ? undefined : close())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Password for {user?.email || user?.username}</DialogTitle>
          <DialogDescription>
            Set one now, or have the provider email the user a reset code.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            const ok = await onSet(password, permanent);
            setBusy(false);
            if (ok) close();
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="auth-user-new-password">New password</Label>
            <Input
              id="auth-user-new-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={permanent} onCheckedChange={(v) => setPermanent(v === true)} />
            Permanent (no change-password prompt at next sign-in)
          </label>
          <DialogFooter className="gap-2 sm:justify-between">
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                const ok = await onReset();
                setBusy(false);
                if (ok) close();
              }}
            >
              Email a reset code
            </Button>
            <Button type="submit" disabled={busy || !password}>
              Set password
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
