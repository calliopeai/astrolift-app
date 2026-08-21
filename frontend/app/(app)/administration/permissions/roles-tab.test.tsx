import type { ReactNode } from "react";

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RolesTab } from "./roles-tab";

/**
 * The roles table moved onto DataTable (#1243), which means the rows are a
 * page now. The editor's permission checklist is the union of *every*
 * role's permission set, so it must keep reading the flat list: derive it
 * from whichever page is on screen and the catalogue silently shrinks as
 * an operator pages or searches, and a role saved from that sheet loses
 * the permissions the page did not happen to contain.
 *
 * That is the failure this migration could introduce and it is invisible
 * — a shorter checklist looks like a shorter checklist. So it is pinned
 * with a page that deliberately does not contain the role holding the
 * full enum.
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
const OWNER = role("r-owner", "Org Owner", ["app.create", "app.delete", "org.manage_members"], true);
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
          { astroliftRolesPage: { items: [VIEWER], nextCursor: "c1", totalCount: 2 } }
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

vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, loading: false }),
}));

describe("RolesTab", () => {
  it("renders the page's rows and not the whole list", () => {
    render(<RolesTab />);
    expect(screen.getByText("Viewer")).toBeInTheDocument();
    expect(screen.queryByText("Org Owner")).not.toBeInTheDocument();
  });

  it("builds the permission catalogue from the flat list, not from the page", async () => {
    render(<RolesTab />);

    // Open the editor for the row that is on the page. Its own permission
    // set is one entry; the catalogue it offers must still be all four.
    fireEvent.click(screen.getByRole("button", { name: "Edit role Viewer" }));

    const checklist = await screen.findByText("1 of 4 selected");
    expect(checklist).toBeInTheDocument();
    for (const permission of ["app.create", "app.delete", "org.manage_members", "app.read"]) {
      expect(screen.getAllByText(permission).length).toBeGreaterThan(0);
    }
  });

  it("names the row activator so a keyboard can reach the editor", () => {
    // The row is not a link — it opens a sheet — so its activator is a
    // button, and the button's name is the only thing announced (#1503).
    render(<RolesTab />);
    expect(screen.getByRole("button", { name: "Edit role Viewer" })).toBeInTheDocument();
  });

  it("asks the server to search rather than filtering a page in the browser", () => {
    state.calls.length = 0;
    render(<RolesTab />);
    const page = state.calls.find((c) => c.op === "ListRolesPage");
    expect(page).toBeDefined();
    expect(page?.variables).toHaveProperty("search");
  });
});
