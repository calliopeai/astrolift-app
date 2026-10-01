"use client";

import { KeyRoundIcon, PlusIcon, Trash2Icon, UsersIcon } from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";

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

import { localizedAuthUsersList, AUTH_USERS_SELECT } from "./auth-users-list";
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
  sourceKey,
  view,
  loading,
  error,
  onRetry,
  onSetGroups,
  onCreateGroup,
  onToggleEnabled,
  onCreate,
  onSetPassword,
  onResetPassword,
  onDelete,
}: AuthUsersViewProps) {
  const t = useTranslations("clusterSettings.authUsers");
  const [creating, setCreating] = React.useState(false);
  const [passwordFor, setPasswordFor] = React.useState<AstroliftClusterAuthUser | null>(null);
  const [deleting, setDeleting] = React.useState<AstroliftClusterAuthUser | null>(null);
  const list = useLocalListState(localizedAuthUsersList(t));
  const [reviewedSource, setReviewedSource] = React.useState(sourceKey);
  if (reviewedSource !== sourceKey) {
    setReviewedSource(sourceKey);
    setCreating(false);
    setPasswordFor(null);
    setDeleting(null);
  }
  const lease = React.useMemo(() => ({ sourceKey }), [sourceKey]);
  const current = React.useRef<typeof lease | null>(lease);
  React.useLayoutEffect(() => {
    current.current = lease;
    return () => {
      current.current = null;
    };
  }, [lease]);
  const sourceFailure = error ? (
    <div role="alert" className="space-y-2">
      <p>{t("readFailed")}</p>
      {view && <p>{t("cached")}</p>}
      <pre className="font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">{error}</pre>
      <Button variant="outline" size="sm" disabled={loading} onClick={onRetry}>
        {t("retry")}
      </Button>
    </div>
  ) : null;

  if (loading && !view) {
    return (
      <Section title={t("title")} divided>
        <Skeleton className="h-24 w-full" />
      </Section>
    );
  }
  if (!view)
    return (
      <Section title={t("title")} divided>
        {sourceFailure ?? (
          <div className="space-y-2">
            <p>{t("unknownSource")}</p>
            <Button size="sm" variant="outline" onClick={onRetry}>
              {t("retry")}
            </Button>
          </div>
        )}
      </Section>
    );

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
      header: t("user"),
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
      header: t("status"),
      sortKey: "status",
      cell: (u) => (
        <div className="flex flex-wrap gap-1">
          <Badge variant={u.enabled ? "secondary" : "outline"}>
            {t(u.enabled ? "states.enabled" : "states.disabled")}
          </Badge>
          <span className="text-muted-foreground text-2xs font-mono" title={u.status}>
            {t.has(`providerStatus.${u.status}`) ? t(`providerStatus.${u.status}`) : u.status}
          </span>
        </div>
      ),
    },
    {
      id: "groups",
      header: t("groups"),
      cell: (u) => (
        <GroupEditor
          key={`${sourceKey}:${u.username}`}
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
          {t("title")}
          {view.provider && (
            <Badge variant="outline" className="text-2xs font-normal">
              {view.provider}
            </Badge>
          )}
        </span>
      }
      description={
        <>
          {t("description")}{" "}
          {view.reachNote ===
          "A user of this pool can sign in to every app on the cluster that has no access rule of its own."
            ? t("reachNote")
            : view.reachNote}
        </>
      }
      action={
        view.supported && (
          <Button size="sm" onClick={() => setCreating(true)}>
            <PlusIcon className="size-4" />
            {t("addUser")}
          </Button>
        )
      }
      divided
    >
      <div className="min-w-0 space-y-3">
        {sourceFailure}
        <p className="text-muted-foreground text-xs">{t("inventoryLimit")}</p>
        <p className="text-muted-foreground text-xs">{t("operationLimit")}</p>
        {!view.supported ? (
          <div className="space-y-2">
            <p>{t("unsupported")}</p>
            <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">{view.reason}</p>
            <Button size="sm" variant="outline" disabled={loading} onClick={onRetry}>
              {t("retry")}
            </Button>
          </div>
        ) : (
          <>
            <ListPage<AstroliftClusterAuthUser>
              embedded
              list={list}
              label={t("title")}
              columns={columns}
              rows={page.rows}
              getRowId={(u) => u.username}
              rowActions={(u) => (
                <>
                  <DropdownMenuItem onSelect={() => setPasswordFor({ ...u })}>
                    <KeyRoundIcon className="size-4" />
                    {t("password")}
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => void onToggleEnabled(u)}>
                    {t(u.enabled ? "disable" : "enable")}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive" onSelect={() => setDeleting({ ...u })}>
                    <Trash2Icon className="size-4" />
                    {t("deleteName", { name: u.email || u.username })}
                  </DropdownMenuItem>
                </>
              )}
              totalCount={page.totalCount}
              empty={{ icon: <UsersIcon className="size-5" />, title: t("noUsers") }}
            />
          </>
        )}
      </div>

      <CreateUserDialog
        key={`create:${sourceKey}`}
        open={creating}
        onOpenChange={(open) => {
          if (current.current === lease) setCreating(open);
        }}
        groups={view.groups}
        onCreate={(input) => (current.current === lease ? onCreate(input) : Promise.resolve(false))}
      />

      <PasswordDialog
        key={`password:${sourceKey}`}
        user={passwordFor}
        onClose={() => setPasswordFor((active) => (active === passwordFor ? null : active))}
        onSet={async (password, permanent) =>
          passwordFor && current.current === lease
            ? onSetPassword(passwordFor.username, password, permanent)
            : false
        }
        onReset={async () =>
          passwordFor && current.current === lease ? onResetPassword(passwordFor.username) : false
        }
      />

      <ConfirmDialog
        key={`delete:${sourceKey}`}
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting((active) => (active === deleting ? null : active));
        }}
        title={t("deleteTitle", { name: deleting?.email || deleting?.username || "" })}
        description={t("deleteDescription")}
        confirmLabel={t("deleteUser")}
        destructive
        onConfirm={async () => {
          if (!deleting || current.current !== lease) return false;
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
  onChange: (add: string[], remove: string[]) => Promise<boolean>;
  onCreateGroup: (name: string) => Promise<boolean>;
}) {
  const t = useTranslations("clusterSettings.authUsers");
  const [adding, setAdding] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const others = groups.filter((g) => !user.groups.includes(g));
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1">
      {user.groups.map((g) => (
        <Badge key={g} variant="secondary" className="gap-1">
          {g}
          <button
            type="button"
            className="hover:text-destructive"
            aria-label={t("removeGroup", { group: g })}
            disabled={busy}
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
          if (!name || busy) return;
          setBusy(true);
          try {
            if (!groups.includes(name) && !(await onCreateGroup(name))) return;
            if (await onChange([name], [])) setAdding("");
          } finally {
            setBusy(false);
          }
        }}
      >
        <Input
          aria-label={t("addToGroup", { name: user.email || user.username })}
          list={`groups-${user.username}`}
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
          placeholder={t("addGroup")}
          disabled={busy}
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
  const t = useTranslations("clusterSettings.authUsers");
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
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!busy) {
          if (o) onOpenChange(true);
          else close();
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("createTitle")}</DialogTitle>
          <DialogDescription>{t("createDescription")}</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            if (busy) return;
            setBusy(true);
            try {
              if (
                await onCreate({
                  email: email.trim(),
                  password: password || null,
                  permanent: Boolean(password) && permanent,
                  groups: chosen,
                })
              )
                close();
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="auth-user-email">{t("email")}</Label>
            <Input
              id="auth-user-email"
              disabled={busy}
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="auth-user-password">{t("optionalPassword")}</Label>
            <Input
              id="auth-user-password"
              disabled={busy}
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {password && (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                disabled={busy}
                checked={permanent}
                onCheckedChange={(v) => setPermanent(v === true)}
              />
              {t("createPermanent")}
            </label>
          )}
          {groups.length > 0 && (
            <fieldset className="space-y-1.5">
              <legend className="text-sm font-medium">{t("groups")}</legend>
              <div className="flex flex-wrap gap-3">
                {groups.map((g) => (
                  <label key={g} className="flex items-center gap-1.5 text-sm">
                    <Checkbox
                      disabled={busy}
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
            <Button type="button" variant="outline" onClick={close} disabled={busy}>
              {t("cancel")}
            </Button>
            <Button type="submit" disabled={busy || !email.trim()}>
              {t(busy ? "adding" : "addUser")}
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
  const t = useTranslations("clusterSettings.authUsers");
  const [reviewedUser, setReviewedUser] = React.useState(user);
  const [password, setPassword] = React.useState("");
  const [permanent, setPermanent] = React.useState(true);
  const [busy, setBusy] = React.useState(false);
  if (reviewedUser !== user) {
    setReviewedUser(user);
    setPassword("");
    setPermanent(true);
  }

  const lease = React.useMemo(() => ({ user }), [user]);
  const current = React.useRef<typeof lease | null>(lease);
  React.useLayoutEffect(() => {
    current.current = lease;
    return () => {
      current.current = null;
    };
  }, [lease]);
  function close() {
    if (current.current !== lease) return;
    setPassword("");
    setPermanent(true);
    onClose();
  }

  return (
    <Dialog
      open={user !== null}
      onOpenChange={(o) => {
        if (!o && !busy) close();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {t("passwordTitle", { name: user?.email || user?.username || "" })}
          </DialogTitle>
          <DialogDescription>{t("passwordDescription")}</DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={async (e) => {
            e.preventDefault();
            if (busy) return;
            setBusy(true);
            try {
              if (await onSet(password, permanent)) close();
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="auth-user-new-password">{t("newPassword")}</Label>
            <Input
              id="auth-user-new-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              disabled={busy}
              checked={permanent}
              onCheckedChange={(v) => setPermanent(v === true)}
            />
            {t("passwordPermanent")}
          </label>
          <DialogFooter className="gap-2 sm:justify-between">
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={async () => {
                if (busy) return;
                setBusy(true);
                try {
                  if (await onReset()) close();
                } finally {
                  setBusy(false);
                }
              }}
            >
              {t("requestReset")}
            </Button>
            <Button type="submit" disabled={busy || !password}>
              {t("setPassword")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
