"use client";

import { useQuery } from "@apollo/client/react";
import { CheckIcon, SearchIcon, XIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LIST_TEAMS, ORG_MEMBERS_FOR_APPROVAL_PICKER } from "@/graphql/identity/identity.queries";
import type { AstroliftApproverUser, AstroliftTeam } from "@/graphql/identity/identity.types";
import { cn } from "@/lib/utils";

interface ApproverPickerResponse {
  astroliftOrgMembersForApprovalPicker: AstroliftApproverUser[];
}

interface TeamsResponse {
  astroliftTeams: AstroliftTeam[];
}

export interface ApproverSelection {
  approverUserIds: string[];
  approverTeamId: string;
  minimumApprovals: number;
}

interface ApproverSelectorProps {
  orgSlug: string;
  value: ApproverSelection;
  onChange: (next: ApproverSelection) => void;
  /**
   * Reported up to the wizard step so the Next button stays disabled
   * until the approval-gate inputs are coherent. Mirrors backend
   * validation: when the gate is on, you need either a team or one or
   * more users (XOR) and ``minimumApprovals`` cannot exceed the user
   * count.
   */
  onValidityChange: (valid: boolean) => void;
}

/**
 * Live approval-gate picker (#410). Replaces the disabled inputs +
 * "Coming soon" badge that shipped with the wizard's first cut.
 *
 * UX choices:
 * - User multi-select OR team single-select — mutually exclusive on the
 *   UI side (matching the backend validator). Picking a team clears
 *   any user selection and vice-versa, with a soft warning rather than
 *   an outright error if the operator tries to mix the two.
 * - `minimumApprovals` is a clamp-on-input number field — bounded by 1
 *   on the low side and the user count on the high side when users are
 *   selected. Team-only policies don't carry a count bound (membership
 *   is resolved at approval time) but still enforce >= 1.
 * - Search-as-you-type filter over the user picker for orgs with more
 *   than a couple dozen members. The backend currently caps at the
 *   active org-member set; pagination lands when org rosters routinely
 *   exceed a single picker window.
 */
export function ApproverSelector({
  orgSlug,
  value,
  onChange,
  onValidityChange,
}: ApproverSelectorProps) {
  const t = useTranslations("apps.wizard.approval");
  const usersQuery = useQuery<ApproverPickerResponse>(ORG_MEMBERS_FOR_APPROVAL_PICKER, {
    variables: { orgSlug },
    skip: !orgSlug,
    fetchPolicy: "cache-and-network",
  });
  const teamsQuery = useQuery<TeamsResponse>(LIST_TEAMS, {
    fetchPolicy: "cache-and-network",
  });

  const allUsers = React.useMemo(
    () => usersQuery.data?.astroliftOrgMembersForApprovalPicker ?? [],
    [usersQuery.data]
  );
  // The teams query is install-wide; narrow to the same org as the
  // approver-user picker to keep the policy coherent.
  const orgTeams = React.useMemo(
    () => (teamsQuery.data?.astroliftTeams ?? []).filter((t) => t.organization?.slug === orgSlug),
    [teamsQuery.data, orgSlug]
  );

  const [search, setSearch] = React.useState("");
  const filteredUsers = React.useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return allUsers;
    return allUsers.filter(
      (u) => u.displayName.toLowerCase().includes(q) || u.email.toLowerCase().includes(q)
    );
  }, [allUsers, search]);

  const userById = React.useMemo(() => {
    const map = new Map<string, AstroliftApproverUser>();
    for (const u of allUsers) map.set(u.id, u);
    return map;
  }, [allUsers]);

  const pickedUsers = value.approverUserIds
    .map((id) => userById.get(id))
    .filter((u): u is AstroliftApproverUser => u !== undefined);
  // Selected ids that are no longer in the live picker (member removed,
  // user deactivated) — keep them visible but flagged so the operator
  // can drop them rather than silently shipping a broken policy.
  const orphanIds = value.approverUserIds.filter((id) => !userById.has(id));

  const usersMode = value.approverUserIds.length > 0;
  const teamMode = value.approverTeamId !== "";

  const toggleUser = React.useCallback(
    (userId: string) => {
      const has = value.approverUserIds.includes(userId);
      const nextIds = has
        ? value.approverUserIds.filter((id) => id !== userId)
        : [...value.approverUserIds, userId];
      // Picking a user clears any team selection — XOR.
      const nextTeamId = nextIds.length > 0 ? "" : value.approverTeamId;
      const maxAllowed = Math.max(1, nextIds.length || 1);
      onChange({
        approverUserIds: nextIds,
        approverTeamId: nextTeamId,
        minimumApprovals: Math.min(value.minimumApprovals, maxAllowed),
      });
    },
    [onChange, value]
  );

  const pickTeam = React.useCallback(
    (teamId: string) => {
      const nextTeamId = teamId === "__none__" ? "" : teamId;
      // Picking a team clears the user picker — XOR.
      const nextUserIds = nextTeamId ? [] : value.approverUserIds;
      onChange({
        approverUserIds: nextUserIds,
        approverTeamId: nextTeamId,
        minimumApprovals: Math.max(1, value.minimumApprovals),
      });
    },
    [onChange, value]
  );

  const onMinimumChange = React.useCallback(
    (raw: string) => {
      const parsed = parseInt(raw, 10);
      if (Number.isNaN(parsed)) {
        onChange({ ...value, minimumApprovals: 1 });
        return;
      }
      // Clamp: at least 1, at most the user count when users are picked.
      const upper = usersMode ? Math.max(1, value.approverUserIds.length) : 99;
      const next = Math.min(upper, Math.max(1, parsed));
      onChange({ ...value, minimumApprovals: next });
    },
    [onChange, usersMode, value]
  );

  const validityMessage = React.useMemo(() => {
    if (!usersMode && !teamMode) {
      return t("errorPickAny");
    }
    if (usersMode && teamMode) {
      // Shouldn't occur because the togglers enforce XOR, but the
      // backend validator would reject it so we surface the hint.
      return t("errorBoth");
    }
    if (usersMode && value.minimumApprovals > value.approverUserIds.length) {
      return t("errorMinTooHigh");
    }
    return null;
  }, [usersMode, teamMode, value, t]);

  React.useEffect(() => {
    onValidityChange(validityMessage === null);
  }, [validityMessage, onValidityChange]);

  const usersLoading = usersQuery.loading && allUsers.length === 0;
  const usersError = usersQuery.error?.message ?? null;

  return (
    <div className="flex flex-col gap-4 pl-7">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label>{t("teamLabel")}</Label>
          <Select value={value.approverTeamId || "__none__"} onValueChange={pickTeam}>
            <SelectTrigger>
              <SelectValue placeholder={t("teamPlaceholder")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">{t("teamNoneOption")}</SelectItem>
              {orgTeams.map((team) => (
                <SelectItem key={team.id} value={team.id}>
                  <span className="font-mono text-xs">{team.slug}</span>{" "}
                  <span className="text-muted-foreground">— {team.name}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-muted-foreground text-xs">{t("teamHint")}</p>
        </div>

        <div className="space-y-2">
          <Label htmlFor="minimum-approvals">{t("minimumLabel")}</Label>
          <Input
            id="minimum-approvals"
            type="number"
            min={1}
            max={usersMode ? Math.max(1, value.approverUserIds.length) : 99}
            value={value.minimumApprovals}
            onChange={(e) => onMinimumChange(e.target.value)}
            className="font-mono text-xs"
          />
          <p className="text-muted-foreground text-xs">
            {usersMode
              ? t("minimumHintUsers", { max: Math.max(1, value.approverUserIds.length) })
              : t("minimumHintTeam")}
          </p>
        </div>
      </div>

      <div className="space-y-2">
        <Label>{t("usersLabel")}</Label>
        <div className="flex flex-wrap items-center gap-1 rounded-md border p-2">
          {pickedUsers.length === 0 && orphanIds.length === 0 && (
            <span className="text-muted-foreground px-1 text-xs">
              {teamMode ? t("usersPlaceholderTeam") : t("usersPlaceholderEmpty")}
            </span>
          )}
          {pickedUsers.map((u) => (
            <Badge key={u.id} variant="secondary" className="flex items-center gap-1 pr-1 text-xs">
              <Avatar size="sm">
                {u.avatarUrl ? <AvatarImage src={u.avatarUrl} alt={u.displayName} /> : null}
                <AvatarFallback>{initials(u.displayName || u.email)}</AvatarFallback>
              </Avatar>
              <span className="truncate">{u.displayName || u.email}</span>
              <button
                type="button"
                onClick={() => toggleUser(u.id)}
                className="hover:bg-muted ml-1 rounded-sm p-0.5"
                aria-label={t("userRemove", { label: u.displayName || u.email })}
              >
                <XIcon className="size-3" />
              </button>
            </Badge>
          ))}
          {orphanIds.map((id) => (
            <Badge key={id} variant="outline" className="flex items-center gap-1 pr-1 text-xs">
              <span className="font-mono">user {id}</span>
              <span className="text-muted-foreground text-2xs">{t("orphanSuffix")}</span>
              <button
                type="button"
                onClick={() => toggleUser(id)}
                className="hover:bg-muted ml-1 rounded-sm p-0.5"
                aria-label={t("orphanRemove", { id })}
              >
                <XIcon className="size-3" />
              </button>
            </Badge>
          ))}
        </div>

        {!teamMode && (
          <div className="rounded-md border">
            <div className="flex items-center gap-2 border-b px-2 py-1">
              <SearchIcon className="text-muted-foreground size-3.5" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t("search")}
                className="placeholder:text-muted-foreground w-full bg-transparent text-xs outline-none"
              />
            </div>
            <div className="max-h-48 overflow-y-auto">
              {usersLoading && <p className="text-muted-foreground p-3 text-xs">{t("loading")}</p>}
              {usersError && <p className="text-destructive p-3 text-xs">{usersError}</p>}
              {!usersLoading && filteredUsers.length === 0 && (
                <p className="text-muted-foreground p-3 text-xs">
                  {allUsers.length === 0 ? t("noMembers") : t("noMatch")}
                </p>
              )}
              {filteredUsers.map((u) => {
                const selected = value.approverUserIds.includes(u.id);
                return (
                  <button
                    key={u.id}
                    type="button"
                    onClick={() => toggleUser(u.id)}
                    className={cn(
                      "hover:bg-muted/50 flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs",
                      selected && "bg-muted/30"
                    )}
                  >
                    <Avatar size="sm">
                      {u.avatarUrl ? <AvatarImage src={u.avatarUrl} alt={u.displayName} /> : null}
                      <AvatarFallback>{initials(u.displayName || u.email)}</AvatarFallback>
                    </Avatar>
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{u.displayName || u.email}</p>
                      <p className="text-muted-foreground truncate text-2xs">{u.email}</p>
                    </div>
                    {selected && <CheckIcon className="text-primary size-3.5" />}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {validityMessage && <p className="text-destructive text-xs">{validityMessage}</p>}
    </div>
  );
}

function initials(label: string): string {
  const cleaned = label.trim();
  if (!cleaned) return "?";
  const parts = cleaned.split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
