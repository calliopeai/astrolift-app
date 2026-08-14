import type { ReactNode } from "react";

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NavTree } from "./NavTree";

const route = vi.hoisted(() => ({ pathname: "/projects/emr-bug-triage" }));

const agent = (id: string, slug: string) => ({
  id,
  slug: `${slug}-registration`,
  name: slug,
  status: "ready" as const,
  primitiveKind: "agent",
  primitiveSlug: slug,
});

const tree = {
  organization: { id: "org-1", slug: "steadymd", name: "SteadyMD" },
  teams: [
    {
      team: { id: "team-1", slug: "engineering", name: "Engineering" },
      projects: [
        {
          project: { id: "project-1", slug: "emr-bug-triage", name: "EMR Bug Triage" },
          apps: [],
          standaloneAgents: [],
          workflows: [
            {
              id: "workflow-intake",
              slug: "emr-triage",
              name: "EMR bug-report triage",
              isEnabled: true,
              childWorkflowIds: [],
              agents: [agent("agent-intake", "emr-triage-intake")],
            },
            {
              id: "workflow-patch",
              slug: "emr-triage-patch",
              name: "EMR triage — code research and review",
              isEnabled: true,
              childWorkflowIds: [],
              agents: [agent("agent-research", "emr-triage-research")],
            },
            {
              id: "workflow-full",
              slug: "emr-triage-full",
              name: "EMR triage — full path",
              isEnabled: true,
              childWorkflowIds: ["workflow-intake", "workflow-patch"],
              agents: [],
            },
          ],
        },
      ],
      unassignedApps: [],
    },
  ],
  unassignedApps: [],
};

vi.mock("@apollo/client/react", () => ({
  useQuery: () => ({ data: { astroliftNavTree: tree }, loading: false, error: undefined }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => route.pathname,
}));

vi.mock("@/graphql/user/user.hooks", () => ({
  useModules: () => ({ loading: false, canView: () => true }),
}));

vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true }),
}));

vi.mock("@/components/ui/collapsible", () => ({
  Collapsible: ({ children }: { children: ReactNode }) => <>{children}</>,
  CollapsibleContent: ({ children }: { children: ReactNode }) => <>{children}</>,
  CollapsibleTrigger: ({ children, ...props }: { children: ReactNode }) => (
    <button {...props}>{children}</button>
  ),
}));

vi.mock("@/components/ui/sidebar", () => {
  const Div = ({ children }: { children: ReactNode }) => <div>{children}</div>;
  const List = ({ children }: { children: ReactNode }) => <ul>{children}</ul>;
  const Item = ({ children }: { children: ReactNode }) => <li>{children}</li>;
  return {
    SidebarGroup: Div,
    SidebarGroupLabel: Div,
    SidebarMenu: List,
    SidebarMenuButton: Div,
    SidebarMenuItem: Item,
    SidebarMenuSub: List,
    SidebarMenuSubButton: Div,
    SidebarMenuSubItem: Item,
  };
});

describe("NavTree nested workflows", () => {
  beforeEach(() => {
    route.pathname = "/projects/emr-bug-triage";
    window.localStorage.clear();
  });

  it("renders referenced workflows under their parent instead of as peer roots", () => {
    render(<NavTree />);

    const fullPath = screen.getByRole("link", { name: "EMR triage — full path" });
    const fullPathItem = fullPath.closest("li");
    expect(fullPathItem).not.toBeNull();
    expect(
      within(fullPathItem as HTMLElement).getByRole("link", { name: "EMR bug-report triage" })
    ).toBeInTheDocument();
    expect(
      within(fullPathItem as HTMLElement).getByRole("link", {
        name: "EMR triage — code research and review",
      })
    ).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "EMR bug-report triage" })).toHaveLength(1);
    expect(
      screen.getAllByRole("link", { name: "EMR triage — code research and review" })
    ).toHaveLength(1);
  });

  it("opens the parent chain for an active nested agent", async () => {
    route.pathname = "/agents/emr-triage-research";
    render(<NavTree />);

    expect(
      await screen.findByRole("button", { name: "Toggle EMR triage — full path" })
    ).toBeVisible();
    expect(window.localStorage.getItem("astrolift.nav.tree.open.v1")).toContain(
      '"workflow:workflow-full":true'
    );
  });
});
