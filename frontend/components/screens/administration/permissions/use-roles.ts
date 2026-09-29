"use client";

import type { CursorPage } from "@/components/data-table";
import { useListState } from "@/components/list/use-list-state";
import { LIST_ROLES_PAGE } from "@/graphql/identity/identity.queries";
import type { AstroliftRole } from "@/graphql/identity/identity.types";

import { useListPageQuery } from "../access/use-list-page-query";
import { ROLES_LIST } from "./permissions-lists";

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
  // `astroliftRolesPage` searches slug, name and description. The document
  // sends no sort argument, so no column declares a `sortKey`.
  const list = useListState(ROLES_LIST);
  const page = useListPageQuery<AstroliftRole>(
    LIST_ROLES_PAGE,
    list,
    (d) => (d as RolesPageResp | undefined)?.astroliftRolesPage
  );
  return { list, page };
}
