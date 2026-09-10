import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MemberRolesPanel } from "./member-roles-panel";

const binding = (
  id: string,
  role: string,
  scopeKind: string,
  extra: Record<string, unknown> = {}
) =>
  ({
    id,
    role: { id: `role-${id}`, name: role },
    scopeKind,
    scopeId: null,
    sourceScopeLabel: `${scopeKind.toLowerCase()} label`,
    inherits: false,
    grantedAt: "2026-09-01T00:00:00Z",
    ...extra,
  }) as never;

const BINDINGS = [
  binding("1", "app_admin", "APP"),
  binding("2", "team_developer", "TEAM"),
  binding("3", "org_viewer", "ORG"),
  binding("4", "project_admin", "PROJECT", { inherits: true }),
  binding("5", "app_admin", "APP"),
];

describe("MemberRolesPanel", () => {
  it("summarises what the member holds before the list", () => {
    render(<MemberRolesPanel bindings={BINDINGS} loading={false} />);

    const summary = screen.getByText(/across 5 bindings/);
    expect(summary).toHaveTextContent("4 roles");
    expect(summary).toHaveTextContent("1 inherited");
    expect(summary).toHaveTextContent("2 at app scope");
  });

  it("groups by scope, widest first", () => {
    render(<MemberRolesPanel bindings={BINDINGS} loading={false} />);

    const headings = screen
      .getAllByRole("button")
      .map((b) => b.textContent ?? "")
      .filter((t) => /Organization|Team|Project|App/.test(t));

    expect(headings.map((t) => t.replace(/\d+/g, "").trim())).toEqual([
      "Organization",
      "Team",
      "Project",
      "App",
    ]);
  });

  it("a group can be collapsed on its own", () => {
    render(<MemberRolesPanel bindings={BINDINGS} loading={false} />);
    expect(screen.getByText("org_viewer")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Organization/ }));

    expect(screen.queryByText("org_viewer")).not.toBeInTheDocument();
    // The others are untouched.
    expect(screen.getByText("team_developer")).toBeInTheDocument();
  });

  it("filters by role name", () => {
    render(<MemberRolesPanel bindings={BINDINGS} loading={false} />);

    fireEvent.change(screen.getByLabelText("Filter roles"), { target: { value: "team_dev" } });

    expect(screen.getByText("team_developer")).toBeInTheDocument();
    expect(screen.queryByText("org_viewer")).not.toBeInTheDocument();
  });

  it("filters by scope too", () => {
    render(<MemberRolesPanel bindings={BINDINGS} loading={false} />);

    fireEvent.change(screen.getByLabelText("Filter roles"), { target: { value: "PROJECT" } });

    expect(screen.getByText("project_admin")).toBeInTheDocument();
    expect(screen.queryByText("app_admin")).not.toBeInTheDocument();
  });

  it("the summary counts what they hold, not what the filter shows", () => {
    render(<MemberRolesPanel bindings={BINDINGS} loading={false} />);

    fireEvent.change(screen.getByLabelText("Filter roles"), { target: { value: "team_dev" } });

    expect(screen.getByText(/across 5 bindings/)).toBeInTheDocument();
  });

  it("says so when a filter matches nothing", () => {
    render(<MemberRolesPanel bindings={BINDINGS} loading={false} />);

    fireEvent.change(screen.getByLabelText("Filter roles"), { target: { value: "zzz" } });

    expect(screen.getByText(/No role matches/)).toBeInTheDocument();
  });

  it("renders the empty state for a member with no bindings", () => {
    render(<MemberRolesPanel bindings={[]} loading={false} />);

    expect(screen.getByText("No role bindings")).toBeInTheDocument();
    expect(screen.queryByLabelText("Filter roles")).not.toBeInTheDocument();
  });

  it("keeps the inherited marker on the row it belongs to", () => {
    render(<MemberRolesPanel bindings={BINDINGS} loading={false} />);

    const row = screen.getByText("project_admin").closest("li");
    expect(within(row as HTMLElement).getByText("inherited")).toBeInTheDocument();
  });
});
