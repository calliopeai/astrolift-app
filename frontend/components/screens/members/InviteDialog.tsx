"use client";

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
import type { AstroliftSearchableUser } from "@/graphql/identity/identity.types";

import { InvitationExpiryBadge } from "./InvitationExpiryBadge";
import { useInviteDialog } from "./use-invite-dialog";

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export type InviteSheetProps = DialogProps & ReturnType<typeof useInviteDialog>;

/** Invite by email, with a de-dupe search and a one-time link reveal. Pure view. */
export function InviteSheet({
  open,
  onOpenChange,
  email,
  setEmail,
  roleSlug,
  setRoleSlug,
  expiresInDays,
  setExpiresInDays,
  created,
  acceptUrl,
  searchActive,
  searchLoading,
  memberMatch,
  invitationMatch,
  blocksSubmit,
  grantableRoles,
  rolesLoading,
  noGrantableRoles,
  creating,
  revoking,
  onSubmit,
  onRevokeAndReinvite,
  onCopyAcceptUrl,
}: InviteSheetProps) {
  const t = useTranslations("lists.inviteDialog");
  const tExpiry = useTranslations("lists.invitationExpiry");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    await onSubmit();
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        {created ? (
          <div className="space-y-4 px-4 py-6">
            <div className="border-warning-border bg-warning/10 rounded-md border p-3 text-sm">
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
                <code className="flex-1 font-mono text-xs break-all">{acceptUrl}</code>
                <Button size="sm" variant="outline" onClick={onCopyAcceptUrl} className="shrink-0">
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
                searchQueryActive={searchActive}
                isLoading={searchLoading}
                memberMatch={memberMatch}
                invitationMatch={invitationMatch}
                onCancelInvitation={onRevokeAndReinvite}
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
                disabled={rolesLoading || noGrantableRoles}
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
              <Button type="submit" disabled={creating || blocksSubmit || noGrantableRoles}>
                <MailIcon className="size-4" /> {t("form.issue")}
              </Button>
            </SheetFooter>
          </form>
        )}
      </SheetContent>
    </Sheet>
  );
}

/** The invite sheet wired to its data: runs useInviteDialog while mounted. */
export function InviteDialog(props: DialogProps) {
  const data = useInviteDialog(props.open);
  return <InviteSheet {...props} {...data} />;
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
              <CheckCircle2Icon className="text-success-fg size-3.5" />
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
              <AlertCircleIcon className="text-warning-fg size-3.5" />
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
