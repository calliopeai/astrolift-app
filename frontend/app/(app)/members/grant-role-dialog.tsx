"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

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
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { GRANT_ROLE } from "@/graphql/identity/identity.mutations";
import {
  LIST_MEMBERS,
  LIST_PROJECTS,
  LIST_ROLE_BINDINGS,
  LIST_TEAMS,
} from "@/graphql/identity/identity.queries";
import type {
  AstroliftProject,
  AstroliftRole,
  AstroliftRoleBinding,
  AstroliftTeam,
  MutationResult,
  ScopeKind,
} from "@/graphql/identity/identity.types";

interface Props {
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

export function GrantRoleDialog({
  open,
  onOpenChange,
  roles,
  initialUserId = null,
  initialUserLabel = null,
}: Props) {
  const { org } = useActiveOrg();
  const teams = useQuery<{ astroliftTeams: AstroliftTeam[] }>(LIST_TEAMS);
  const projects = useQuery<{ astroliftProjects: AstroliftProject[] }>(LIST_PROJECTS);

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
      return (teams.data?.astroliftTeams ?? []).map((t) => ({
        id: t.id,
        label: `${t.organization.slug}/${t.slug}`,
      }));
    }
    if (scopeKind === "PROJECT") {
      return (projects.data?.astroliftProjects ?? []).map((p) => ({
        id: p.id,
        label: `${p.team.slug}/${p.slug}`,
      }));
    }
    return [];
  }, [scopeKind, org, teams.data, projects.data]);

  // Auto-pick the first scope when options arrive.
  React.useEffect(() => {
    if (!scopeGuid && scopeOptions.length > 0) {
      setScopeGuid(scopeOptions[0].id);
    }
  }, [scopeOptions, scopeGuid]);

  const [grantRole, { loading }] = useMutation<{
    grantRole: MutationResult<AstroliftRoleBinding>;
  }>(GRANT_ROLE, {
    refetchQueries: [{ query: LIST_ROLE_BINDINGS }, { query: LIST_MEMBERS }],
    awaitRefetchQueries: true,
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!userId || !roleId || !scopeGuid) return;
    const { data } = await grantRole({
      variables: { input: { userId, roleId, scopeKind, scopeGuid } },
    });
    if (data?.grantRole.ok) {
      toast.success("Role granted");
      onOpenChange(false);
    } else {
      toast.error(data?.grantRole.errors?.[0]?.message ?? "Grant failed");
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
            <Button type="submit" disabled={loading || !userId || !roleId || !scopeGuid}>
              {loading ? "Granting…" : "Grant role"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
