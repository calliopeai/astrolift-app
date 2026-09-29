"use client";

import {
  GroupDetail,
  GroupMembersPanel,
} from "@/components/screens/administration/access/GroupDetail";
import { GroupMappingsPanel } from "@/components/screens/administration/access/GroupMappingsPanel";
import { MemberDetail } from "@/components/screens/administration/access/MemberDetail";
import { PersonActivityPanel } from "@/components/screens/administration/access/PersonActivityPanel";
import { PersonTeamsPanel } from "@/components/screens/administration/access/PersonTeamsPanel";
import { PrincipalAccessPanel } from "@/components/screens/administration/access/PrincipalAccessPanel";
import { parsePrincipalParam } from "@/components/screens/administration/access/principal-tabs";
import { useMemberDetail } from "@/components/screens/administration/access/use-member-detail";
import { usePersonActivity } from "@/components/screens/administration/access/use-person-activity";
import { usePersonTeams } from "@/components/screens/administration/access/use-person-teams";
import { usePrincipalAccess } from "@/components/screens/administration/access/use-principal-access";
import { holderLabel } from "@/components/screens/administration/access/principal-access";
import { useGroupMappings } from "@/components/screens/administration/access/use-group-mappings";

/** The Access tab, for whichever principal the route names. */
export function PrincipalAccessClient({ param }: { param: string }) {
  const who = parsePrincipalParam(param);
  return who.kind === "group" ? (
    <GroupAccessClient externalId={who.externalId} />
  ) : (
    <PersonAccessClient memberId={who.memberId} />
  );
}

function PersonAccessClient({ memberId }: { memberId: string }) {
  const detail = useMemberDetail(memberId);
  const m = detail.member;
  const access = usePrincipalAccess(
    m ? { kind: "user", userId: m.user.id, username: m.user.username } : null
  );
  return (
    <MemberDetail {...detail} tab="access">
      <PrincipalAccessPanel {...access} holderLabel={holderLabel} />
    </MemberDetail>
  );
}

function GroupAccessClient({ externalId }: { externalId: string }) {
  const access = usePrincipalAccess({ kind: "group", externalId });
  const mappings = useGroupMappings(externalId);
  return (
    <GroupDetail externalId={externalId} tab="access">
      <PrincipalAccessPanel {...access} holderLabel={holderLabel} />
      <GroupMappingsPanel {...mappings} />
    </GroupDetail>
  );
}

export function PersonTeamsClient({ memberId }: { memberId: string }) {
  const detail = useMemberDetail(memberId);
  const teams = usePersonTeams(detail.member, detail.memberships);
  return (
    <MemberDetail {...detail} tab="teams">
      <PersonTeamsPanel {...teams} />
    </MemberDetail>
  );
}

export function PersonActivityClient({ memberId }: { memberId: string }) {
  const detail = useMemberDetail(memberId);
  const userId = detail.member?.user.id ?? null;
  const activity = usePersonActivity(userId);
  return (
    <MemberDetail {...detail} tab="activity">
      <PersonActivityPanel
        {...activity}
        auditHref={`/administration/audit${userId ? `?actor=${encodeURIComponent(userId)}` : ""}`}
      />
    </MemberDetail>
  );
}

export function GroupMembersClient({ externalId }: { externalId: string }) {
  return (
    <GroupDetail externalId={externalId} tab="members">
      <GroupMembersPanel externalId={externalId} />
    </GroupDetail>
  );
}
