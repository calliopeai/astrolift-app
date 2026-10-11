import type { DetailTab } from "@/components/DetailPageTabs";
import { groupParam } from "@/components/screens/members/people-model";

import { PEOPLE_HREF, TEAMS_HREF } from "./access-nav";

/**
 * The tabs of a person's, a group's and a team's page (access UX design 3.2).
 * Each tab is its own route (spec 44 §5.2), so only the active one mounts
 * and fetches, and a list on it keeps its own `?view=&q=&page=` in the URL.
 * Pure.
 */
export type PersonTab = "access" | "teams" | "activity";
export type GroupTab = "access" | "members";
export type TeamTab = "access" | "members";

/** `/administration/access/people/<param>`: a member id, or `group:<external id>`. */
export type PrincipalParam =
  | { kind: "user"; memberId: string }
  | { kind: "group"; externalId: string };

export function parsePrincipalParam(raw: string): PrincipalParam {
  let value = raw;
  try {
    value = decodeURIComponent(raw);
  } catch {
    // A malformed escape: use it as it came.
  }
  return value.startsWith("group:")
    ? { kind: "group", externalId: value.slice("group:".length) }
    : { kind: "user", memberId: value };
}

function tabRow<K extends string>(
  base: string,
  tabs: ReadonlyArray<[K, string, string]>,
  active: K
): DetailTab[] {
  return tabs.map(([key, label, segment]) => ({
    key,
    label,
    href: segment ? `${base}/${segment}` : base,
    active: key === active,
  }));
}

export function personTabs(memberId: string, active: PersonTab): DetailTab[] {
  return tabRow(
    `${PEOPLE_HREF}/${memberId}`,
    [
      ["access", "Access", ""],
      ["teams", "Teams", "teams"],
      ["activity", "Activity", "activity"],
    ],
    active
  );
}

export function groupTabs(externalId: string, active: GroupTab): DetailTab[] {
  return tabRow(
    `${PEOPLE_HREF}/${encodeURIComponent(groupParam(externalId))}`,
    [
      ["access", "Access", ""],
      ["members", "Members", "members"],
    ],
    active
  );
}

export function teamTabs(slug: string, active: TeamTab): DetailTab[] {
  return tabRow(
    `${TEAMS_HREF}/${encodeURIComponent(slug)}`,
    [
      ["access", "Access", "access"],
      ["members", "Members", "members"],
    ],
    active
  );
}
