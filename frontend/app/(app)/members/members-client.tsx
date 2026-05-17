"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  InfoIcon,
  MailIcon,
  SearchIcon,
  ShieldIcon,
  Trash2Icon,
  UserMinusIcon,
  UserPlusIcon,
  UsersIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  BULK_REVOKE_ROLE_BINDINGS,
  REVOKE_INVITATION,
  REVOKE_ROLE_BINDING,
} from "@/graphql/identity/identity.mutations";
import {
  LIST_INVITATIONS,
  LIST_MEMBERS,
  LIST_PROJECTS,
  LIST_ROLE_BINDINGS,
  LIST_ROLES,
  LIST_TEAMS,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftBulkRevokeRoleBindingsPayload,
  AstroliftInvitation,
  AstroliftMember,
  AstroliftProject,
  AstroliftRole,
  AstroliftRoleBinding,
  AstroliftTeam,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useDebounce } from "@/hooks/use-debounce";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { GrantRoleDialog } from "./grant-role-dialog";
import { InvitationExpiryBadge } from "./invitation-expiry";
import { InviteDialog } from "./invite-dialog";

interface MembersResp {
  astroliftMembers: AstroliftMember[];
}
interface MembersVars {
  search?: string | null;
}
interface RoleBindingsResp {
  astroliftRoleBindings: AstroliftRoleBinding[];
}
interface RolesResp {
  astroliftRoles: AstroliftRole[];
}
interface InvitationsResp {
  astroliftInvitations: AstroliftInvitation[];
}

const scopeBadge: Record<string, string> = {
  ORG: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  TEAM: "bg-purple-500/15 text-purple-700 dark:text-purple-300",
  PROJECT: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  APP: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
};

type RevokeTarget =
  | { kind: "invitation"; invitation: AstroliftInvitation }
  | { kind: "binding"; binding: AstroliftRoleBinding };

const STALE_THRESHOLD_DAYS = 90;

export function MembersClient() {
  const t = useTranslations("orgMembers");
  // Bulk-revoke (#416) lives in its own i18n namespace so the team-
  // member bulk surface can reuse a sibling key set without
  // overloading orgMembers.
  const tBulk = useTranslations("lists.membersBulk");
  const perms = useMyPermissions();
  const canManageMembers = perms.can("org.manage_members");

  const [open, setOpen] = React.useState(false);
  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [revokeTarget, setRevokeTarget] = React.useState<RevokeTarget | null>(null);
  // Deep-link grant: when set, the GrantRoleDialog opens pre-populated
  // with this member's user PK so the operator skips the user picker.
  // The dialog calls onOpenChange(false) on submit/cancel, which clears
  // this back to null via the wrapper handler below.
  const [grantForMember, setGrantForMember] = React.useState<AstroliftMember | null>(null);

  // Bulk-revoke selection state (#416). A Set of binding GUIDs the
  // operator has checked; cleared on success so the footer disappears.
  const [selectedBindings, setSelectedBindings] = React.useState<Set<string>>(() => new Set());
  const [confirmBulkRevoke, setConfirmBulkRevoke] = React.useState(false);

  // A. Search affordance. 200ms debounce matches the issue spec; the
  // debounced value is what the query keys off, so typing fast doesn't
  // hammer the resolver.
  const [searchInput, setSearchInput] = React.useState("");
  const debouncedSearch = useDebounce(searchInput, 200);
  const searchVariable: MembersVars = debouncedSearch.trim()
    ? { search: debouncedSearch.trim() }
    : {};

  const members = useQuery<MembersResp, MembersVars>(LIST_MEMBERS, {
    variables: searchVariable,
    fetchPolicy: "cache-and-network",
  });
  const bindings = useQuery<RoleBindingsResp>(LIST_ROLE_BINDINGS);
  const roles = useQuery<RolesResp>(LIST_ROLES);
  const invitations = useQuery<InvitationsResp>(LIST_INVITATIONS);
  // Loaded so the Scope column can resolve `(scopeKind=TEAM, scopeId=N)`
  // into a human-readable team / project name instead of the bare
  // "TEAM" badge that previously made it ambiguous whether the column
  // showed a role or a scope.
  const teams = useQuery<{ astroliftTeams: AstroliftTeam[] }>(LIST_TEAMS);
  const projects = useQuery<{ astroliftProjects: AstroliftProject[] }>(LIST_PROJECTS);

  const [revokeBinding, { loading: revoking }] = useMutation<{
    revokeRoleBinding: MutationResult<{ id: string; deleted: boolean }>;
  }>(REVOKE_ROLE_BINDING, {
    refetchQueries: [{ query: LIST_ROLE_BINDINGS }],
    awaitRefetchQueries: true,
  });
  const [bulkRevoke, { loading: bulkRevoking }] = useMutation<{
    bulkRevokeAstroliftRoleBindings: MutationResult<AstroliftBulkRevokeRoleBindingsPayload>;
  }>(BULK_REVOKE_ROLE_BINDINGS, {
    refetchQueries: [{ query: LIST_ROLE_BINDINGS }, { query: LIST_MEMBERS }],
    awaitRefetchQueries: true,
  });
  const [revokeInvite, { loading: revokingInvite }] = useMutation<{
    revokeInvitation: MutationResult<AstroliftInvitation>;
  }>(REVOKE_INVITATION, {
    refetchQueries: [{ query: LIST_INVITATIONS }],
    awaitRefetchQueries: true,
  });

  async function handleRevokeInvite(inv: AstroliftInvitation) {
    const { data } = await revokeInvite({
      variables: { input: { id: inv.id } },
    });
    if (data?.revokeInvitation.ok) {
      toast.success("Invitation revoked");
    } else {
      throw new Error(data?.revokeInvitation.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  async function handleRevoke(rb: AstroliftRoleBinding) {
    const { data } = await revokeBinding({ variables: { input: { id: rb.id } } });
    if (data?.revokeRoleBinding.ok) {
      toast.success("Role revoked");
    } else {
      throw new Error(data?.revokeRoleBinding.errors?.[0]?.message ?? "Revoke failed");
    }
  }

  function toggleBinding(id: string) {
    setSelectedBindings((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAllBindings(visibleIds: string[]) {
    setSelectedBindings((prev) => {
      const allSelected = visibleIds.length > 0 && visibleIds.every((id) => prev.has(id));
      if (allSelected) return new Set();
      return new Set(visibleIds);
    });
  }

  async function handleBulkRevoke() {
    const ids = Array.from(selectedBindings);
    if (ids.length === 0) return;
    try {
      const { data } = await bulkRevoke({
        variables: { input: { bindingIds: ids } },
      });
      const env = data?.bulkRevokeAstroliftRoleBindings;
      if (!env?.ok || !env.data) {
        toast.error(
          tBulk("toasts.allFailed", {
            message: env?.errors?.[0]?.message ?? "unknown error",
          })
        );
        return;
      }
      const { revokedCount, failedCount } = env.data;
      if (failedCount === 0) {
        toast.success(tBulk("toasts.allOk", { count: revokedCount }));
      } else {
        toast.warning(
          tBulk("toasts.partial", {
            revoked: revokedCount,
            failed: failedCount,
          })
        );
      }
      setSelectedBindings(new Set());
    } finally {
      setConfirmBulkRevoke(false);
    }
  }

  // Right-to-delete (GDPR) — anonymize a user's PII while preserving
  // audit-log structural records. The backend mutation tracked in #312
  // is not on main yet; the affordance ships gated + disabled so the
  // permission gate, copy, and double-confirm flow are reviewable. The
  // `coming soon` banner inside the dialog makes the gap explicit.
  const ANONYMIZE_BACKEND_READY = false;
  const [anonymizeTarget, setAnonymizeTarget] = React.useState<AstroliftMember | null>(null);
  const [anonymizeAcknowledged, setAnonymizeAcknowledged] = React.useState(false);

  function openAnonymizeDialog(m: AstroliftMember) {
    setAnonymizeAcknowledged(false);
    setAnonymizeTarget(m);
  }

  async function handleAnonymize(_m: AstroliftMember) {
    if (!ANONYMIZE_BACKEND_READY) {
      throw new Error(
        "anonymizeUser mutation not on main yet — tracked in #312. Re-enable once the backend wiring lands."
      );
    }
    // Wiring placeholder. When #312 lands:
    //   const { data } = await anonymizeUser({
    //     variables: { input: { userGid: m.user.id } },
    //   });
    //   if (!data?.anonymizeUser.ok) {
    //     throw new Error(data?.anonymizeUser.errors?.[0]?.message ?? "Anonymize failed");
    //   }
    //   toast.success("User data anonymized.");
  }

  const memberList = members.data?.astroliftMembers ?? [];
  const bindingList = bindings.data?.astroliftRoleBindings ?? [];
  const teamList = teams.data?.astroliftTeams ?? [];
  const projectList = projects.data?.astroliftProjects ?? [];

  // Lookup tables for the Scope column. Pre-existing scopes were
  // rendered as a bare badge ("TEAM" / "PROJECT") that operators
  // read as a role name; surface the actual team / project name
  // alongside.
  const teamById = new Map(teamList.map((team) => [team.id, team]));
  const projectById = new Map(projectList.map((project) => [project.id, project]));
  function scopeLabel(m: AstroliftMember): string {
    if (m.scopeKind === "TEAM") {
      return teamById.get(m.scopeId)?.slug ?? "—";
    }
    if (m.scopeKind === "PROJECT") {
      const p = projectById.get(m.scopeId);
      return p ? `${p.team.slug}/${p.slug}` : "—";
    }
    if (m.scopeKind === "ORG") {
      return "organization";
    }
    return "—";
  }

  // Group bindings by user for the People-row role pills.
  const bindingsByUser = new Map<string, AstroliftRoleBinding[]>();
  for (const b of bindingList) {
    if (!b.user) continue;
    const arr = bindingsByUser.get(b.user.id) ?? [];
    arr.push(b);
    bindingsByUser.set(b.user.id, arr);
  }

  const hasActiveSearch = debouncedSearch.trim().length > 0;
  const invitationList = invitations.data?.astroliftInvitations ?? [];

  return (
    <TooltipProvider>
      <PageShell
        title="Members"
        description="Users with access to this organization, plus the role bindings that grant their permissions."
        actions={
          <div className="flex items-center gap-2">
            <Can permission="org.manage_members">
              <Button variant="outline" onClick={() => setInviteOpen(true)}>
                <MailIcon className="size-4" />
                Invite
              </Button>
            </Can>
            <Can permission="org.manage_members">
              <Button onClick={() => setOpen(true)} disabled={roles.loading}>
                <UserPlusIcon className="size-4" />
                Grant role
              </Button>
            </Can>
          </div>
        }
      >
        <Card>
          <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <CardTitle>People</CardTitle>
            <div className="relative w-full sm:w-72">
              <SearchIcon
                className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2"
                aria-hidden="true"
              />
              <Input
                type="search"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder={t("searchPlaceholder")}
                aria-label={t("searchLabel")}
                className="pl-8"
              />
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {members.loading && memberList.length === 0 ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : memberList.length === 0 ? (
              <div className="p-6">
                {hasActiveSearch ? (
                  <EmptyState
                    icon={<SearchIcon className="size-5" />}
                    title={t("noMatchTitle")}
                    description={t("noMatchDescription", { term: debouncedSearch.trim() })}
                  />
                ) : (
                  <EmptyState
                    icon={<UsersIcon className="size-5" />}
                    title="No members"
                    description="Members appear here once role bindings are granted to users."
                  />
                )}
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>User</TableHead>
                    <TableHead>Scope</TableHead>
                    <TableHead>Roles</TableHead>
                    <TableHead>{t("lastActiveColumn")}</TableHead>
                    <TableHead>Joined</TableHead>
                    <TableHead className="w-12 text-right">{t("actionsColumn")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {memberList.map((m) => {
                    const userBindings = bindingsByUser.get(m.user.id) ?? [];
                    const alreadyAnonymized = m.lifecycle === "anonymized";
                    return (
                      <TableRow key={m.id} id={`u-${m.user.id}`}>
                        <TableCell>
                          <div className="font-medium">{m.user.username}</div>
                          <div className="text-muted-foreground text-xs">{m.user.email}</div>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-col items-start gap-1">
                            <Badge className={scopeBadge[m.scopeKind]} variant="secondary">
                              {m.scopeKind}
                            </Badge>
                            <span className="text-muted-foreground text-xs">{scopeLabel(m)}</span>
                          </div>
                        </TableCell>
                        <TableCell>
                          {userBindings.length === 0 ? (
                            <span className="text-muted-foreground text-xs">—</span>
                          ) : (
                            <div className="flex flex-wrap gap-1">
                              {userBindings.map((b) => (
                                <RoleSourcePill key={b.id} binding={b} />
                              ))}
                            </div>
                          )}
                        </TableCell>
                        <TableCell>
                          <LastActiveCell value={m.lastActiveAt} />
                        </TableCell>
                        <TableCell className="text-muted-foreground text-sm">
                          {m.joinedAt
                            ? new Date(m.joinedAt).toLocaleDateString()
                            : new Date(m.createdAt).toLocaleDateString()}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1">
                            <Can permission="org.manage_members">
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() => setGrantForMember(m)}
                                    aria-label={t("grantRoleRowLabel", {
                                      name: m.user.username,
                                    })}
                                  >
                                    <UserPlusIcon className="size-4" />
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>
                                  {t("grantRoleRowTooltip", { name: m.user.username })}
                                </TooltipContent>
                              </Tooltip>
                            </Can>
                            <Can permission="org.manage_members">
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={alreadyAnonymized}
                                onClick={() => openAnonymizeDialog(m)}
                                aria-label={`Anonymize ${m.user.username}`}
                                title={
                                  alreadyAnonymized
                                    ? "Already anonymized"
                                    : "Anonymize user data (GDPR right-to-delete)"
                                }
                              >
                                <UserMinusIcon className="size-4" />
                              </Button>
                            </Can>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <CardTitle>Role bindings</CardTitle>
            <span className="text-muted-foreground text-xs">
              {bindingList.length} binding{bindingList.length === 1 ? "" : "s"}
            </span>
          </CardHeader>
          <CardContent className="p-0">
            {bindings.loading ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : bindingList.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={<ShieldIcon className="size-5" />}
                  title="No role bindings"
                  description="Grant a system role to a user to give them access to the platform."
                />
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    {canManageMembers && (
                      <TableHead className="w-10">
                        <input
                          type="checkbox"
                          aria-label={tBulk("selectAllLabel")}
                          checked={
                            bindingList.length > 0 &&
                            bindingList.every((b) => selectedBindings.has(b.id))
                          }
                          onChange={() => toggleAllBindings(bindingList.map((b) => b.id))}
                          className="size-4"
                          disabled={bulkRevoking}
                        />
                      </TableHead>
                    )}
                    <TableHead>Subject</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead>{t("sourceColumn")}</TableHead>
                    <TableHead>Granted</TableHead>
                    <TableHead className="text-right">{t("actionsColumn")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {bindingList.map((b) => (
                    <TableRow key={b.id}>
                      {canManageMembers && (
                        <TableCell>
                          <input
                            type="checkbox"
                            aria-label={tBulk("selectRowLabel", {
                              role: b.role.slug,
                              subject: b.user?.username ?? `group:${b.groupExternalId}`,
                            })}
                            checked={selectedBindings.has(b.id)}
                            onChange={() => toggleBinding(b.id)}
                            className="size-4"
                            disabled={bulkRevoking}
                          />
                        </TableCell>
                      )}
                      <TableCell>
                        {b.user ? (
                          <>
                            <div className="font-medium">{b.user.username}</div>
                            <div className="text-muted-foreground text-xs">{b.user.email}</div>
                          </>
                        ) : (
                          <div className="font-mono text-xs">group:{b.groupExternalId}</div>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="font-medium">{b.role.name}</div>
                        <div className="text-muted-foreground font-mono text-xs">{b.role.slug}</div>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1.5">
                          <Badge className={scopeBadge[b.scopeKind]} variant="secondary">
                            {b.scopeKind}
                          </Badge>
                          {b.sourceScopeLabel && (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <button
                                  type="button"
                                  className="text-muted-foreground hover:text-foreground inline-flex"
                                  aria-label={t("sourceTooltipAria", {
                                    scope: b.sourceScopeLabel,
                                  })}
                                >
                                  <InfoIcon className="size-3.5" />
                                </button>
                              </TooltipTrigger>
                              <TooltipContent>
                                {t("rolePillTooltipPrefix", { scope: b.sourceScopeLabel })}
                              </TooltipContent>
                            </Tooltip>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {new Date(b.grantedAt).toLocaleDateString()}
                      </TableCell>
                      <TableCell className="text-right">
                        <Can permission="org.manage_members">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setRevokeTarget({ kind: "binding", binding: b })}
                            disabled={revoking || bulkRevoking}
                          >
                            <Trash2Icon className="size-4" />
                            <span className="sr-only">Revoke</span>
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

        <Card>
          <CardHeader>
            <CardTitle>Invitations</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {invitations.loading ? (
              <div className="space-y-2 p-6">
                <Skeleton className="h-12 w-full" />
              </div>
            ) : invitationList.length === 0 ? (
              <div className="p-6">
                <EmptyState
                  icon={<MailIcon className="size-5" />}
                  title="No invitations"
                  description="Use Invite to send a one-time accept link. Tokens are hashed at rest; the plaintext is shown once at creation."
                />
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Email</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Expires</TableHead>
                    <TableHead>Invited by</TableHead>
                    <TableHead className="w-12"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {invitationList.map((inv) => (
                    <TableRow key={inv.id}>
                      <TableCell className="font-medium">{inv.email}</TableCell>
                      <TableCell>
                        {inv.roleSlug ? (
                          <Badge variant="outline" className="font-mono text-xs">
                            {inv.roleSlug}
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground text-xs">—</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={inv.status === "pending" ? "default" : "secondary"}
                          className="capitalize"
                        >
                          {inv.status}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {inv.status === "pending" ? (
                          <InvitationExpiryBadge expiresAt={inv.expiresAt} />
                        ) : (
                          <span className="text-muted-foreground text-sm">
                            {new Date(inv.expiresAt).toLocaleDateString()}
                          </span>
                        )}
                      </TableCell>
                      <TableCell>
                        <InviterCell invitation={inv} />
                      </TableCell>
                      <TableCell className="text-right">
                        {inv.status === "pending" && (
                          <Can permission="org.manage_members">
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() =>
                                setRevokeTarget({
                                  kind: "invitation",
                                  invitation: inv,
                                })
                              }
                              disabled={revokingInvite}
                            >
                              <Trash2Icon className="size-4" />
                              <span className="sr-only">Revoke</span>
                            </Button>
                          </Can>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        {canManageMembers && selectedBindings.size > 0 && (
          // Sticky bulk action bar — surfaces only while a selection is
          // live. Mirrors the approvals-queue footer so the muscle memory
          // ("checkbox → sticky bar → confirm") transfers across pages.
          <div className="bg-background pointer-events-auto fixed inset-x-0 bottom-0 z-30 border-t shadow-lg">
            <div className="mx-auto flex max-w-5xl flex-col items-stretch gap-2 p-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm font-medium">
                {tBulk("selected", { count: selectedBindings.size })}
              </p>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <Button
                  variant="ghost"
                  onClick={() => setSelectedBindings(new Set())}
                  disabled={bulkRevoking}
                  className="min-h-11 w-full sm:w-auto"
                >
                  {tBulk("clear")}
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => setConfirmBulkRevoke(true)}
                  disabled={bulkRevoking}
                  className="min-h-11 w-full sm:w-auto"
                >
                  <Trash2Icon className="size-4" />
                  {tBulk("revokeButton", { count: selectedBindings.size })}
                </Button>
              </div>
            </div>
          </div>
        )}

        <ConfirmDialog
          open={confirmBulkRevoke}
          onOpenChange={setConfirmBulkRevoke}
          title={tBulk("confirm.title", { count: selectedBindings.size })}
          description={tBulk("confirm.description")}
          confirmLabel={tBulk("confirm.confirmLabel", { count: selectedBindings.size })}
          destructive
          onConfirm={handleBulkRevoke}
        />

        <GrantRoleDialog
          open={open || grantForMember !== null}
          onOpenChange={(next) => {
            setOpen(next);
            if (!next) setGrantForMember(null);
          }}
          roles={roles.data?.astroliftRoles ?? []}
          initialUserId={grantForMember?.user.id ?? null}
          initialUserLabel={grantForMember?.user.username ?? null}
        />
        <InviteDialog open={inviteOpen} onOpenChange={setInviteOpen} />

        <ConfirmDialog
          open={revokeTarget !== null}
          onOpenChange={(next) => {
            if (!next) setRevokeTarget(null);
          }}
          title={
            revokeTarget?.kind === "invitation"
              ? `Revoke invitation for ${revokeTarget.invitation.email}?`
              : revokeTarget?.kind === "binding"
                ? `Revoke ${revokeTarget.binding.role.slug} from ${revokeTarget.binding.user?.username ?? revokeTarget.binding.groupExternalId}?`
                : "Revoke?"
          }
          description={
            revokeTarget?.kind === "invitation"
              ? "The pending invitation link stops working immediately. You can re-send a fresh invitation if needed."
              : "The user loses the permissions this role granted. Other role bindings, if any, remain in effect."
          }
          confirmLabel="Revoke"
          destructive
          onConfirm={async () => {
            if (!revokeTarget) return;
            if (revokeTarget.kind === "invitation") {
              await handleRevokeInvite(revokeTarget.invitation);
            } else {
              await handleRevoke(revokeTarget.binding);
            }
          }}
        />

        <AnonymizeUserDialog
          target={anonymizeTarget}
          onOpenChange={(next) => {
            if (!next) setAnonymizeTarget(null);
          }}
          acknowledged={anonymizeAcknowledged}
          onAcknowledgedChange={setAnonymizeAcknowledged}
          backendReady={ANONYMIZE_BACKEND_READY}
          onConfirm={async () => {
            if (anonymizeTarget) await handleAnonymize(anonymizeTarget);
          }}
        />
      </PageShell>
    </TooltipProvider>
  );
}

/* ---- helper components ---------------------------------------------- */

/**
 * Role pill with a hover tooltip when the binding's source scope label
 * is present. Mirrors the role-bindings table's info-icon affordance so
 * the operator can confirm "why does this user have this role" from
 * either surface without a second click.
 */
function RoleSourcePill({ binding }: { binding: AstroliftRoleBinding }) {
  const t = useTranslations("orgMembers");
  if (!binding.sourceScopeLabel) {
    return (
      <Badge variant="outline" className="font-mono text-xs">
        {binding.role.slug}
      </Badge>
    );
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="outline" className="cursor-help font-mono text-xs">
          {binding.role.slug}
        </Badge>
      </TooltipTrigger>
      <TooltipContent>
        {t("rolePillTooltipPrefix", { scope: binding.sourceScopeLabel })}
      </TooltipContent>
    </Tooltip>
  );
}

const RELATIVE_DIVISIONS: ReadonlyArray<{
  amount: number;
  unit: Intl.RelativeTimeFormatUnit;
}> = [
  { amount: 60, unit: "second" },
  { amount: 60, unit: "minute" },
  { amount: 24, unit: "hour" },
  { amount: 7, unit: "day" },
  { amount: 4.34524, unit: "week" },
  { amount: 12, unit: "month" },
  { amount: Number.POSITIVE_INFINITY, unit: "year" },
];

function formatRelative(iso: string, now: number): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  let duration = (date.getTime() - now) / 1000;
  for (const division of RELATIVE_DIVISIONS) {
    if (Math.abs(duration) < division.amount) {
      return rtf.format(Math.round(duration), division.unit);
    }
    duration /= division.amount;
  }
  return iso;
}

/**
 * Last-active cell: relative time + hover-tooltip with absolute, plus
 * a muted "inactive" chip when older than ``STALE_THRESHOLD_DAYS``.
 * Null values render as "—" rather than "never" so a user with no
 * audit records yet doesn't read as suspicious.
 *
 * The "now" reference is snapshotted at mount via useState's lazy
 * initializer so React's purity rules don't trip on a render-time
 * ``Date.now()`` call. Re-snapshot is fine — even if a row sits on
 * screen for an hour, the "3d ago" vs "3d ago" delta is invisible.
 */
function LastActiveCell({ value }: { value: string | null | undefined }) {
  const t = useTranslations("orgMembers");
  const [now] = React.useState(() => Date.now());
  if (!value) {
    return <span className="text-muted-foreground text-sm">{t("lastActiveNever")}</span>;
  }
  const date = new Date(value);
  const daysAgo = (now - date.getTime()) / (1000 * 60 * 60 * 24);
  const isStale = daysAgo > STALE_THRESHOLD_DAYS;
  return (
    <div className="flex flex-col items-start gap-1">
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="text-foreground cursor-help text-sm">{formatRelative(value, now)}</span>
        </TooltipTrigger>
        <TooltipContent>{date.toLocaleString()}</TooltipContent>
      </Tooltip>
      {isStale && (
        <Badge variant="secondary" className="text-[10px]">
          {t("lastActiveInactive")}
        </Badge>
      )}
    </div>
  );
}

/**
 * Render the "invited by" cell on the invitation row (#418).
 * Falls back to the username (legacy backend) when the richer
 * attribution fields aren't populated yet — keeps the column useful
 * for invitations created before the enrichment landed. When a user
 * id is present, the display name links to the inviter's row in the
 * members list (filtered via the user-id hash anchor so the row
 * scrolls into view).
 */
function InviterCell({ invitation }: { invitation: AstroliftInvitation }) {
  const display = invitation.invitedByDisplayName ?? invitation.invitedByUsername ?? null;
  if (display === null) {
    return <span className="text-muted-foreground text-sm">—</span>;
  }
  const avatarUrl = invitation.invitedByAvatarUrl ?? "";
  const email = invitation.invitedByEmail ?? "";
  const userId = invitation.invitedByUserId ?? null;
  return (
    <div className="flex items-center gap-2">
      <Avatar size="sm">
        {avatarUrl ? <AvatarImage src={avatarUrl} alt={display} /> : null}
        <AvatarFallback>{display.slice(0, 1).toUpperCase()}</AvatarFallback>
      </Avatar>
      <div className="flex min-w-0 flex-col">
        {userId ? (
          <a
            href={`/members#u-${userId}`}
            className="text-foreground truncate text-sm font-medium hover:underline"
            title={email || display}
          >
            {display}
          </a>
        ) : (
          <span className="text-foreground truncate text-sm font-medium" title={email || display}>
            {display}
          </span>
        )}
        {email ? <span className="text-muted-foreground truncate text-xs">{email}</span> : null}
      </div>
    </div>
  );
}

interface AnonymizeUserDialogProps {
  target: AstroliftMember | null;
  onOpenChange: (open: boolean) => void;
  acknowledged: boolean;
  onAcknowledgedChange: (next: boolean) => void;
  backendReady: boolean;
  onConfirm: () => Promise<void>;
}

/**
 * Right-to-delete (GDPR Art. 17) anonymization flow. Double-confirm:
 * the operator must (a) check the "I understand this is irreversible"
 * box before the destructive action button enables, and (b) click that
 * button. The dialog stays open while the mutation is in flight; on
 * error sonner surfaces the message so the operator can retry.
 *
 * The flow is laid out as an explicit list of what gets scrubbed and
 * what stays so the operator can verify the blast radius before
 * acting — `core/anonymization.py` is the source of truth for the
 * field set.
 */
function AnonymizeUserDialog({
  target,
  onOpenChange,
  acknowledged,
  onAcknowledgedChange,
  backendReady,
  onConfirm,
}: AnonymizeUserDialogProps) {
  const [pending, setPending] = React.useState(false);

  async function handleConfirm(e: React.MouseEvent) {
    e.preventDefault();
    if (pending || !acknowledged || !backendReady) return;
    setPending(true);
    try {
      await onConfirm();
      onOpenChange(false);
    } catch (err) {
      const message = err instanceof Error && err.message ? err.message : "Anonymize failed";
      toast.error(message);
    } finally {
      setPending(false);
    }
  }

  const fullName = target?.user.username ?? "";
  const open = target !== null;

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (pending && !next) return;
        onOpenChange(next);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Anonymize {fullName}?</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-4">
              {!backendReady && (
                <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-900 dark:text-amber-200">
                  <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
                  <div>
                    <p className="font-medium">Backend wiring pending</p>
                    <p className="mt-0.5">
                      The Anonymize button is disabled until the
                      <code className="mx-1 rounded bg-amber-500/10 px-1 font-mono">
                        anonymizeUser
                      </code>
                      mutation lands. The flow, copy, and double-confirm below are reviewable;
                      tracking under{" "}
                      <a
                        href="https://github.com/calliopeai/astrolift-app/issues/312"
                        target="_blank"
                        rel="noreferrer"
                        className="underline"
                      >
                        #312
                      </a>
                      .
                    </p>
                  </div>
                </div>
              )}

              <div>
                <p className="text-foreground mb-1.5 text-xs font-medium tracking-wide uppercase">
                  This will scrub
                </p>
                <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-xs">
                  <li>
                    <code className="bg-muted rounded px-1 font-mono">email</code> → SHA-256 hash,
                    not reversible
                  </li>
                  <li>
                    First and last name →{" "}
                    <code className="bg-muted rounded px-1 font-mono">[redacted]</code>
                  </li>
                  <li>Username → deterministic placeholder bound to the user ID</li>
                  <li>Phone, avatar URL → removed</li>
                  <li>Past audit-event payloads → IP, user-agent, email scrubbed in place</li>
                </ul>
              </div>

              <div>
                <p className="text-foreground mb-1.5 text-xs font-medium tracking-wide uppercase">
                  This preserves
                </p>
                <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-xs">
                  <li>
                    Audit-log structural records (timestamps, action types, affected resources)
                  </li>
                  <li>FK relationships from past actions — referential integrity stays intact</li>
                  <li>
                    Member <code className="bg-muted rounded px-1 font-mono">lifecycle</code> flips
                    to <code className="bg-muted rounded px-1 font-mono">anonymized</code>; role
                    bindings are revoked
                  </li>
                </ul>
              </div>

              <p className="text-destructive font-medium">
                <strong>This action is irreversible.</strong> Re-running it on the same user is a
                no-op; the original PII cannot be restored.
              </p>

              <label className="flex items-start gap-2 rounded-md border p-3 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4"
                  checked={acknowledged}
                  onChange={(e) => onAcknowledgedChange(e.target.checked)}
                  disabled={pending}
                />
                <span>I understand this is irreversible.</span>
              </label>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={pending || !acknowledged || !backendReady}
            onClick={handleConfirm}
          >
            {pending ? "Anonymizing…" : "Anonymize user data"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
