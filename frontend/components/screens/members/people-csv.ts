/** The People list's CSV columns: what an admin pastes into a spreadsheet. Pure. */
import type { CsvColumn } from "@/components/list/exportCsv";

import { type PeopleRow, principalOf } from "./people-model";

export const PEOPLE_CSV: CsvColumn<PeopleRow>[] = [
  { header: "Kind", value: (r) => r.kind },
  { header: "Name", value: (r) => principalOf(r).name },
  {
    header: "Email",
    value: (r) =>
      r.kind === "user" ? r.user.email : r.kind === "invitation" ? r.invitation.email : "",
  },
  {
    header: "Roles",
    value: (r) =>
      r.kind === "invitation"
        ? (r.invitation.roleSlug ?? "")
        : r.kind === "user"
          ? r.bindings.map((b) => `${b.role.slug}@${b.scopeKind}`).join(" ")
          : `${r.bindingsCount} grants, ${r.mappingsCount} mappings`,
  },
  {
    header: "Teams",
    value: (r) => (r.kind === "user" ? r.teams.map((t) => t.slug).join(" ") : ""),
  },
  {
    header: "Status",
    value: (r) =>
      r.kind === "user" ? r.lifecycle : r.kind === "invitation" ? r.invitation.status : "idp group",
  },
  { header: "Last active", value: (r) => (r.kind === "user" ? r.lastActiveAt : null) },
  {
    header: "Joined",
    value: (r) =>
      r.kind === "user" ? r.joinedAt : r.kind === "invitation" ? r.invitation.createdAt : null,
  },
];
