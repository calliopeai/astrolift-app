import type { ReactNode } from "react";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RolesTab } from "./roles-tab";

/**
 * The roles table is a page of `astroliftRolesPage` (#1243). Editing moved
 * off the list to each role's own page (design 3.5), and with it the flat
 * roles list the editor's catalogue is built from: the list fetches only the
 * page it shows (Leo's page rule 2). The catalogue invariant (built from the
 * whole flat list, never from a page) is pinned where the editor now lives,
 * in roles/[id]/role-detail-client.test.tsx.
 */

type Vars = Record<string, unknown>;

const state = vi.hoisted(() => ({ calls: [] as { op: string; variables: Vars }[] }));

function role(id: string, name: string, permissions: string[], isSystem = false) {
  return {
    id,
    slug: name.toLowerCase().replace(/\s+/g, "-"),
    name,
    description: "",
    scopeLevel: "ORG",
    permissions,
    isSystem,
  };
}

// `org_owner` carries the full Permission enum and is the reason the union
// over the whole list is the complete catalogue. It is kept off the page
// on purpose.
const OWNER = role(
  "r-owner",
  "Org Owner",
  ["app.create", "app.delete", "org.manage_members"],
  true
);
const VIEWER = role("r-viewer", "Viewer", ["app.read"]);

vi.mock("@apollo/client/react", () => ({
  useQuery: (
    doc: { definitions?: { kind: string; name?: { value: string } }[] },
    options?: { variables?: Vars }
  ) => {
    const op = doc.definitions?.find((d) => d.kind === "OperationDefinition")?.name?.value ?? "";
    state.calls.push({ op, variables: options?.variables ?? {} });
    const data =
      op === "ListRolesPage"
        ? // One row only: the page the operator is looking at.
          {
            astroliftRolesPage: {
              items: [VIEWER],
              nextCursor: null,
              totalCount: 2,
              page: 1,
              pageSize: 25,
            },
          }
        : { astroliftRoles: [OWNER, VIEWER] };
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

// The roles list keeps its search and page in the URL (spec 44 §5.1); this
// run has no app router to hold it.
vi.mock("next/navigation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("next/navigation")>()),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, loading: false }),
}));

describe("RolesTab", () => {
  it("renders the page's rows and not the whole list", () => {
    render(<RolesTab />);
    expect(screen.getByText("Viewer")).toBeInTheDocument();
    expect(screen.queryByText("Org Owner")).not.toBeInTheDocument();
  });

  it("does not fetch the flat roles list it no longer shows", () => {
    state.calls.length = 0;
    render(<RolesTab />);
    expect(state.calls.some((c) => c.op === "ListRoles")).toBe(false);
  });

  it("makes each row a link to the role's page, named by the role", () => {
    // A row opens the role's page rather than a sheet, so its activator is
    // a real link and its name is what a keyboard announces (#1503).
    render(<RolesTab />);
    expect(screen.getByRole("link", { name: /Viewer/ })).toHaveAttribute(
      "href",
      "/administration/permissions/roles/r-viewer"
    );
  });

  it("asks the server to search, filter, sort and number the page", () => {
    state.calls.length = 0;
    render(<RolesTab />);
    const page = state.calls.find((c) => c.op === "ListRolesPage");
    expect(page).toBeDefined();
    expect(page?.variables).toEqual({
      search: null,
      filter: null,
      sort: "name",
      page: 1,
      pageSize: 25,
    });
  });
});
