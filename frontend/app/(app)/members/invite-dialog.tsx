"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertCircleIcon,
  CheckCircle2Icon,
  CopyIcon,
  InfoIcon,
  MailIcon,
  Trash2Icon,
  UserIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
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
import { CREATE_INVITATION, REVOKE_INVITATION } from "@/graphql/identity/identity.mutations";
import {
  LIST_INVITATIONS,
  LIST_ROLES_I_CAN_GRANT,
  SEARCHABLE_USERS,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftInvitation,
  AstroliftRole,
  AstroliftSearchableUser,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useDebounce } from "@/hooks/use-debounce";

import { InvitationExpiryBadge } from "./invitation-expiry";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface InvitationCreated {
  invitation: AstroliftInvitation;
  plaintextToken: string;
  acceptUrlPath: string;
}

const SEARCH_DEBOUNCE_MS = 300;
const MIN_SEARCH_LENGTH = 2;

export function InviteDialog({ open, onOpenChange }: Props) {
  const t = useTranslations("lists.inviteDialog");
  const tExpiry = useTranslations("lists.invitationExpiry");
  const [email, setEmail] = React.useState("");
  const [roleSlug, setRoleSlug] = React.useState<string>("");
  const [expiresInDays, setExpiresInDays] = React.useState<string>("7");

  // After a successful create the dialog flips to a "share this link"
  // view. The plaintext token is the only chance — we render it once
  // with a Copy button and surface the accept URL the recipient
  // clicks.
  const [created, setCreated] = React.useState<InvitationCreated | null>(null);

  const debouncedEmail = useDebounce(email, SEARCH_DEBOUNCE_MS);
  const searchQuery = debouncedEmail.trim();
  const shouldSearch = searchQuery.length >= MIN_SEARCH_LENGTH && created === null;

  const search = useQuery<{ astroliftSearchableUsers: AstroliftSearchableUser[] }>(
    SEARCHABLE_USERS,
    {
      variables: { query: searchQuery },
      skip: !shouldSearch,
      fetchPolicy: "cache-and-network",
    }
  );

  // Roles list is fetched here (instead of being passed in) so the
  // dialog ships its own perm-gated list (#418). The members surface
  // still uses the broader astroliftRoles query for the role-binding
  // tab; the dialog deliberately uses the narrower one because the
  // server would reject a grant attempt for an out-of-scope role
  // anyway and surfacing it in the picker is misleading.
  const roles = useQuery<{ astroliftRolesICanGrant: AstroliftRole[] }>(LIST_ROLES_I_CAN_GRANT, {
    fetchPolicy: "cache-and-network",
    skip: !open,
  });

  const [createInvite, { loading }] = useMutation<{
    createInvitation: MutationResult<InvitationCreated>;
  }>(CREATE_INVITATION, {
    refetchQueries: [{ query: LIST_INVITATIONS }],
    awaitRefetchQueries: true,
  });

  const [revokeInvite, { loading: revoking }] = useMutation<{
    revokeInvitation: MutationResult<AstroliftInvitation>;
  }>(REVOKE_INVITATION, {
    refetchQueries: [
      { query: LIST_INVITATIONS },
      { query: SEARCHABLE_USERS, variables: { query: searchQuery } },
    ],
    awaitRefetchQueries: true,
  });

  React.useEffect(() => {
    if (!open) {
      setEmail("");
      setRoleSlug("");
      setExpiresInDays("7");
      setCreated(null);
    }
  }, [open]);

  const grantableRoles = React.useMemo(() => {
    const list = roles.data?.astroliftRolesICanGrant ?? [];
    return list.filter((r) => r.scopeLevel === "ORG");
  }, [roles.data]);

  const matches = search.data?.astroliftSearchableUsers ?? [];
  const memberMatch = matches.find(
    (m) => m.matchKind === "MEMBER" && m.email.toLowerCase() === email.trim().toLowerCase()
  );
  const invitationMatch = matches.find(
    (m) => m.matchKind === "INVITATION" && m.email.toLowerCase() === email.trim().toLowerCase()
  );

  // A pending invitation for the exact same email blocks submission;
  // resending is a separate flow (revoke + re-invite). A member match
  // is informational only (the operator may still want to upgrade
  // their role via Grant role); we surface the badge but leave the
  // form enabled so the user explicitly cancels if needed.
  const blocksSubmit = invitationMatch !== undefined;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim()) {
      toast.error(t("toasts.emailRequired"));
      return;
    }
    if (blocksSubmit) {
      toast.error(t("toasts.invitationExists"));
      return;
    }
    const days = parseInt(expiresInDays, 10);
    const { data } = await createInvite({
      variables: {
        input: {
          email: email.trim(),
          roleSlug: roleSlug || null,
          expiresInDays: Number.isFinite(days) && days > 0 ? days : null,
        },
      },
    });
    const result = data?.createInvitation;
    if (result?.ok && result.data) {
      toast.success(t("toasts.created"));
      setCreated(result.data);
    } else {
      toast.error(result?.errors?.[0]?.message ?? t("toasts.failed"));
    }
  }

  async function handleRevokeAndReinvite() {
    if (!invitationMatch?.invitationId) return;
    const { data } = await revokeInvite({
      variables: { input: { id: invitationMatch.invitationId } },
    });
    if (data?.revokeInvitation.ok) {
      toast.success(t("toasts.cancelledForReinvite"));
      // Refetch the search so the now-revoked invitation drops out
      // and the form unblocks.
      await search.refetch?.();
    } else {
      toast.error(data?.revokeInvitation.errors?.[0]?.message ?? t("toasts.cancelFailed"));
    }
  }

  function copyAcceptUrl() {
    if (!created) return;
    const url = buildAcceptUrlWithHint(created);
    navigator.clipboard
      .writeText(url)
      .then(() => toast.success(t("toasts.linkCopied")))
      .catch(() => toast.error(t("toasts.copyFailed")));
  }

  const noGrantableRoles =
    !roles.loading && (roles.data?.astroliftRolesICanGrant ?? []).length === 0;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        {created ? (
          <div className="space-y-4 px-4 py-6">
            <div className="rounded-md border border-warning-border bg-warning/10 p-3 text-sm">
              <strong className="mb-1 block">{t("reveal.saveTitle")}</strong>
              {t("reveal.saveDescription")}
            </div>
            <div className="space-y-1">
              <Label className="text-xs tracking-wide uppercase">{t("reveal.recipient")}</Label>
              <div className="font-mono text-sm">{created.invitation.email}</div>
            </div>
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <Label className="text-xs tracking-wide uppercase">{t("reveal.expiresIn")}</Label>
                <InvitationExpiryBadge expiresAt={created.invitation.expiresAt} />
              </div>
            </div>
            <div className="space-y-1">
              <Label className="text-xs tracking-wide uppercase">{t("reveal.acceptLink")}</Label>
              <div className="bg-muted flex items-center gap-2 rounded-md p-2">
                <code className="flex-1 font-mono text-xs break-all">
                  {typeof window !== "undefined" ? buildAcceptUrlWithHint(created) : ""}
                </code>
                <Button size="sm" variant="outline" onClick={copyAcceptUrl} className="shrink-0">
                  <CopyIcon className="size-3" /> {t("reveal.copy")}
                </Button>
              </div>
              <p className="text-muted-foreground text-xs">{t("reveal.expHint")}</p>
            </div>
            <SheetFooter>
              <Button onClick={() => onOpenChange(false)}>{t("reveal.done")}</Button>
            </SheetFooter>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4 px-4 py-6">
            <div className="space-y-1">
              <Label htmlFor="invite-email">{t("form.email")}</Label>
              <Input
                id="invite-email"
                type="email"
                placeholder={t("form.emailPlaceholder")}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoFocus
              />
              <p className="text-muted-foreground text-xs">{t("form.emailHint")}</p>
              <SearchMatchPanel
                searchQueryActive={shouldSearch}
                isLoading={search.loading && !search.data}
                memberMatch={memberMatch}
                invitationMatch={invitationMatch}
                onCancelInvitation={handleRevokeAndReinvite}
                revoking={revoking}
                tExpiry={tExpiry}
                t={t}
              />
            </div>
            <div className="space-y-1">
              <Label>{t("form.role")}</Label>
              <Select
                value={roleSlug}
                onValueChange={setRoleSlug}
                disabled={roles.loading || noGrantableRoles}
              >
                <SelectTrigger>
                  <SelectValue placeholder={t("form.rolePlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  {grantableRoles.map((r) => (
                    <SelectItem key={r.id} value={r.slug}>
                      {r.name} ({r.slug})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {noGrantableRoles ? (
                <p className="text-destructive flex items-start gap-1.5 text-xs">
                  <AlertCircleIcon className="mt-0.5 size-3.5 shrink-0" />
                  {t("form.noGrantableRoles")}
                </p>
              ) : grantableRoles.length > 0 ? (
                <p className="text-muted-foreground text-xs">
                  {t("form.roleHint", {
                    list: grantableRoles
                      .slice(0, 4)
                      .map((r) => r.name)
                      .join(", "),
                  })}
                </p>
              ) : (
                <p className="text-muted-foreground text-xs">{t("form.roleHintGeneric")}</p>
              )}
            </div>
            <div className="space-y-1">
              <Label htmlFor="invite-expires">{t("form.expiresInDays")}</Label>
              <Input
                id="invite-expires"
                type="number"
                min={1}
                max={90}
                value={expiresInDays}
                onChange={(e) => setExpiresInDays(e.target.value)}
              />
            </div>
            <SheetFooter>
              <Button type="submit" disabled={loading || blocksSubmit || noGrantableRoles}>
                <MailIcon className="size-4" /> {t("form.issue")}
              </Button>
            </SheetFooter>
          </form>
        )}
      </SheetContent>
    </Sheet>
  );
}

type DialogTranslator = (k: string, v?: Record<string, string | number | Date>) => string;

interface SearchMatchPanelProps {
  searchQueryActive: boolean;
  isLoading: boolean;
  memberMatch: AstroliftSearchableUser | undefined;
  invitationMatch: AstroliftSearchableUser | undefined;
  onCancelInvitation: () => Promise<void>;
  revoking: boolean;
  tExpiry: DialogTranslator;
  t: DialogTranslator;
}

function SearchMatchPanel({
  searchQueryActive,
  isLoading,
  memberMatch,
  invitationMatch,
  onCancelInvitation,
  revoking,
  t,
}: SearchMatchPanelProps) {
  if (!searchQueryActive) return null;
  if (isLoading) {
    return (
      <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
        <InfoIcon className="size-3.5" />
        {t("search.loading")}
      </p>
    );
  }
  if (!memberMatch && !invitationMatch) {
    return null;
  }
  return (
    <div className="border-border space-y-2 rounded-md border px-3 py-2 text-sm">
      {memberMatch ? (
        <div className="flex items-start gap-2">
          <Avatar size="sm">
            {memberMatch.avatarUrl ? (
              <AvatarImage src={memberMatch.avatarUrl} alt={memberMatch.displayLabel} />
            ) : null}
            <AvatarFallback>
              <UserIcon className="size-3" />
            </AvatarFallback>
          </Avatar>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <CheckCircle2Icon className="size-3.5 text-success-fg" />
              <span className="font-medium">{t("search.alreadyMember")}</span>
            </div>
            <div className="text-muted-foreground truncate text-xs">
              {memberMatch.displayLabel} · {memberMatch.email}
            </div>
            <p className="text-muted-foreground mt-1 text-xs">{t("search.alreadyMemberHint")}</p>
          </div>
        </div>
      ) : null}
      {invitationMatch ? (
        <div className="flex items-start gap-2">
          <Avatar size="sm">
            <AvatarFallback>
              <MailIcon className="size-3" />
            </AvatarFallback>
          </Avatar>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <AlertCircleIcon className="size-3.5 text-warning-fg" />
              <span className="font-medium">{t("search.invitationExists")}</span>
              {invitationMatch.expiresAt ? (
                <InvitationExpiryBadge expiresAt={invitationMatch.expiresAt} />
              ) : null}
            </div>
            <div className="text-muted-foreground truncate text-xs">{invitationMatch.email}</div>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={revoking}
                onClick={() => void onCancelInvitation()}
              >
                <Trash2Icon className="size-3.5" /> {t("search.cancelAndReinvite")}
              </Button>
            </div>
            <p className="text-muted-foreground mt-1 text-xs">{t("search.invitationHint")}</p>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Wraps the plaintext accept-url path with the absolute origin and
 * appends ``?exp=<unix>`` as a soft client-side hint (#418). The
 * server still authoritatively checks expiry on accept; the query
 * param is purely an affordance so a link-handler can present a
 * sensible "this link expired" message without hitting the API.
 */
function buildAcceptUrlWithHint(created: InvitationCreated): string {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const expSec = Math.floor(new Date(created.invitation.expiresAt).getTime() / 1000);
  if (!Number.isFinite(expSec) || expSec <= 0) {
    return `${origin}${created.acceptUrlPath}`;
  }
  const join = created.acceptUrlPath.includes("?") ? "&" : "?";
  return `${origin}${created.acceptUrlPath}${join}exp=${expSec}`;
}
