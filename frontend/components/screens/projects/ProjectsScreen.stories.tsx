import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, userEvent, within } from "storybook/test";

import { CreateProjectSheet } from "./CreateProjectSheet";
import { EditProjectSheet } from "./EditProjectSheet";
import { ProjectsScreen } from "./ProjectsScreen";
import {
  LONG_PROJECT,
  PROJECTS,
  createProjectProps,
  editProjectProps,
  projectsProps,
} from "./projects-teams-dialogs.fixtures";

const meta: Meta = {
  title: "Screens/Projects/ProjectsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <ProjectsScreen {...projectsProps()} /> };

export const Loading: Story = {
  render: () => (
    <ProjectsScreen {...projectsProps({ state: "loading", rows: [] }, { teamsLoading: true })} />
  ),
};

export const Empty: Story = {
  render: () => <ProjectsScreen {...projectsProps({ state: "empty", rows: [], totalCount: 0 })} />,
};

/** No teams yet: the empty state points at team management instead. */
export const EmptyNoTeams: Story = {
  render: () => (
    <ProjectsScreen
      {...projectsProps({ state: "empty", rows: [], totalCount: 0 }, { teams: [], noTeams: true })}
    />
  ),
};

export const EmptyFiltered: Story = {
  render: () => (
    <ProjectsScreen
      {...projectsProps({ state: "emptyFiltered", rows: [], isFiltered: true, search: "zzz" })}
    />
  ),
};

export const LoadError: Story = {
  render: () => (
    <ProjectsScreen
      {...projectsProps({
        state: "error",
        rows: [],
        error: new globalThis.Error("upstream timed out"),
      })}
    />
  ),
};

export const Deleting: Story = {
  render: () => <ProjectsScreen {...projectsProps({}, { deleting: true })} />,
};

export const LongStrings: Story = {
  render: () => (
    <ProjectsScreen
      {...projectsProps({ rows: [LONG_PROJECT, ...PROJECTS], totalCount: PROJECTS.length + 1 })}
    />
  ),
};

/** The New project button opens the sheet the caller renders. */
export const OpensCreateSheet: Story = {
  render: () => (
    <ProjectsScreen
      {...projectsProps()}
      renderCreateDialog={(p) => <CreateProjectSheet {...createProjectProps()} {...p} />}
    />
  ),
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: /New project/ }));
    await expect(
      await within(document.body).findByRole("heading", { name: "New project" })
    ).toBeInTheDocument();
  },
};

/** ?new=1 from the NavTree's "Add project" affordance opens the sheet on arrival. */
export const AutoOpenCreate: Story = {
  render: () => (
    <ProjectsScreen
      {...projectsProps({}, { autoOpenCreate: true })}
      renderCreateDialog={(p) => <CreateProjectSheet {...createProjectProps()} {...p} />}
    />
  ),
};

/** A row's edit button opens the edit sheet for that project. */
export const OpensEditSheet: Story = {
  render: () => (
    <ProjectsScreen
      {...projectsProps()}
      renderEditDialog={(p) => (
        <EditProjectSheet
          {...editProjectProps()}
          {...p}
          name={p.project?.name ?? ""}
          slug={p.project?.slug ?? ""}
        />
      )}
    />
  ),
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getAllByRole("button", { name: "Edit" })[0]);
    await expect(
      await within(document.body).findByRole("heading", { name: "Edit project" })
    ).toBeInTheDocument();
  },
};
