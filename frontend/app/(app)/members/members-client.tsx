"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  MailIcon,
  ShieldIcon,
  Trash2Icon,
  UserMinusIcon,
  UserPlusIcon,
  UsersIcon,
} from "lucide-react";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { REVOKE_INVITATION, REVOKE_ROLE_BINDING } from "@/graphql/identity/identity.mutations";
import {
  LIST_INVITATIONS,
  LIST_MEMBERS,
  LIST_PROJECTS,
  LIST_ROLE_BINDINGS,
  LIST_ROLES,
  LIST_TEAMS,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftInvitation,
  AstroliftMember,
  AstroliftProject,
  AstroliftRole,
  AstroliftRoleBinding,
  AstroliftTeam,
  MutationResult,
} from "@/graphql/identity/identity.types";

import { GrantRoleDialog } from "./grant-role-dialog";
import { InviteDialog } from "./invite-dialog";

interface MembersResp {
  astroliftMembers: AstroliftMember[];
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

export function MembersClient() {
  const [open, setOpen] = React.useState(false);
  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [revokeTarget, setRevokeTarget] = React.useState<RevokeTarget | null>(null);
  const members = useQuery<MembersResp>(LIST_MEMBERS);
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
        "anonymizeUser mutation not on main yet — tracked in #312. Re-enable once the backend wiring lands.",
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
  const teamById = new Map(teamList.map((t) => [t.id, t]));
  const projectById = new Map(projectList.map((p) => [p.id, p]));
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

  // Group bindings by user for the role-binding tab.
  const bindingsByUser = new Map<string, AstroliftRoleBinding[]>();
  for (const b of bindingList) {
    if (!b.user) continue;
    const arr = bindingsByUser.get(b.user.id) ?? [];
    arr.push(b);
    bindingsByUser.set(b.user.id, arr);
  }

  return (
    <PageShell
      title="Members"
      description="Users with access to this organization, plus the role bindings that grant their permissions."
      actions={
        <div className="flex items-center gap-2">
          <Can permission="org.manage_members">
            <Button variant="outline" onClick={() => setInviteOpen(true)} disabled={roles.loading}>
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
        <CardHeader>
          <CardTitle>People</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {members.loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : memberList.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<UsersIcon className="size-5" />}
                title="No members"
                description="Members appear here once role bindings are granted to users."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>User</TableHead>
                  <TableHead>Scope</TableHead>
                  <TableHead>Roles</TableHead>
                  <TableHead>Lifecycle</TableHead>
                  <TableHead>Joined</TableHead>
                  <TableHead className="w-12 text-right"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {memberList.map((m) => {
                  const userBindings = bindingsByUser.get(m.user.id) ?? [];
                  const alreadyAnonymized = m.lifecycle === "anonymized";
                  return (
                    <TableRow key={m.id}>
                      <TableCell>
                        <div className="font-medium">{m.user.username}</div>
                        <div className="text-muted-foreground text-xs">{m.user.email}</div>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-col items-start gap-1">
                          <Badge className={scopeBadge[m.scopeKind]} variant="secondary">
                            {m.scopeKind}
                          </Badge>
                          <span className="text-muted-foreground font-mono text-[10px]">
                            {scopeLabel(m)}
                          </span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {userBindings.length === 0 ? (
                            <span className="text-muted-foreground text-xs">—</span>
                          ) : (
                            userBindings.map((b) => (
                              <Badge key={b.id} variant="outline" className="font-mono text-xs">
                                {b.role.slug}
                              </Badge>
                            ))
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={m.isActive ? "default" : "secondary"}>{m.lifecycle}</Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {m.joinedAt
                          ? new Date(m.joinedAt).toLocaleDateString()
                          : new Date(m.createdAt).toLocaleDateString()}
                      </TableCell>
                      <TableCell className="text-right">
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
                  <TableHead>Subject</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Scope</TableHead>
                  <TableHead>Granted</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {bindingList.map((b) => (
                  <TableRow key={b.id}>
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
                      <Badge className={scopeBadge[b.scopeKind]} variant="secondary">
                        {b.scopeKind}
                      </Badge>
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
                          disabled={revoking}
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
          ) : (invitations.data?.astroliftInvitations ?? []).length === 0 ? (
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
                {(invitations.data?.astroliftInvitations ?? []).map((inv) => (
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
                    <TableCell className="text-muted-foreground text-sm">
                      {new Date(inv.expiresAt).toLocaleString()}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {inv.invitedByUsername ?? "—"}
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

      <GrantRoleDialog
        open={open}
        onOpenChange={setOpen}
        roles={roles.data?.astroliftRoles ?? []}
      />
      <InviteDialog
        open={inviteOpen}
        onOpenChange={setInviteOpen}
        roles={roles.data?.astroliftRoles ?? []}
      />

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
      const message =
        err instanceof Error && err.message ? err.message : "Anonymize failed";
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
                <div className="border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-200 flex items-start gap-2 rounded-md border p-3 text-xs">
                  <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
                  <div>
                    <p className="font-medium">Backend wiring pending</p>
                    <p className="mt-0.5">
                      The Anonymize button is disabled until the
                      <code className="bg-amber-500/10 mx-1 rounded px-1 font-mono">
                        anonymizeUser
                      </code>
                      mutation lands. The flow, copy, and double-confirm
                      below are reviewable; tracking under{" "}
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
                <p className="text-foreground mb-1.5 text-xs font-medium uppercase tracking-wide">
                  This will scrub
                </p>
                <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-xs">
                  <li>
                    <code className="bg-muted rounded px-1 font-mono">email</code>{" "}
                    → SHA-256 hash, not reversible
                  </li>
                  <li>
                    First and last name → <code className="bg-muted rounded px-1 font-mono">[redacted]</code>
                  </li>
                  <li>Username → deterministic placeholder bound to the user ID</li>
                  <li>Phone, avatar URL → removed</li>
                  <li>Past audit-event payloads → IP, user-agent, email scrubbed in place</li>
                </ul>
              </div>

              <div>
                <p className="text-foreground mb-1.5 text-xs font-medium uppercase tracking-wide">
                  This preserves
                </p>
                <ul className="text-muted-foreground list-disc space-y-0.5 pl-5 text-xs">
                  <li>
                    Audit-log structural records (timestamps, action types,
                    affected resources)
                  </li>
                  <li>FK relationships from past actions — referential integrity stays intact</li>
                  <li>
                    Member <code className="bg-muted rounded px-1 font-mono">lifecycle</code>{" "}
                    flips to <code className="bg-muted rounded px-1 font-mono">anonymized</code>; role bindings are revoked
                  </li>
                </ul>
              </div>

              <p className="text-destructive font-medium">
                <strong>This action is irreversible.</strong> Re-running it on the same user
                is a no-op; the original PII cannot be restored.
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
