import type { MembershipReview, MembershipRow } from "./TeamMembershipPanels";
export const row: MembershipRow = {
  team: {
    id: "019e0000-0000-7000-8000-000000000001",
    version: 1,
    name: "Engineering",
    slug: "engineering",
    canManageMembers: true,
  },
  person: {
    orgMemberId: "019e0000-0000-7000-8000-000000000002",
    name: "Example person",
    email: "person@example.test",
    active: true,
  },
  teamMemberId: "019e0000-0000-7000-8000-000000000003",
  lifecycle: "active",
  canRemove: true,
  sources: [
    {
      id: "019e0000-0000-7000-8000-000000000004",
      roleId: "019e0000-0000-7000-8000-000000000007",
      sourceHref: null,
      source: "DIRECT",
      scopeKind: "TEAM",
      roleName: "Team reader",
      expiresAt: null,
      expired: false,
      removable: true,
    },
    {
      id: "019e0000-0000-7000-8000-000000000005",
      roleId: "019e0000-0000-7000-8000-000000000008",
      sourceHref: null,
      source: "IDP_GROUP",
      scopeKind: "TEAM",
      roleName: "Engineering group",
      expiresAt: null,
      expired: false,
      removable: false,
    },
  ],
};
export const review: MembershipReview = {
  kind: "REMOVE",
  expectedSource: "private-reviewed-source",
  membership: row,
  roles: [],
  remainingSources: row.sources.filter((source) => !source.removable),
};
