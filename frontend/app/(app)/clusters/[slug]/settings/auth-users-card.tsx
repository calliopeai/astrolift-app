"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { KeyRoundIcon, PlusIcon, Trash2Icon, UsersIcon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  AstroliftClusterAuthUser,
  AstroliftClusterAuthUsers,
} from "@/graphql/__generated__/schema";
import {
  CLUSTER_AUTH_USERS,
  CREATE_CLUSTER_AUTH_GROUP,
  CREATE_CLUSTER_AUTH_USER,
  DELETE_CLUSTER_AUTH_USER,
  RESET_CLUSTER_AUTH_USER_PASSWORD,
  SET_CLUSTER_AUTH_USER_ENABLED,
  SET_CLUSTER_AUTH_USER_GROUPS,
  SET_CLUSTER_AUTH_USER_PASSWORD,
} from "@/graphql/clusters/clusters.queries";

/**
 * The users of the cluster's central auth (#2131): who can sign in to the
 * apps behind it. Passwords are typed here and sent once; nothing the server
 * returns carries one.
 */

type Result = { ok: boolean; errors?: { message: string }[] | null };

function firstError(r: Result | undefined, fallback: string): string {
  return r?.errors?.[0]?.message ?? fallback;
}

export function AuthUsersCard({ clusterId }: { clusterId: string }) {
  const { data, loading, refetch } = useQuery<{
    astroliftClusterAuthUsers: AstroliftClusterAuthUsers | null;
  }>(CLUSTER_AUTH_USERS, { variables: { clusterId }, fetchPolicy: "cache-and-network" });
  const view = data?.astroliftClusterAuthUsers;
  const refetchOpts = { onCompleted: () => void refetch() };

  const [createUser] = useMutation<{ createClusterAuthUser: Result }>(
    CREATE_CLUSTER_AUTH_USER,
    refetchOpts
  );
  const [setPassword] = useMutation<{ setClusterAuthUserPassword: Result }>(
    SET_CLUSTER_AUTH_USER_PASSWORD
  );
  const [resetPassword] = useMutation<{ resetClusterAuthUserPassword: Result }>(
    RESET_CLUSTER_AUTH_USER_PASSWORD
  );
  const [setEnabled] = useMutation<{ setClusterAuthUserEnabled: Result }>(
    SET_CLUSTER_AUTH_USER_ENABLED,
    refetchOpts
  );
  const [deleteUser] = useMutation<{ deleteClusterAuthUser: Result }>(
    DELETE_CLUSTER_AUTH_USER,
    refetchOpts
  );
  const [setGroups] = useMutation<{ setClusterAuthUserGroups: Result }>(
    SET_CLUSTER_AUTH_USER_GROUPS,
    refetchOpts
  );
  const [createGroup] = useMutation<{ createClusterAuthGroup: Result }>(
    CREATE_CLUSTER_AUTH_GROUP,
    refetchOpts
  );

  const [creating, setCreating] = React.useState(false);
  const [passwordFor, setPasswordFor] = React.useState<AstroliftClusterAuthUser | null>(null);
  const [deleting, setDeleting] = React.useState<AstroliftClusterAuthUser | null>(null);

  if (loading && !view) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Sign-in users</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Card>
    );
  }
  if (!view) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <UsersIcon className="size-4" />
          Sign-in users
          {view.provider && (
            <Badge variant="outline" className="text-2xs font-normal">
              {view.provider}
            </Badge>
          )}
        </CardTitle>
        <CardDescription>
          The logins of this cluster&apos;s central auth. {view.reachNote}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {!view.supported ? (
          <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">{view.reason}</p>
        ) : (
          <>
            <div className="flex justify-end">
              <Button size="sm" onClick={() => setCreating(true)}>
                <PlusIcon className="size-4" />
                Add user
              </Button>
            </div>
            {view.users.length === 0 ? (
              <p className="text-muted-foreground text-sm">No users yet.</p>
            ) : (
              // A list, not a table: the provider's user list has no cursor
              // to page, which is what DataTable is built on.
              <ul className="divide-y rounded-md border" aria-label="Sign-in users">
                {view.users.map((u) => (
                  <li
                    key={u.username}
                    className="grid min-w-0 gap-2 p-3 sm:grid-cols-[minmax(0,2fr)_auto_minmax(0,2fr)_auto] sm:items-center"
                  >
                    <span className="min-w-0 text-sm [overflow-wrap:anywhere]">
                      {u.email || u.username}
                    </span>
                    <span>
                      <div className="flex flex-wrap gap-1">
                        <Badge variant={u.enabled ? "secondary" : "outline"}>
                          {u.enabled ? "enabled" : "disabled"}
                        </Badge>
                        <span className="text-muted-foreground text-2xs">
                          {u.status.toLowerCase().replace(/_/g, " ")}
                        </span>
                      </div>
                    </span>
                    <span className="min-w-0">
                      <GroupEditor
                        user={u}
                        groups={view.groups}
                        onChange={async (add, remove) => {
                          const { data } = await setGroups({
                            variables: {
                              input: { clusterId, username: u.username, add, remove },
                            },
                          });
                          if (!data?.setClusterAuthUserGroups.ok) {
                            toast.error(
                              firstError(data?.setClusterAuthUserGroups, "Group change failed.")
                            );
                          }
                        }}
                        onCreateGroup={async (name) => {
                          const { data } = await createGroup({
                            variables: { input: { clusterId, name } },
                          });
                          if (!data?.createClusterAuthGroup.ok) {
                            toast.error(
                              firstError(data?.createClusterAuthGroup, "Couldn't create group.")
                            );
                            return false;
                          }
                          return true;
                        }}
                      />
                    </span>
                    <span>
                      <div className="flex flex-wrap justify-end gap-1">
                        <Button size="sm" variant="ghost" onClick={() => setPasswordFor(u)}>
                          <KeyRoundIcon className="size-3.5" />
                          Password
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={async () => {
                            const { data } = await setEnabled({
                              variables: {
                                input: { clusterId, username: u.username, enabled: !u.enabled },
                              },
                            });
                            if (data?.setClusterAuthUserEnabled.ok) {
                              toast.success(u.enabled ? "User disabled." : "User enabled.");
                            } else {
                              toast.error(
                                firstError(data?.setClusterAuthUserEnabled, "Change failed.")
                              );
                            }
                          }}
                        >
                          {u.enabled ? "Disable" : "Enable"}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`Delete ${u.email || u.username}`}
                          onClick={() => setDeleting(u)}
                        >
                          <Trash2Icon className="size-3.5" />
                        </Button>
                      </div>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </CardContent>

      <CreateUserDialog
        open={creating}
        onOpenChange={setCreating}
        groups={view.groups}
        onCreate={async (input) => {
          const { data } = await createUser({ variables: { input: { clusterId, ...input } } });
          if (!data?.createClusterAuthUser.ok) {
            toast.error(firstError(data?.createClusterAuthUser, "Couldn't create the user."));
            return false;
          }
          toast.success(
            input.password
              ? "User created with the password you set."
              : "User created. They were emailed a temporary password."
          );
          return true;
        }}
      />

      <PasswordDialog
        user={passwordFor}
        onClose={() => setPasswordFor(null)}
        onSet={async (password, permanent) => {
          if (!passwordFor) return false;
          const { data } = await setPassword({
            variables: {
              input: { clusterId, username: passwordFor.username, password, permanent },
            },
          });
          if (!data?.setClusterAuthUserPassword.ok) {
            toast.error(firstError(data?.setClusterAuthUserPassword, "Couldn't set the password."));
            return false;
          }
          toast.success("Password set.");
          return true;
        }}
        onReset={async () => {
          if (!passwordFor) return false;
          const { data } = await resetPassword({
            variables: { input: { clusterId, username: passwordFor.username } },
          });
          if (!data?.resetClusterAuthUserPassword.ok) {
            toast.error(firstError(data?.resetClusterAuthUserPassword, "Couldn't send the reset."));
            return false;
          }
          toast.success("Reset code sent to the user.");
          return true;
        }}
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
          const { data } = await deleteUser({
            variables: { input: { clusterId, username: deleting.username } },
          });
          if (!data?.deleteClusterAuthUser.ok) {
            throw new Error(firstError(data?.deleteClusterAuthUser, "Delete failed."));
          }
          toast.success("User deleted.");
        }}
      />
    </Card>
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
  onCreate: (input: {
    email: string;
    password: string | null;
    permanent: boolean;
    groups: string[];
  }) => Promise<boolean>;
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
