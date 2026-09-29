"use client";

import * as React from "react";

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
  // Deep-link grant (#417): pre-populate the user PK so the operator
  // skips the picker step when the dialog opens from a per-row action
  // on the members table. The label, when provided, surfaces as a
  // muted help line next to the locked user input.
  initialUserId?: string | null;
  initialUserLabel?: string | null;
}

export type GrantRoleSheetProps = DialogProps & ReturnType<typeof useGrantRole>;

/** Grant a system role to a user on a chosen scope. Pure view; data comes from useGrantRole. */
export function GrantRoleSheet({
  open,
  onOpenChange,
  roles,
  initialUserId = null,
  initialUserLabel = null,
  org,
  teams,
  projects,
  granting,
  onGrant,
}: GrantRoleSheetProps) {
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

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!userId || !roleId || !scopeGuid) return;
    if (await onGrant({ userId, roleId, scopeKind, scopeGuid })) {
      onOpenChange(false);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>Grant role</SheetTitle>
          <SheetDescription>
            Bind a system role to a user on the chosen scope. The user must exist in the platform
            already; SCIM provisioning isn&apos;t wired here.
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="userId">User PK</Label>
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
              {initialUserLabel
                ? `Granting role to ${initialUserLabel}.`
                : "Internal Django user pk. Member-search UI lands when SCIM is wired."}
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="role">Role</Label>
            <Select value={roleId} onValueChange={setRoleId}>
              <SelectTrigger id="role">
                <SelectValue placeholder="Select a role" />
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

          <div className="space-y-2">
            <Label htmlFor="scope">Scope</Label>
            <Select
              value={scopeGuid}
              onValueChange={setScopeGuid}
              disabled={scopeOptions.length === 0}
            >
              <SelectTrigger id="scope">
                <SelectValue
                  placeholder={
                    scopeOptions.length === 0
                      ? `No ${scopeKind.toLowerCase()}s available`
                      : "Select a scope"
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
              Locked to the role&apos;s scope level: <span className="font-mono">{scopeKind}</span>
            </p>
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={granting || !userId || !roleId || !scopeGuid}>
              {granting ? "Granting…" : "Grant role"}
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
