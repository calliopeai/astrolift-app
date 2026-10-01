"use client";

import * as React from "react";
import { useTranslations } from "next-intl";

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
import type { AstroliftRole, ScopeKind } from "@/graphql/identity/identity.types";

import { useGrantRole } from "./use-grant-role";

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  roles: AstroliftRole[];
  rolesLoading?: boolean;
  rolesKnown?: boolean;
  rolesError?: { message: string } | null;
  onRetryRoles?: () => Promise<void>;
  // Deep-link grant (#417): pre-populate the user PK so the operator
  // skips the picker step when the dialog opens from a per-row action
  // on the members table. The label, when provided, surfaces as a
  // muted help line next to the locked user input.
  initialUserId?: string | null;
  initialUserLabel?: string | null;
}

export type GrantRoleSheetProps = DialogProps & ReturnType<typeof useGrantRole>;

/** Grant a role to a user on a chosen scope. Pure view; data comes from useGrantRole. */
export function GrantRoleSheet({
  open,
  onOpenChange,
  roles,
  rolesLoading = false,
  rolesKnown = true,
  rolesError = null,
  onRetryRoles,
  initialUserId = null,
  initialUserLabel = null,
  org,
  teams,
  projects,
  granting,
  organizationState,
  scopeReads,
  onRetryScope,
  onGrant,
}: GrantRoleSheetProps) {
  const t = useTranslations("shared.access.legacyGrantRole");
  const [userId, setUserId] = React.useState(initialUserId ?? "");
  const [roleId, setRoleId] = React.useState("");
  const [scopeKind, setScopeKind] = React.useState<ScopeKind>("ORG");
  const [scopeGuid, setScopeGuid] = React.useState("");

  const selectedRole = roles.find((r) => r.id === roleId);
  // Constrain scope to match role.scope_level when a role is picked.
  React.useEffect(() => {
    if (selectedRole) {
      setScopeKind(selectedRole.scopeLevel);
      setScopeGuid("");
    }
  }, [selectedRole]);

  // When the dialog opens for a specific member (deep-link), prefill
  // the user PK. When the dialog closes, reset everything so the next
  // open starts fresh.
  React.useEffect(() => {
    if (open) {
      setUserId(initialUserId ?? "");
    } else {
      setUserId("");
      setRoleId("");
      setScopeKind("ORG");
      setScopeGuid("");
    }
  }, [open, initialUserId]);

  const scopeOptions = React.useMemo(() => {
    if (scopeKind === "ORG") {
      return org ? [{ id: org.id, label: `${org.name} (${org.slug})` }] : [];
    }
    if (scopeKind === "TEAM") {
      return (teams ?? []).map((t) => ({
        id: t.id,
        label: `${t.organization.slug}/${t.slug}`,
      }));
    }
    if (scopeKind === "PROJECT") {
      return (projects ?? []).map((p) => ({
        id: p.id,
        label: `${p.team.slug}/${p.slug}`,
      }));
    }
    return [];
  }, [scopeKind, org, teams, projects]);

  // Auto-pick the first scope when options arrive.
  React.useEffect(() => {
    if (!scopeGuid && scopeOptions.length > 0) {
      setScopeGuid(scopeOptions[0].id);
    }
  }, [scopeOptions, scopeGuid]);

  const read = scopeKind === "TEAM" || scopeKind === "PROJECT" ? scopeReads[scopeKind] : null;
  const blocked =
    rolesLoading ||
    !rolesKnown ||
    Boolean(rolesError) ||
    !org ||
    organizationState.loading ||
    Boolean(organizationState.error) ||
    Boolean(read && (read.loading || read.error || !read.known));
  const currentSelection =
    selectedRole?.scopeLevel === scopeKind &&
    scopeOptions.some((option) => option.id === scopeGuid);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (blocked || !currentSelection || !userId) return;
    if (await onGrant({ userId, roleId, scopeKind, scopeGuid })) {
      onOpenChange(false);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>{t("title")}</SheetTitle>
          <SheetDescription>{t("description")}</SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="userId">{t("userId")}</Label>
            <Input
              id="userId"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              placeholder="2"
              required
              type="number"
              readOnly={initialUserId != null}
            />
            <p className="text-muted-foreground text-xs">
              {initialUserLabel ? t("targetUser", { name: initialUserLabel }) : t("userGuidance")}
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="role">{t("role")}</Label>
            <Select
              value={roleId}
              onValueChange={setRoleId}
              disabled={rolesLoading || !rolesKnown || Boolean(rolesError)}
            >
              <SelectTrigger id="role">
                <SelectValue placeholder={t("selectRole")} />
              </SelectTrigger>
              <SelectContent>
                {roles.map((r) => (
                  <SelectItem key={r.id} value={r.id}>
                    <span className="mr-2 font-mono text-xs">[{r.scopeLevel}]</span>
                    {r.name} <span className="text-muted-foreground">({r.slug})</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {rolesError ? (
            <div role="alert" className="text-destructive text-sm">
              <p>{t("rolesReadFailed")}</p>
              <p className="[overflow-wrap:anywhere]">{rolesError.message}</p>
              {onRetryRoles ? (
                <Button type="button" variant="outline" onClick={onRetryRoles}>
                  {t("retry")}
                </Button>
              ) : null}
            </div>
          ) : rolesLoading ? (
            <p role="status" className="text-muted-foreground text-sm">
              {t("loadingRoles")}
            </p>
          ) : !rolesKnown ? (
            <p role="status" className="text-muted-foreground text-sm">
              {t("unknownRoles")}
            </p>
          ) : roles.length === 0 ? (
            <p role="status" className="text-muted-foreground text-sm">
              {t("emptyRoles")}
            </p>
          ) : null}

          <div className="space-y-2">
            <Label htmlFor="scope">{t("scope")}</Label>
            <Select
              value={scopeGuid}
              onValueChange={setScopeGuid}
              disabled={blocked || scopeOptions.length === 0}
            >
              <SelectTrigger id="scope">
                <SelectValue
                  placeholder={
                    scopeOptions.length === 0 ? t("noScope", { scopeKind }) : t("selectScope")
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {scopeOptions.map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-xs">
              {t.rich("scopeGuidance", {
                scope: () => <span className="font-mono">{scopeKind}</span>,
              })}
            </p>
          </div>

          {organizationState.error ? (
            <div role="alert" className="text-destructive text-sm">
              <p>{t("orgReadFailed")}</p>
              <p className="[overflow-wrap:anywhere]">{organizationState.error.message}</p>
            </div>
          ) : organizationState.loading ? (
            <p role="status" className="text-muted-foreground text-sm">
              {t("loadingOrg")}
            </p>
          ) : !org ? (
            <p role="status" className="text-muted-foreground text-sm">
              {t("noOrg")}
            </p>
          ) : scopeKind === "APP" ? (
            <p role="status" className="text-muted-foreground text-sm">
              {t("unsupportedApp")}
            </p>
          ) : read?.error ? (
            <div role="alert" className="text-destructive text-sm">
              <p>{t("scopeReadFailed")}</p>
              <p className="[overflow-wrap:anywhere]">{read.error.message}</p>
              <Button type="button" variant="outline" onClick={() => onRetryScope(scopeKind)}>
                {t("retry")}
              </Button>
            </div>
          ) : read?.loading ? (
            <p role="status" className="text-muted-foreground text-sm">
              {t("loadingScopes")}
            </p>
          ) : read && !read.known ? (
            <p role="status" className="text-muted-foreground text-sm">
              {t("unknownScopes")}
            </p>
          ) : null}

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t("cancel")}
            </Button>
            <Button type="submit" disabled={granting || blocked || !currentSelection || !userId}>
              {granting ? t("granting") : t("title")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

/** The grant-role sheet wired to its data: runs useGrantRole while mounted. */
export function GrantRoleDialog(props: DialogProps) {
  const data = useGrantRole();
  return <GrantRoleSheet {...props} {...data} />;
}
