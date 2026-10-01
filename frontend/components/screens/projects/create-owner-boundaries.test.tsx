import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import messages from "@/messages/en.json";

import { CreateTeamSheet } from "../teams/CreateTeamSheet";
import { CreateProjectSheet } from "./CreateProjectSheet";
import { TEAMS } from "./projects-teams-dialogs.fixtures";
import { isValidSlug, slugify } from "./project-team-slug";

const Context = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);

describe("backend-compatible default slugs", () => {
  it.each(["123", "2026 Team", "x".repeat(100), `${"a".repeat(39)} - trailing`, " API / Gateway "])(
    "creates a valid default for %s",
    (name) => {
      expect(isValidSlug(slugify(name))).toBe(true);
      expect(slugify(name).length).toBeLessThanOrEqual(40);
    }
  );
  it.each(["", "!!!", "   "])("does not invent a slug for %s", (name) =>
    expect(slugify(name)).toBe("")
  );
});

describe("create sheet admission", () => {
  it.each(["1start", "trailing-", "UPPERCASE", "x".repeat(41)])(
    "refuses invalid project slug %s even without browser validation",
    (slug) => {
      const createProject = vi.fn().mockResolvedValue(true);
      render(
        <CreateProjectSheet
          open
          onOpenChange={vi.fn()}
          teams={TEAMS}
          creating={false}
          createProject={createProject}
        />
      );
      fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Demo" } });
      fireEvent.change(screen.getByLabelText("Slug"), { target: { value: slug } });
      fireEvent.submit(screen.getByLabelText("Slug").closest("form")!);
      expect(createProject).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Create project" })).toBeDisabled();
    }
  );
  it("refuses an invalid team slug even without browser validation", () => {
    const createTeam = vi.fn().mockResolvedValue(true);
    render(
      <CreateTeamSheet
        open
        onOpenChange={vi.fn()}
        hasOrg
        creating={false}
        createTeam={createTeam}
      />,
      { wrapper: Context }
    );
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Demo" } });
    fireEvent.change(screen.getByLabelText("Slug"), { target: { value: "1team" } });
    fireEvent.submit(screen.getByLabelText("Slug").closest("form")!);
    expect(createTeam).not.toHaveBeenCalled();
  });
  it("selects the requested visible team when it arrives and resets the choice on reopen", async () => {
    const createProject = vi.fn().mockResolvedValue(false);
    const props = {
      open: true,
      onOpenChange: vi.fn(),
      teams: [],
      creating: false,
      createProject,
      initialTeamSlug: TEAMS[1].slug,
    };
    const view = render(<CreateProjectSheet {...props} />);
    view.rerender(<CreateProjectSheet {...props} teams={TEAMS} />);
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Demo" } });
    fireEvent.submit(screen.getByLabelText("Slug").closest("form")!);
    await waitFor(() =>
      expect(createProject).toHaveBeenCalledWith(expect.objectContaining({ teamId: TEAMS[1].id }))
    );
    view.rerender(<CreateProjectSheet {...props} teams={TEAMS} open={false} />);
    view.rerender(<CreateProjectSheet {...props} teams={TEAMS} initialTeamSlug={TEAMS[2].slug} />);
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "New owner" } });
    fireEvent.submit(screen.getByLabelText("Slug").closest("form")!);
    await waitFor(() =>
      expect(createProject).toHaveBeenLastCalledWith(
        expect.objectContaining({ teamId: TEAMS[2].id })
      )
    );
  });
  it("does not silently substitute another team for an unavailable requested team", () => {
    const createProject = vi.fn();
    render(
      <CreateProjectSheet
        open
        onOpenChange={vi.fn()}
        teams={TEAMS}
        initialTeamSlug="hidden-team"
        creating={false}
        createProject={createProject}
      />
    );
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Demo" } });
    fireEvent.submit(screen.getByLabelText("Slug").closest("form")!);
    expect(createProject).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Create project" })).toBeDisabled();
  });
});
