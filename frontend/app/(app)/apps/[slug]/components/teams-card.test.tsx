import { renderWithIntl as render } from "@/test/render-with-intl";
import type { ReactNode } from "react";

import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TeamsCard } from "./teams-card";

/**
 * The grants table moved onto DataTable (#1243), so its rows are a page
 * now. The Add-Team picker offers the org's teams **minus** the ones that
 * already hold a grant, and it must keep subtracting from the whole list:
 * derive that set from whichever page is on screen and the picker starts
 * offering teams that already have access.
 *
 * That matters more than it sounds. Granting a team that already holds a
 * grant is not a no-op — `grantTeamAccessToApp` is how the level is
 * changed, so re-granting an existing team at the default level silently
 * demotes or promotes it.
 *
 * So the page deliberately keeps two queries, and this pins why.
 */

type Vars = Record<string, unknown>;

const state = vi.hoisted(() => ({ calls: [] as { op: string; variables: Vars }[] }));

function access(id: string, teamId: string, teamName: string, isHome = false) {
  return {
    id,
    teamId,
    teamName,
    teamSlug: teamName.toLowerCase(),
    accessLevel: isHome ? "owner" : "viewer",
    isHome,
  };
}

// On the page the operator is looking at.
const PLATFORM = access("a-1", "t-1", "Platform");
// Holds a grant, but is NOT on the page.
const PAYMENTS = access("a-2", "t-2", "Payments");

vi.mock("@apollo/client/react", () => ({
  useQuery: (
    doc: { definitions?: { kind: string; name?: { value: string } }[] },
    options?: { variables?: Vars }
  ) => {
    const op = doc.definitions?.find((d) => d.kind === "OperationDefinition")?.name?.value ?? "";
    state.calls.push({ op, variables: options?.variables ?? {} });
    let data: unknown = {};
    if (op === "ListAppTeamAccessesPage") {
      data = {
        astroliftAppTeamAccessesPage: { items: [PLATFORM], nextCursor: "c1", totalCount: 2 },
      };
    } else if (op === "ListAppTeamAccesses") {
      data = { astroliftAppTeamAccesses: [PLATFORM, PAYMENTS] };
    } else if (op === "ListTeams") {
      // Exactly the two teams that already hold grants. So the correct
      // candidate set is empty, and a candidate set derived from the page
      // (which holds only Platform) would wrongly contain Payments.
      data = {
        astroliftTeams: [
          { id: "t-1", name: "Platform", slug: "platform" },
          { id: "t-2", name: "Payments", slug: "payments" },
        ],
      };
    }
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

vi.mock("@/components/Can", () => ({
  Can: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));

function renderCard() {
  state.calls.length = 0;
  return render(<TeamsCard appSlug="shop" appId="app-1" homeTeamSlug="platform" />);
}

describe("TeamsCard", () => {
  it("renders the page's grants, not the whole list", () => {
    renderCard();
    expect(screen.getByText("Platform")).toBeInTheDocument();
    expect(screen.queryByText("Payments")).not.toBeInTheDocument();
  });

  it("computes the Add-Team candidates from every grant, not the page", () => {
    // Both of the org's teams already hold a grant, so there is nothing to
    // add and the button says so. Feed the candidate set from the page
    // instead and Payments — granted, but off-page — is offered as new.
    //
    // Re-granting an existing team is not a no-op: grantTeamAccessToApp is
    // how the level is changed, so it would silently reset that team's
    // access to the picker's default.
    renderCard();
    const add = screen.getByRole("button", { name: /add team/i });
    expect(add).toBeDisabled();
    expect(add).toHaveAttribute("title", "Every team in this org already has access.");
  });

  it("still asks for the flat list the candidates are subtracted from", () => {
    renderCard();
    const ops = state.calls.map((c) => c.op);
    expect(ops).toContain("ListAppTeamAccessesPage");
    expect(ops).toContain("ListAppTeamAccesses");
  });

  it("scopes both queries to this app", () => {
    renderCard();
    for (const op of ["ListAppTeamAccessesPage", "ListAppTeamAccesses"]) {
      const call = state.calls.find((c) => c.op === op);
      expect(call?.variables).toMatchObject({ appSlug: "shop" });
    }
  });

  it("asks the server to search rather than filtering a page in the browser", () => {
    renderCard();
    const page = state.calls.find((c) => c.op === "ListAppTeamAccessesPage");
    expect(page?.variables).toHaveProperty("search");
  });
});
