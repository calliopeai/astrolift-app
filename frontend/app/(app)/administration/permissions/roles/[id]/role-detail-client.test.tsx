import type { ReactNode } from "react";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ASTROLIFT_PERMISSIONS } from "@/lib/permissions/permissions.generated";

import { RoleDetailClient } from "./role-detail-client";

/**
 * The editor's catalogue is the union of *every* role's permission set (and
 * the declared catalogue), read from the flat roles list. Derive it from a
 * page of roles and it silently shrinks, and a role saved from the matrix
 * loses the permissions the page did not happen to contain. A shorter
 * catalogue looks like a shorter catalogue, so it is pinned here, with a
 * slug only the owner role holds and the generated list does not know.
 *
 * The Holders tab's bindings query must not run on the other tabs (Leo's
 * page rule 2), so that is pinned too.
 */

type Vars = Record<string, unknown>;

const state = vi.hoisted(() => ({ ops: [] as string[], vars: {} as Record<string, Vars> }));

const role = (id: string, name: string, permissions: string[], isSystem: boolean) => ({
  id,
  slug: name.toLowerCase().replace(/\s+/g, "-"),
  name,
  description: "",
  scopeLevel: "ORG",
  permissions,
  isSystem,
});

const UNLISTED = "legacy.unlisted_permission";
const OWNER = role("r-owner", "Org Owner", ["app.create", "org.manage_members", UNLISTED], true);
const AUDITOR = role("r-auditor", "Auditor", ["app.read"], false);

vi.mock("@apollo/client/react", () => ({
  useQuery: (
    doc: { definitions?: { kind: string; name?: { value: string } }[] },
    options?: { variables?: Vars }
  ) => {
    const op = doc.definitions?.find((d) => d.kind === "OperationDefinition")?.name?.value ?? "";
    state.ops.push(op);
    state.vars[op] = options?.variables ?? {};
    const data =
      op === "ListRoles"
        ? { astroliftRoles: [OWNER, AUDITOR] }
        : op === "GetRole"
          ? {
              astroliftRole: [OWNER, AUDITOR].find((r) => r.id === options?.variables?.id) ?? null,
            }
          : {
              astroliftRoleBindingsPage: {
                items: [],
                nextCursor: null,
                totalCount: 0,
                page: 1,
                pageSize: 25,
              },
            };
    return {
      data,
      previousData: undefined,
      loading: false,
      error: undefined,
      refetch: vi.fn().mockResolvedValue({}),
    };
  },
  useMutation: () => [vi.fn(), { loading: false }],
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("next/navigation")>()),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

vi.mock("@/lib/i18n/formatters", () => ({
  useFormatters: () => ({ formatDate: (iso: string) => iso }),
}));

vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, loading: false }),
}));

describe("RoleDetailClient", () => {
  it("offers the whole catalogue from the flat list, not the role's own set", () => {
    render(<RoleDetailClient id="r-auditor" tab="permissions" />);
    const size = new Set([...ASTROLIFT_PERMISSIONS, UNLISTED]).size;
    expect(screen.getByText(`1 of ${size}`)).toBeInTheDocument();
  });

  it("does not query the holders on the Permissions tab", () => {
    state.ops.length = 0;
    render(<RoleDetailClient id="r-auditor" tab="permissions" />);
    expect(state.ops).not.toContain("ListRoleBindingsPage");
  });

  it("reads the role on its own, so a role past the flat list's cap still opens", () => {
    state.ops.length = 0;
    render(<RoleDetailClient id="r-auditor" tab="permissions" />);
    expect(state.ops).toContain("GetRole");
    expect(state.vars.GetRole).toEqual({ id: "r-auditor" });
  });

  it("queries the holders by the role's id on the Holders tab, a numbered page", () => {
    state.ops.length = 0;
    render(<RoleDetailClient id="r-auditor" tab="holders" />);
    expect(state.ops).toContain("ListRoleBindingsPage");
    expect(state.vars.ListRoleBindingsPage).toEqual({
      roleId: "r-auditor",
      search: null,
      filter: null,
      sort: "-created",
      page: 1,
      pageSize: 25,
    });
  });

  it("says so when the id names no role", () => {
    render(<RoleDetailClient id="r-gone" tab="permissions" />);
    expect(screen.getByText("No role with this id")).toBeInTheDocument();
  });
});
