"use client";

import type { CursorPage } from "@/components/data-table";
import { useListState } from "@/components/list/use-list-state";
import { LIST_ROLES_PAGE } from "@/graphql/identity/identity.queries";
import type { AstroliftRole } from "@/graphql/identity/identity.types";

import { useNumberedListQuery } from "../access/use-list-page-query";
import { ROLES_LIST, rolesFilter } from "./permissions-lists";

interface RolesPageResp {
  astroliftRolesPage: CursorPage<AstroliftRole>;
}

/**
 * The data half of RolesView: one page of roles, with its search and cursor
 * in the URL. Editing moved to the role's page, and with it the flat roles
 * list the editor's catalog is built from (use-role-detail.ts), so this list
 * fetches only what it shows.
 */
export function useRoles() {
  // `astroliftRolesPage` searches slug, name and description, and filters,
  // sorts and numbers the pages (#2153).
  const list = useListState(ROLES_LIST);
  const page = useNumberedListQuery<AstroliftRole, ReturnType<typeof rolesFilter>>(
    LIST_ROLES_PAGE,
    list,
    (d) => (d as RolesPageResp | undefined)?.astroliftRolesPage,
    { toFilter: rolesFilter }
  );
  return { list, page };
}
